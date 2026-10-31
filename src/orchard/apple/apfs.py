"""Read-only APFS: container -> volume -> path lookup -> file bytes (incl. decmpfs)."""
import struct
import zlib

from . import lzfse

OBJ_PHYSICAL = 0x40000000
T_INODE, T_XATTR, T_EXTENT, T_DREC = 3, 4, 8, 9
BTNODE_ROOT, BTNODE_LEAF, BTNODE_FIXED = 1, 2, 4
OID_MASK = (1 << 60) - 1


class Container:
    def __init__(self, source, offset=0):
        self.source, self.offset = source, offset
        head = source.read(offset, 4096)
        if head[32:36] != b'NXSB':
            raise ValueError('No APFS container superblock')
        self.block_size = struct.unpack_from('<I', head, 36)[0]
        sb = self.latest_superblock(head)
        self.xid = struct.unpack_from('<Q', sb, 16)[0]
        self.omap = self.omap_tree(struct.unpack_from('<Q', sb, 160)[0])
        self.volumes = [v for v in struct.unpack_from('<100Q', sb, 184) if v]

    def block(self, paddr):
        return self.source.read(self.offset + paddr * self.block_size, self.block_size)

    def latest_superblock(self, head):
        desc_blocks, _, desc_base = struct.unpack_from('<IIQ', head, 104)
        best = head
        if desc_blocks & 0x80000000:          # non-contiguous checkpoint area: trust block 0
            return best
        for i in range(desc_blocks):
            b = self.block(desc_base + i)
            if b[32:36] == b'NXSB' and struct.unpack_from('<Q', b, 16)[0] > struct.unpack_from('<Q', best, 16)[0]:
                best = b
        return best

    def omap_tree(self, paddr):
        omap = self.block(paddr)
        return struct.unpack_from('<Q', omap, 48)[0]

    def omap_lookup(self, tree, oid, xid):
        """Physical address of virtual oid at the newest xid <= xid."""
        found = None
        for key, value in self.iterate(tree, lambda k: _cmp_omap(k, oid), lambda c: c):
            k_oid, k_xid = struct.unpack_from('<QQ', key)
            if k_oid == oid and k_xid <= xid and (found is None or k_xid > found[0]):
                found = (k_xid, struct.unpack_from('<Q', value, 8)[0])
        if found is None:
            raise KeyError(f'oid {oid} not in object map')
        return found[1]

    def iterate(self, root, compare, resolve):
        """Yield leaf (key, value) whose compare(key) == 0; compare is monotone over sorted keys.

        compare(key) < 0 means key sorts before the wanted range, > 0 after.
        resolve maps an index-node child pointer to a physical block.
        """
        stack = [root]
        while stack:
            node = self.block(stack.pop())
            flags, level, nkeys = struct.unpack_from('<HHI', node, 32)
            toc_off, toc_len = struct.unpack_from('<HH', node, 40)
            keys = 56 + toc_off + toc_len
            vend = self.block_size - (40 if flags & BTNODE_ROOT else 0)
            entries = []
            for i in range(nkeys):
                if flags & BTNODE_FIXED:
                    k_off, v_off = struct.unpack_from('<HH', node, 56 + toc_off + 4 * i)
                    k_len, v_len = 16, (16 if flags & BTNODE_LEAF else 8)
                else:
                    k_off, k_len, v_off, v_len = struct.unpack_from('<HHHH', node, 56 + toc_off + 8 * i)
                key = node[keys + k_off:keys + k_off + k_len]
                value = node[vend - v_off:vend - v_off + v_len] if v_off != 0xFFFF else b''
                entries.append((key, value))
            if flags & BTNODE_LEAF:
                for key, value in entries:
                    if compare(key) == 0:
                        yield key, value
                continue
            children = []
            for i, (key, value) in enumerate(entries):
                if compare(key) > 0:
                    break
                nxt = entries[i + 1][0] if i + 1 < len(entries) else None
                if nxt is not None and compare(nxt) < 0:
                    continue                    # whole child sorts before the range
                children.append(resolve(struct.unpack_from('<Q', value)[0]))
            stack.extend(reversed(children))


