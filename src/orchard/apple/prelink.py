"""Locate one kext in a legacy prelinked ARM64 kernelcache.

No hardcoded offsets: the kext is selected by its prelink metadata, and its
virtual addresses are mapped through the outer kernel's file-backed segments.
For MH_FILESET kernels use the upstream ipsw kext extractor instead.
"""
from typing import Any, Dict, List, Tuple

from orchard.apple.macho import macho
from orchard.apple.osserialize import apple_xml

Section = Dict[str, Any]


class Kernelcache:
    def __init__(self, blob: bytes):
        kind, self.segments, self.sections = macho(blob)
        if kind != 2:
            raise ValueError('Expected legacy MH_EXECUTE kernelcache')
        self.blob = blob

    def offset(self, addr: int, size: int) -> int:
        offsets = {s['offset'] + addr - s['vm'] for s in self.segments
                   if s['vm'] <= addr and addr + size <= s['vm'] + s['size']}
        if len(offsets) != 1:
            raise ValueError('Section has no unique file-backed mapping')
        offset = offsets.pop()
        if offset + size > len(self.blob):
            raise ValueError('Section exceeds file')
        return offset

    def read(self, addr: int, size: int) -> bytes:
        offset = self.offset(addr, size)
        return self.blob[offset:offset + size]

    def kext_sections(self, bundle: str, label: str) -> List[Section]:
        """The sections of the one prelinked kext with this bundle identifier."""
        info = [s for s in self.sections if (s['segment'], s['name']) == ('__PRELINK_INFO', '__info')]
        if len(info) != 1:
            raise ValueError('No unique prelink metadata section')
        metadata = apple_xml(self.read(info[0]['addr'], info[0]['size']))
        kexts = [k for k in metadata['_PrelinkInfoDictionary'] if k.get('CFBundleIdentifier') == bundle]
        if len(kexts) != 1:
            raise ValueError(f'No unique {label} kext')
        _, _, sections = macho(self.blob, self.offset(kexts[0]['_PrelinkExecutableLoadAddr'], 32))
        return sections


def named(sections: List[Section], *keys: Tuple[str, str]) -> Dict[Tuple[str, str], Section]:
    """Exactly one section per (segment, section) key."""
    result = {}
    for key in keys:
        found = [s for s in sections if (s['segment'], s['name']) == key]
        if len(found) != 1:
            raise ValueError('No unique section %s.%s' % key)
        result[key] = found[0]
    return result
