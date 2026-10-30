"""Install targets from a device's partition layout, and the plan for one.

Input is the `layout` event of the device install script's `inspect` (see
install_device.py). Pure logic: nothing here touches a device. The rules
follow hardware docs/boot-contract.md:

- APFS and every partition that is not a Linux type are never touched.
- A target is either a free range or one existing Linux partition to replace.
- The disk has at most one XBOOTLDR boot partition; it is created inside the
  target when missing, and an existing one is reused.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional
import uuid

XBOOTLDR = 'bc13c2ff-59e6-4262-a352-b275fd6f7172'
ROOT_ARM64 = 'b921b045-1df0-41c3-af44-4c6f280d3fae'
LINUX_DATA = '0fc63daf-8483-4772-8e79-3d69d8477de4'
REPLACEABLE = {ROOT_ARM64, LINUX_DATA}

MIB = 1 << 20
GIB = 1 << 30
BOOT_BYTES = 1 * GIB
MIN_ROOT_BYTES = 8 * GIB
PLAN_VERSION = 1


class LayoutError(ValueError):
    pass


@dataclass
class Option:
    kind: str                      # 'free' or 'replace'
    start: int                     # first block of the target range
    blocks: int
    description: str
    partition: Optional[dict] = None
    creates_boot: bool = False

    def bytes(self, block):
        return self.blocks * block


def _align_up(value, step):
    return -(-value // step) * step


def _align_down(value, step):
    return value // step * step


def boot_partition(layout) -> Optional[dict]:
    boots = [p for p in layout['partitions'] if p['type'] == XBOOTLDR]
    if len(boots) > 1:
        raise LayoutError('The disk has more than one XBOOTLDR boot partition; '
                          'refusing to choose one (see the boot contract)')
    return boots[0] if boots else None


def options(layout) -> List[Option]:
    """Every allowed target, in disk order."""
    block = layout['disk']['sector']
    boot = boot_partition(layout)
    needed = MIN_ROOT_BYTES + (0 if boot else BOOT_BYTES)
    align = MIB // block
    result = []
    for free in layout['free']:
        start = _align_up(free['start'], align)
        end = _align_down(free['start'] + free['blocks'], align)
        if (end - start) * block >= needed:
            result.append(Option('free', start, end - start,
                                 f'free space, {(end - start) * block / GIB:.1f} GiB',
                                 creates_boot=boot is None))
    for part in layout['partitions']:
        if part['type'] not in REPLACEABLE:
            continue
        if part['blocks'] * block < needed:
            continue
        label = part.get('label') or part.get('name') or ''
        fs = part.get('fs') or 'no filesystem'
        result.append(Option('replace', part['start'], part['blocks'],
                             f'replace partition {part["number"]} ({fs}{", " + label if label else ""}, '
                             f'{part["blocks"] * block / GIB:.1f} GiB) - erases it',
                             partition=part, creates_boot=boot is None))
    return sorted(result, key=lambda o: o.start)


def plan(layout, option: Option, image: Dict, entry: Dict, firmware: Optional[Dict] = None,
         account: bool = False) -> Dict:
    """The plan `install_device.py run` executes for one chosen option."""
    block = layout['disk']['sector']
    align = MIB // block
    boot = boot_partition(layout)
    start, end = option.start, option.start + option.blocks
    if option.kind == 'replace':
        # A replaced partition's range is reused; new partitions start aligned.
        start = _align_up(start, align)
    new_boot = None
    if boot is None:
        boot_blocks = BOOT_BYTES // block
        new_boot = {'start': start, 'blocks': boot_blocks, 'partuuid': str(uuid.uuid4()),
                    'fs_uuid': uuid.uuid4().hex[:8].upper()}
        start += boot_blocks
    root_blocks = _align_down(end, align) - start
    if root_blocks * block < MIN_ROOT_BYTES:
        raise LayoutError('Target is too small for a root partition')
    if root_blocks * block < image['expanded_bytes'] * 1.25:
        raise LayoutError('Target is too small for this system image')
    remove = None
    if option.partition is not None:
        remove = {key: option.partition[key] for key in ('number', 'partuuid', 'start', 'blocks')}
    if new_boot is not None:
        boot_plan = {**new_boot, 'create': True}
    else:
        boot_plan = {'create': False, 'partuuid': boot['partuuid'], 'number': boot['number']}
    return {
        'version': PLAN_VERSION,
        'disk': {'path': layout['disk']['path'], 'sector': block,
                 'table_sha256': layout['disk']['table_sha256']},
        'remove': remove,
        'boot': boot_plan,
        'root': {'start': start, 'blocks': root_blocks, 'partuuid': str(uuid.uuid4()),
                 'fs_uuid': str(uuid.uuid4()), 'fs': 'ext4', 'label': entry['id'][:16]},
        'image': image,
        'entry': entry,
        'firmware': firmware,
        'account': account,     # secrets travel separately, never in the plan
    }