def _cmp_omap(key, oid):
    k = struct.unpack_from('<Q', key)[0]
    return (k > oid) - (k < oid)


class Volume:
    def __init__(self, container, index=0):
        self.nx = nx = container
        vaddr = nx.omap_lookup(nx.omap, nx.volumes[index], nx.xid)
        sb = nx.block(vaddr)
        if sb[32:36] != b'APSB':
            raise ValueError('No APFS volume superblock')
        self.xid = struct.unpack_from('<Q', sb, 16)[0]
        self.incompat = struct.unpack_from('<Q', sb, 56)[0]
        self.fs_flags = struct.unpack_from('<Q', sb, 264)[0]
        self.name = sb[704:960].split(b'\0')[0].decode()
        root_type = struct.unpack_from('<I', sb, 116)[0]
        self.omap = nx.omap_tree(struct.unpack_from('<Q', sb, 128)[0])
        root = struct.unpack_from('<Q', sb, 136)[0]
        self.physical = bool(root_type & OBJ_PHYSICAL)
        self.root = root if self.physical else self.resolve(root)
        self.fext = struct.unpack_from('<Q', sb, 1032)[0]
        self.sealed = bool(self.incompat & 0x20)
        self.hashed_names = bool(self.incompat & (1 | 8))
        if not self.fs_flags & 1:
            raise ValueError('Volume is encrypted (APFS_FS_UNENCRYPTED not set)')

    def resolve(self, oid):
        return oid if self.physical else self.nx.omap_lookup(self.omap, oid, self.xid)

    def records(self, oid, kind):
        def compare(key):
            k = struct.unpack_from('<Q', key)[0]
            a, b = (k & OID_MASK, k >> 60), (oid, kind)
            return (a > b) - (a < b)
        return self.nx.iterate(self.root, compare, self.resolve)

    def children(self, parent):
        for key, value in self.records(parent, T_DREC):
            if self.hashed_names:
                n = struct.unpack_from('<I', key, 8)[0] & 0x3FF
                name = key[12:12 + n - 1]
            else:
                n = struct.unpack_from('<H', key, 8)[0]
                name = key[10:10 + n - 1]
            file_id, _, flags = struct.unpack_from('<QQH', value)
            yield name.decode(), file_id, flags & 0xF

    def lookup(self, path):
        oid = 2                                 # root directory
        for part in path.strip('/').split('/'):
            match = [c for c in self.children(oid) if c[0] == part]
            if not match:
                raise FileNotFoundError(path)
            oid = match[0][1]
        return oid

    def inode(self, oid):
        for _, value in self.records(oid, T_INODE):
            private_id = struct.unpack_from('<Q', value, 8)[0]
            bsd_flags = struct.unpack_from('<I', value, 68)[0]
            mode = struct.unpack_from('<H', value, 80)[0]
            size = None
            n, _ = struct.unpack_from('<HH', value, 92)
            data = 96 + 4 * n
            for i in range(n):
                xtype, _, xsize = struct.unpack_from('<BBH', value, 96 + 4 * i)
                if xtype == 8:                  # INO_EXT_TYPE_DSTREAM
                    size = struct.unpack_from('<Q', value, data)[0]
                data += (xsize + 7) & ~7
            return {'private_id': private_id, 'bsd_flags': bsd_flags, 'mode': mode, 'size': size}
        raise KeyError(f'no inode {oid}')

    def xattrs(self, oid):
        out = {}
        for key, value in self.records(oid, T_XATTR):
            n = struct.unpack_from('<H', key, 8)[0]
            name = key[10:10 + n - 1].decode()
            flags, length = struct.unpack_from('<HH', value)
            if flags & 2:                       # embedded
                out[name] = ('embedded', value[4:4 + length])
            else:                               # data stream: xattr_obj_id + j_dstream
                xid, size = struct.unpack_from('<QQ', value, 4)
                out[name] = ('stream', (xid, size))
        return out

    def extents(self, private_id):
        found = []
        for key, value in self.records(private_id, T_EXTENT):
            logical = struct.unpack_from('<Q', key, 8)[0]
            length, block = struct.unpack_from('<QQ', value)
            found.append((logical, length & ((1 << 56) - 1), block))
        if not found and self.fext:             # sealed volumes keep extents in the fext tree
            def compare(key):
                k = struct.unpack_from('<Q', key)[0]
                return (k > private_id) - (k < private_id)
            for key, value in self.nx.iterate(self.fext, compare, lambda c: c):
                logical = struct.unpack_from('<Q', key, 8)[0]
                length, block = struct.unpack_from('<QQ', value)
                found.append((logical, length & ((1 << 56) - 1), block))
        return sorted(found)

    def stream(self, private_id, size):
        out = bytearray()
        bs = self.nx.block_size
        for logical, length, block in self.extents(private_id):
            if logical != len(out):
                out += bytes(logical - len(out))
            if block == 0:
                out += bytes(length)
            else:
                out += self.nx.source.read(self.nx.offset + block * bs, length)
        return bytes(out[:size]) + bytes(max(0, size - len(out)))

    def read(self, path):
        oid = self.lookup(path)
        info = self.inode(oid)
        if not info['bsd_flags'] & 0x20:        # UF_COMPRESSED
            return self.stream(info['private_id'], info['size'] or 0), 'plain'
        attrs = self.xattrs(oid)
        header = attrs['com.apple.decmpfs'][1]
        magic, kind, size = struct.unpack_from('<4sIQ', header)
        if magic != b'fpmc':
            raise ValueError('Bad decmpfs header')
        inline = header[16:]
        if kind == 1:                           # stored, uncompressed, inside the xattr
            return inline[:size], 'decmpfs 1 stored'
        if kind == 9:                           # observed: 0xCC marker byte + raw data; others unseen
            if inline[0] != 0xCC or len(inline) != size + 1:
                raise ValueError('decmpfs type 9 with a compressed payload: not implemented')
            return inline[1:], 'decmpfs 9 stored'
        if kind in (3, 7, 11):                  # payload inside the xattr
            if (kind == 3 and inline[0] & 0x0F == 0x0F) or (kind == 7 and inline[0] == 0x06):
                return inline[1:1 + size], f'decmpfs {kind} stored'
            return _decode(kind, inline, size), f'decmpfs {kind}'
        where, (stream_id, fork_size) = attrs['com.apple.ResourceFork']
        fork = self.stream(stream_id, fork_size)
        return _resource_fork(kind, fork, size), f'decmpfs {kind} rsrc'


