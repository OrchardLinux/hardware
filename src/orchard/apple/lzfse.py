"""Pure-Python LZFSE decoder (bvx- raw, bvx1/bvx2 FSE, bvxn LZVN blocks), after Apple's reference lzfse."""
import struct

L_SYMBOLS, M_SYMBOLS, D_SYMBOLS, LIT_SYMBOLS = 20, 20, 64, 256
L_STATES, M_STATES, D_STATES, LIT_STATES = 64, 64, 256, 1024
L_EXTRA = [0] * 16 + [2, 3, 5, 8]
M_EXTRA = [0] * 16 + [3, 5, 8, 11]
D_EXTRA = [i // 4 for i in range(64)]


def _bases(extra):
    out, value = [], 0
    for bits in extra:
        out.append(value)
        value += 1 << bits
    return out


L_BASE, M_BASE, D_BASE = _bases(L_EXTRA), _bases(M_EXTRA), _bases(D_EXTRA)
_NBITS = [2, 3, 2, 5, 2, 3, 2, 8, 2, 3, 2, 5, 2, 3, 2, 14] * 2
_VALUE = [0, 2, 1, 4, 0, 3, 1, -1, 0, 2, 1, 5, 0, 3, 1, -1, 0, 2, 1, 6, 0, 3, 1, -1, 0, 2, 1, 7, 0, 3, 1, -1]


def _clz32(x):
    return 32 - x.bit_length()


def _decoder(nstates, freq):
    """Literal decoder: per state (k, delta, symbol)."""
    table = []
    n_clz = _clz32(nstates)
    for symbol, f in enumerate(freq):
        if not f:
            continue
        k = _clz32(f) - n_clz
        j0 = ((2 * nstates) >> k) - f
        for j in range(f):
            if j < j0:
                table.append((k, ((f + j) << k) - nstates, symbol))
            else:
                table.append((k - 1, (j - j0) << (k - 1), symbol))
    if len(table) != nstates:
        raise ValueError('Bad FSE frequencies')
    return table


def _value_decoder(nstates, freq, extra, base):
    """Value decoder: per state (total bits, value bits, delta, base)."""
    table = []
    n_clz = _clz32(nstates)
    for symbol, f in enumerate(freq):
        if not f:
            continue
        k = _clz32(f) - n_clz
        j0 = ((2 * nstates) >> k) - f
        vb = extra[symbol]
        for j in range(f):
            if j < j0:
                table.append((k + vb, vb, ((f + j) << k) - nstates, base[symbol]))
            else:
                table.append((k - 1 + vb, vb, (j - j0) << (k - 1), base[symbol]))
    if len(table) != nstates:
        raise ValueError('Bad FSE frequencies')
    return table


class _Backward:
    """FSE bit stream: the segment as one little-endian integer, read from its top bit down."""

    def __init__(self, data, start, end, initial_bits):
        self.data = data
        self.base = start
        self.pos = (end - start) * 8 + initial_bits  # initial_bits in -7..0
        if self.pos < 0:
            raise ValueError('Truncated FSE stream')

    def pull(self, n):
        if not n:
            return 0
        self.pos -= n
        p = self.pos
        if p < 0:
            raise ValueError('FSE stream underflow')
        at = self.base + (p >> 3)
        last = self.base + ((p + n - 1) >> 3)
        word = int.from_bytes(self.data[at:last + 1], 'little')
        return (word >> (p & 7)) & ((1 << n) - 1)


def _fse_block(src, p, out):
    magic = src[p:p + 4]
    n_raw = struct.unpack_from('<I', src, p + 4)[0]
    if magic == b'bvx1':
        (n_payload, n_lit, n_matches, n_lit_payload, n_lmd_payload, lit_bits) = struct.unpack_from('<IIIIIi', src, p + 8)
        lit_state = struct.unpack_from('<4H', src, p + 32)
        lmd_bits, l_state, m_state, d_state = struct.unpack_from('<iHHH', src, p + 40)
        freqs = struct.unpack_from('<360H', src, p + 50)
        payload = p + 50 + 720
    else:
        v0, v1, v2 = struct.unpack_from('<QQQ', src, p + 8)
        field = lambda v, o, n: (v >> o) & ((1 << n) - 1)
        n_lit, n_lit_payload, n_matches = field(v0, 0, 20), field(v0, 20, 20), field(v0, 40, 20)
        lit_bits = field(v0, 60, 3) - 7
        lit_state = [field(v1, 10 * i, 10) for i in range(4)]
        n_lmd_payload, lmd_bits = field(v1, 40, 20), field(v1, 60, 3) - 7
        header_size = field(v2, 0, 32)
        l_state, m_state, d_state = field(v2, 32, 10), field(v2, 42, 10), field(v2, 52, 10)
        freqs, accum, nbits, q, end = [], 0, 0, p + 32, p + header_size
        for _ in range(360):
            while q < end and nbits + 8 <= 32:
                accum |= src[q] << nbits
                nbits += 8
                q += 1
            b = accum & 31
            n = _NBITS[b]
            value = 8 + ((accum >> 4) & 0xF) if n == 8 else 24 + ((accum >> 4) & 0x3FF) if n == 14 else _VALUE[b]
            if n > nbits:
                raise ValueError('Bad LZFSE frequency table')
            freqs.append(value)
            accum >>= n
            nbits -= n
        payload = p + header_size
    l_freq, m_freq, d_freq, lit_freq = freqs[:20], freqs[20:40], freqs[40:104], freqs[104:360]

    # Literals: four interleaved FSE states.
    lit_table = _decoder(LIT_STATES, lit_freq)
    stream = _Backward(src, payload, payload + n_lit_payload, lit_bits)
    states = list(lit_state)
    literals = bytearray(n_lit + 4)
    pull = stream.pull
    for i in range(0, n_lit, 4):
        for s in range(4):
            k, delta, symbol = lit_table[states[s]]
            literals[i + s] = symbol
            states[s] = delta + pull(k)

    # L/M/D triples.
    lt = _value_decoder(L_STATES, l_freq, L_EXTRA, L_BASE)
    mt = _value_decoder(M_STATES, m_freq, M_EXTRA, M_BASE)
    dt = _value_decoder(D_STATES, d_freq, D_EXTRA, D_BASE)
    lmd_start = payload + n_lit_payload
    stream = _Backward(src, lmd_start, lmd_start + n_lmd_payload, lmd_bits)
    pull = stream.pull
    lit, d, target = 0, -1, len(out) + n_raw
    for _ in range(n_matches):
        total, vb, delta, base = lt[l_state]
        b = pull(total); l_state = delta + (b >> vb); L = base + (b & ((1 << vb) - 1))
        total, vb, delta, base = mt[m_state]
        b = pull(total); m_state = delta + (b >> vb); M = base + (b & ((1 << vb) - 1))
        total, vb, delta, base = dt[d_state]
        b = pull(total); d_state = delta + (b >> vb); D = base + (b & ((1 << vb) - 1))
        if D:
            d = D
        out += literals[lit:lit + L]
        lit += L
        if M:
            _match(out, d, M)
    if len(out) != target:
        raise ValueError('LZFSE block size mismatch')
    return payload + n_lit_payload + n_lmd_payload


def _match(out, d, m):
    start = len(out) - d
    if d <= 0 or start < 0:
        raise ValueError('Bad match distance')
    if d >= m:
        out += out[start:start + m]
    else:                                   # overlapping: repeat the period
        period = out[start:]
        reps, rest = divmod(m, d)
        out += period * reps + period[:rest]


def _lzvn(src, p, end, out):
    """LZVN instruction stream src[p:end] appended to out."""
    d = 0
    while p < end:
        op = src[p]
        if op >= 0xE0:
            if op == 0xE0:
                n = src[p + 1] + 16; p += 2
                out += src[p:p + n]; p += n
            elif op < 0xF0:
                n = op & 0xF; p += 1
                out += src[p:p + n]; p += n
            elif op == 0xF0:
                _match(out, d, src[p + 1] + 16); p += 2
            else:
                _match(out, d, op & 0xF); p += 1
            continue
        low = op & 7
        if 0xA0 <= op < 0xC0:                # medium distance
            w = src[p + 1] | (src[p + 2] << 8)
            L, M, d = (op >> 3) & 3, (((op & 7) << 2) | (w & 3)) + 3, w >> 2
            p += 3
        elif op in (0x06,):
            return
        elif op in (0x0E, 0x16):
            p += 1
            continue
        elif (0x70 <= op < 0x80) or (0xD0 <= op < 0xE0) or (low == 6 and op < 0x40):
            raise ValueError(f'Undefined LZVN opcode {op:#x}')
        else:
            L, M = op >> 6, ((op >> 3) & 7) + 3
            if low == 7:
                d = src[p + 1] | (src[p + 2] << 8); p += 3
            elif low == 6:
                p += 1
            else:
                d = (low << 8) | src[p + 1]; p += 2
        out += src[p:p + L]; p += L
        _match(out, d, M)
    raise ValueError('LZVN stream without end marker')


def decompress(src):
    src = bytes(src)
    out = bytearray()
    p = 0
    while True:
        magic = src[p:p + 4]
        if magic == b'bvx$':
            return bytes(out)
        if magic == b'bvx-':
            n = struct.unpack_from('<I', src, p + 4)[0]
            out += src[p + 8:p + 8 + n]
            p += 8 + n
        elif magic == b'bvxn':
            n_raw, n_payload = struct.unpack_from('<II', src, p + 4)
            target = len(out) + n_raw
            _lzvn(src, p + 12, p + 12 + n_payload, out)
            if len(out) != target:
                raise ValueError('LZVN block size mismatch')
            p += 12 + n_payload
        elif magic in (b'bvx1', b'bvx2'):
            p = _fse_block(src, p, out)
        else:
            raise ValueError(f'Bad LZFSE block magic {magic!r}')


def decompress_lzvn(src, size):
    """Raw LZVN stream (no bvxn header), as decmpfs type 7/8 uses."""
    out = bytearray()
    _lzvn(bytes(src), 0, len(src), out)
    if len(out) != size:
        raise ValueError('LZVN size mismatch')
    return bytes(out)