def _decode(kind, data, size):
    if kind in (3, 4):
        return zlib.decompress(data)
    if kind in (7, 8):
        return lzfse.decompress_lzvn(data, size)
    if kind in (11, 12):
        return lzfse.decompress(data)
    raise ValueError(f'Unsupported decmpfs type {kind}')


def _resource_fork(kind, fork, size):
    out = bytearray()
    chunks = (size + 0xFFFF) // 0x10000
    if kind == 4:                              # classic resource fork with a block table
        base = struct.unpack_from('>I', fork, 0)[0] + 4
        count = struct.unpack_from('<I', fork, base)[0]
        for i in range(count):
            off, length = struct.unpack_from('<II', fork, base + 4 + 8 * i)
            piece = fork[base + off:base + off + length]
            out += piece[1:] if piece[:1] == b'\xff' else zlib.decompress(piece)
    else:                                       # LZVN / LZFSE: u32 offset table
        offsets = struct.unpack_from(f'<{chunks + 1}I', fork, 0)
        for i in range(chunks):
            piece = fork[offsets[i]:offsets[i + 1]]
            want = min(0x10000, size - len(out))
            if piece[:1] == b'\x06' and kind == 8 and len(piece) == want + 1:
                out += piece[1:]
            elif len(piece) == want:
                out += piece
            else:
                out += _decode(kind, piece, want)
    if len(out) != size:
        raise ValueError('decmpfs size mismatch')
    return bytes(out)
