#!/usr/bin/env python3
"""BL-01/BL-02 on T8010 (J71b) over the m1n1 proxy, read-only.

BL-01: bring up H9P PCIe port 0 the way pcie-apple-h9p.c does for T8010 and
read the storage endpoint's IDs. BL-02: NVMMU + SART, ANS registers, NVMe
enable, Identify, and a FlatDMA read of the GPT. Then stop ANS the way iBoot
does and take the port down, so BL-03 can boot Linux in the same session.

Only these NVMe opcodes exist here: admin Identify (0x06), Create/Delete I/O
CQ/SQ (0x05/0x01/0x04/0x00); I/O Read (0x02). Nothing is retried.
Sources: pcie-apple-h9p.c and pci-apple-ans.c on hardware main; iBoot stop
sequence from Berkay Kösebay's ds-nvme-d-iboot-handoff-state.md.
"""
import argparse
import os
import pathlib
import struct
import sys
import time
import uuid

sys.path.append(os.environ.get('M1N1_PROXYCLIENT', str(pathlib.Path(__file__).resolve().parent /
                                                      'm1n1-work/source/proxyclient')))

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--bl01-only', action='store_true', help='stop after the endpoint IDs')
parser.add_argument('--keep-power', action='store_true', help='leave the apcie power domain on at the end')
args = parser.parse_args()

from m1n1.setup import *  # noqa: E402,F403  (p, u, iface)

# T8010 addresses (t8010.dtsi pcie0).
CFG, RC, PHY, PHY_IP = 0x610000000, 0x600000000, 0x600008000, 0x60a000000
PORT0, NVMMU = 0x601000000, 0x601004000
GPIO_AP = 0x20f100000
PMGR = 0x20e000000                      # t8010-pmgr.dtsi: ps_pcie, ps_pcie_aux, ps_pcie_ref
DART0 = 0x601008000                     # pcie0_dart0, stream 0 = requester 0x100
QUEUE_IOVA = 0x80010000                 # inside the iommu-map window 0x80000000 + 0x3c000000
PS_REGS = (('PCIE', 0x80318), ('PCIE_AUX', 0x80320), ('PCIE_REF', 0x80148))
PERST_PIN, CLKREQ_PIN = 12, 16          # both active low
BAR_PCI, BAR_CPU = 0xc0500000, 0x7c0500000   # ranges: pci 0xc0000000 -> cpu 0x7c0000000
NVMMU_IOVA, FLATDMA_BASE, FLATDMA_STRIDE = 0xbc000000, 0x40000000, 8 << 20
MAX_REQS, MAX_PAGES = 36, 256

RC_COMMON_TUNABLES = [
    (0x0008, 0x7f7f7f7f, 0x00000000), (0x000c, 0x00073f3f, 0x00043f00), (0x0010, 0x00000700, 0x00000000),
    (0x0018, 0x00ffffff, 0x000c0960), (0x001c, 0x00001fff, 0x0000092c), (0x002c, 0x000000ff, 0x00000009),
    (0x003c, 0x80000000, 0x00000000), (0x0100, 0x31100010, 0x01000000), (0x0108, 0x00000707, 0x00000000),
    (0x010c, 0x00073f3f, 0x00043f00), (0x0110, 0x00000011, 0x00000001), (0x0114, 0x00000007, 0x00000000),
    (0x0118, 0x00073f3f, 0x00043f00), (0x0120, 0x0333003f, 0x0111000f), (0x0130, 0x000000ff, 0x0000000f),
    (0x0138, 0x0000007f, 0x0000003e), (0x0180, 0x31100010, 0x01000000), (0x0188, 0x00000707, 0x00000000),
    (0x018c, 0x00073f3f, 0x00043f00), (0x01a0, 0x0333003f, 0x0111000f), (0x01b0, 0x000000ff, 0x0000000f),
    (0x01b8, 0x0000007f, 0x0000003e), (0x0200, 0x31100010, 0x01000000), (0x0208, 0x00000707, 0x00000000),
    (0x020c, 0x00073f3f, 0x00043f00), (0x0220, 0x0333003f, 0x0111000f), (0x0230, 0x000000ff, 0x0000000f),
    (0x0238, 0x0000007f, 0x0000003e), (0x0280, 0x31100010, 0x01000000), (0x0288, 0x00000707, 0x00000000),
    (0x028c, 0x00073f3f, 0x00043f00), (0x02a0, 0x0333003f, 0x0111000f), (0x02b0, 0x000000ff, 0x0000000f),
    (0x02b8, 0x0000007f, 0x0000003e), (0x0100, 0x00000010, 0x00000010), (0x0180, 0x00000010, 0x00000000),
    (0x0200, 0x00000010, 0x00000000), (0x0280, 0x00000010, 0x00000000)]
CONFIG_TUNABLES = [(0x0098, 0x0000000f, 0), (0x0164, 0x00f8ff00, 0), (0x08e0, 0x00000005, 0x00000005)]
PORT_TUNABLES = [(0x0090, 0x000000ff, 0x00000028), (0x0130, 0x0000000d, 0x00000005),
                 (0x0134, 0x00000001, 0x00000001), (0x0138, 0x00007f7f, 0x00000000),
                 (0x013c, 0x00000002, 0x00000002), (0x0140, 0x0073ffff, 0x00704c4b)]

READ_ONLY_OPCODES = {('admin', 0x06), ('admin', 0x05), ('admin', 0x01), ('admin', 0x04),
                     ('admin', 0x00), ('io', 0x02)}


class Stop(Exception):
    pass


def log(message):
    print(f'[{time.monotonic() - T0:8.3f}] {message}', flush=True)


def rd(addr):
    return p.read32(addr)


def rmw(addr, clear, set_):
    p.write32(addr, (rd(addr) & ~clear) | set_)


def wait(addr, mask, want, seconds, what):
    deadline = time.monotonic() + seconds
    while True:
        value = rd(addr)
        if value & mask == want:
            return value
        if time.monotonic() > deadline:
            raise Stop(f'{what}: timeout, {addr:#x} = {value:#010x}')


def tunables(base, table):
    for offset, mask, value in table:
        current = rd(base + offset)
        if current & mask != value:
            p.write32(base + offset, (current & ~mask) | value)


def gpio_out(pin, level):
    # pinctrl-apple-gpio direction_output: clear PERIPH|MODE|DATA|INPUT_ENABLE, MODE=OUT.
    rmw(GPIO_AP + 4 * pin, 0x260 | 0xe | 1 | 0x200, (1 << 1) | level)


def gpio_in(pin):
    rmw(GPIO_AP + 4 * pin, 0x60 | 0xe | 1 | 0x200, (7 << 1) | 0x200)
    return rd(GPIO_AP + 4 * pin) & 1


def ltssm():
    linksts = rd(PORT0 + 0x208)
    return linksts, (linksts >> 8) & 0x3f


def cfg_addr(bus, dev=0, fn=0):
    return CFG + (bus << 20) + (dev << 15) + (fn << 12)


def find_cap(base, cap_id):
    ptr, ttl = rd(base + 0x34) & 0xff, 48
    while ptr and ttl:
        word = rd(base + ptr)
        if word & 0xff == cap_id:
            return ptr
        ptr, ttl = (word >> 8) & 0xff, ttl - 1
    return 0


def find_ext_cap(base, cap_id):
    ptr, ttl = 0x100, 64
    while ptr and ttl:
        word = rd(base + ptr)
        if word in (0, 0xffffffff):
            return 0
        if word & 0xffff == cap_id:
            return ptr
        ptr, ttl = (word >> 20) & 0xffc, ttl - 1
    return 0


def zeroed(size, align=0x4000):
    addr = u.memalign(align, size)
    iface.writemem(addr, bytes(size))
    p.dc_civac(addr, size)
    return addr


T0 = time.monotonic()
result = {}

# ---------------------------------------------------------------- BL-01
def bl01():
    # Nothing in the PCIe block may be read before it is powered and clocked:
    # an unclocked read takes a synchronous exception. /arm-io/apcie's ADT
    # clock gates are PCIE, PCIE_AUX, PCIE_REF (= Linux ps_pcie, aux, ref).
    states = {name: rd(PMGR + off) for name, off in PS_REGS}
    log('PMGR before: ' + ' '.join(f'{n} {v:#x}' for n, v in states.items()))
    log(f'power on /arm-io/apcie: {p.pmgr_adt_power_enable("/arm-io/apcie")}')
    states = {name: rd(PMGR + off) for name, off in PS_REGS}
    log('PMGR after: ' + ' '.join(f'{n} {v:#x}' for n, v in states.items()))
    off = [n for n, v in states.items() if (v >> 4) & 0xf != 0xf]
    if off:
        raise Stop(f'still powered off: {off}; not touching PCIe registers')
    result['powered'] = True
    log(f'before: RC stat {rd(RC + 0x28):#x} RC100 {rd(RC + 0x100):#x} '
        f'RC114 {rd(RC + 0x114):#x} RC124 {rd(RC + 0x124):#x}')

    # apple_t8010_setup_common
    p.write32(RC + 0x04, 1 << 4)
    rmw(RC + 0x124, 0, 1)
    wait(RC + 0x28, 1 << 4, 1 << 4, 0.25, 'global PHY init')
    wait(RC + 0x28, 1, 1, 0.25, 'global PHY ready')
    p.write32(RC + 0x34, 1)
    tunables(RC, RC_COMMON_TUNABLES)
    p.write32(RC + 0x14, 1)
    time.sleep(0.01)
    p.write32(RC + 0x24, 1)
    time.sleep(0.001)
    log('common setup done')

    # apple_t8010_setup_port(0)
    linksts, state = ltssm()
    if 0x11 <= state <= 0x14:
        log(f'link already up (LINKSTS {linksts:#x}); not touching port setup')
        result['link'] = True
    else:
        gpio_out(PERST_PIN, 0)          # assert PERST#
        time.sleep(0.001)
        rmw(RC + 0x134, 1, 0)
        rmw(RC + 0x124, 0, 1)
        wait(RC + 0x28, 1 << 4, 1 << 4, 0.25, 'port 0 init')
        time.sleep(0.0002)
        rmw(RC + 0x100, 0, 1)
        rmw(RC + 0x100, 0x100, 0)
        time.sleep(0.0002)
        rmw(RC + 0x134, 0, 1)
        p.write32(RC + 0x4020, 3)       # 8 GT/s
        rmw(RC + 0x124, 0x100, 0)
        wait(PHY + 0x0c, 1, 1, 0.25, 'PHY lane 0 up')

        # apple_h9p_program_phy_port, lane 0
        rmw(PHY_IP + 0x180, 0, 0x4000)
        rmw(PHY_IP + 0x184, 0, 0x4000)
        rmw(PHY_IP + 0x090, 0xfff, 100)
        rmw(PHY_IP + 0x098, 0xfff, 25)
        rmw(PHY_IP + 0x10088, 0, 0x4000)
        p.write32(PHY_IP + 0x10784, 0)
        rmw(PHY_IP + 0x10004, 0xfff, 0x600)
        p.write32(PHY_IP + 0x20788, 0x3105)
        rmw(PHY_IP + 0x207a0, 0xff, 0x9f)
        rmw(PHY_IP + 0x207a8, 0xff, 0x01)
        rmw(PHY_IP + 0x20400, 0x1f, 0x0a)
        for reg, value in ((0x2009c, 175), (0x200dc, 175), (0x200a0, 333), (0x200e0, 333),
                           (0x200a4, 530), (0x200e4, 530), (0x20330, 0), (0x20340, 0), (0x20350, 0)):
            p.write32(PHY_IP + reg, value)

        deadline = time.monotonic() + 0.25
        while gpio_in(CLKREQ_PIN) != 0:     # CLKREQ# asserted = low
            if time.monotonic() > deadline:
                raise Stop('CLKREQ# timeout')

        root = cfg_addr(0)
        cap = find_cap(root, 0x10)
        log(f'root port PCIe cap at {cap:#x}')
        if cap:
            rmw(root + cap + 0x30, 0xf, 3)  # LNKCTL2 target speed 8 GT/s
        tunables(root, CONFIG_TUNABLES)
        header = [rd(root + off) for off in range(0x08, 0x40, 4)]
        tunables(PORT0, PORT_TUNABLES)
        rmw(root + 0x8e0, 0, 1)
        p.write32(PORT0 + 0x104, 0xff002fff)
        p.write32(PORT0 + 0x100, 0x00ffd000)
        rmw(PORT0 + 0x140, 0, 1 << 31)
        p.write32(PORT0 + 0x124, 0x31)
        p.write32(PORT0 + 0x128, 0)
        p.write32(PORT0 + 0x104, 0xff002f0f)
        for i, off in enumerate(range(0x08, 0x40, 4)):
            p.write32(root + off, header[i])
        gpio_out(PERST_PIN, 1)          # deassert PERST#
        rmw(PORT0 + 0x80, 0, 1)         # LTSSM enable
        deadline = time.monotonic() + 0.5
        while True:
            linksts, state = ltssm()
            if 0x11 <= state <= 0x14:
                break
            if time.monotonic() > deadline:
                raise Stop(f'port 0 link did not reach L0 (LINKSTS {linksts:#x}, LTSSM {state:#x})')
            time.sleep(0.001)
        log(f'port 0 link up: LINKSTS {linksts:#x} LTSSM {state:#x}')
    result['link'] = True

    # Bridge: bus 0 -> 1, memory window for BAR0, memory + bus master.
    root = cfg_addr(0)
    p.write32(root + 0x18, 0x00010100)
    window = (BAR_PCI >> 16) & 0xfff0      # one 1 MiB window holding BAR0
    p.write32(root + 0x20, window << 16 | window)
    rmw(root + 0x04, 0, 0x6)
    ep = cfg_addr(1)
    ids, klass = rd(ep), rd(ep + 0x08)
    log(f'endpoint 01:00.0 ids {ids:#010x} class/rev {klass:#010x}')
    result['endpoint'] = (ids, klass)
    if ids & 0xffff != 0x106b:
        raise Stop('endpoint is not Apple (0x106b)')
    return ep


# ---------------------------------------------------------------- BL-02
class Nvme:
    def __init__(self, ep):
        self.ep = ep
        node = u.adt['/arm-io/nvme-mmu0']
        region = list(node.sart_region)
        self.hmb_pa, self.hmb_size = int(region[0]), int(region[1])
        log(f'HMB carve-out {self.hmb_pa:#x} size {self.hmb_size:#x}')

    def endpoint_config(self):
        ep = self.ep
        p.write32(ep + 0x10, BAR_PCI | 0x4)   # 64-bit memory BAR
        p.write32(ep + 0x14, 0)
        rmw(ep + 0x04, 0, 0x6)
        rmw(ep + 0x0c, 0xff00, 0x4000)        # latency timer 0x40
        ltr = find_ext_cap(ep, 0x18)
        if ltr:
            p.write32(ep + ltr + 4, 0x10081008)
        l1ss = find_ext_cap(ep, 0x1e)
        if l1ss:
            p.write32(ep + l1ss + 0x0c, 0)
            p.write32(ep + l1ss + 0x08, 0x40550000)
        log(f'endpoint configured: LTR cap {ltr:#x}, L1SS cap {l1ss:#x}')

    def setup_nvmmu(self):
        self.tcb = zeroed(0x4000)                       # 36 x 0x80
        self.tcb_table = zeroed(16 * 0x4000)
        self.sgl = zeroed(MAX_REQS * MAX_PAGES * 4)
        for off, value in ((0x08, self.tcb), (0x10, self.tcb_table)):
            p.write32(NVMMU + off, value & 0xffffffff)
            p.write32(NVMMU + off + 4, value >> 32)
        p.write32(NVMMU + 0x04, 1 << 16)
        wait(NVMMU + 0x04, 1 << 4, 0, 0.25, 'NVMMU init')
        end = (NVMMU_IOVA + self.hmb_size + 0xfffff) & ~0xfffff
        p.write32(NVMMU + 0x24, NVMMU_IOVA - 0x80000000)
        p.write32(NVMMU + 0x28, end - 0x80100000)
        p.write32(NVMMU + 0x2c, self.hmb_pa >> 20)
        p.write32(NVMMU + 0x20, 1)
        log('NVMMU and SART set up')

    def tcb_map(self, tag, pages, to_memory):
        tcb = self.tcb + tag * 0x80
        sgl = self.sgl + tag * MAX_PAGES * 4
        iface.writemem(sgl, struct.pack(f'<{len(pages)}I', *[pg >> 12 for pg in pages]))
        p.dc_civac(sgl, MAX_PAGES * 4)
        body = struct.pack('<IIII', 0x200 if to_memory else 0x100, 0, pages[0] >> 12, 0) + struct.pack('<Q', sgl)
        iface.writemem(tcb, body + bytes(0x80 - len(body)))
        p.dc_civac(tcb, 0x80)
        p.write32(tcb + 4, len(pages))          # page count last: makes the TCB valid
        p.dc_civac(tcb, 0x80)
        return FLATDMA_BASE + tag * FLATDMA_STRIDE

    def tcb_retire(self, tag):
        tcb = self.tcb + tag * 0x80
        p.write32(tcb + 4, 0)
        iface.writemem(tcb, bytes(0x80))
        p.dc_civac(tcb, 0x80)
        p.write32(NVMMU + 0x04, tag)

    # Queues go through pcie0_dart0 like Linux's coherent allocations.
    # S5L8960X format (io-pgtable-dart, 4 KiB pages, ias 32): 4 TTBRs of
    # 1 GiB, two levels, tables and leaves pa | 3. Whether the bridge hands
    # the DART absolute IOVAs or window-relative ones is not settled, so
    # the same L1 table sits behind TTBR 2 (absolute) and TTBR 0 (relative).
    def setup_dart(self):
        node = u.adt['/arm-io/dart-apcie0']
        reflector = int(list(node.error_reflector)[0]) >> 12
        fetch, diag = int(node.fetch_config), int(node.diag_config)
        self.l1, self.l2 = zeroed(0x4000), zeroed(0x4000)
        iface.writemem(self.l1, struct.pack('<Q', self.l2 | 3))
        p.dc_civac(self.l1, 0x4000)
        ttbr = (self.l1 >> 12) | 1 << 31
        rmw(DART0 + 0x0c, 0xff, 0)                      # stream 0 translation off
        for idx in range(4):
            p.write32(DART0 + 0x40 + 4 * idx, ttbr if idx in (0, 2) else 0)
        p.write32(DART0 + 0x24, reflector)
        rmw(DART0 + 0x0c, 0xff, 0x80)                   # translation on
        p.write32(DART0 + 0x30, fetch)
        p.write32(DART0 + 0x20, diag)
        p.write32(DART0 + 0x00, 1 << 8 | 1 << 1)         # invalidate stream 0
        wait(DART0 + 0x00, 1 << 3, 0, 0.1, 'DART invalidate')
        p.write32(DART0 + 0x10, rd(DART0 + 0x10))       # clear stale errors
        log(f'DART: TCR {rd(DART0 + 0x0c):#x} TTBR0 {rd(DART0 + 0x40):#x} TTBR2 {rd(DART0 + 0x48):#x} '
            f'fetch {rd(DART0 + 0x30):#x} diag {rd(DART0 + 0x20):#x} error {rd(DART0 + 0x10):#x}')
        self.next_queue = 0

    def queue(self, _offset=None):
        pa = zeroed(0x1000, 0x1000)
        iova = QUEUE_IOVA + self.next_queue * 0x1000
        index = (iova >> 12) & 511
        iface.writemem(self.l2 + 8 * index, struct.pack('<Q', pa | 3))
        p.dc_civac(self.l2, 0x4000)
        self.next_queue += 1
        return pa, iova

    def enable(self):
        bar = BAR_CPU
        cap = rd(bar) | rd(bar + 4) << 32
        vs, cc, csts = rd(bar + 0x8), rd(bar + 0x14), rd(bar + 0x1c)
        core_mask, log_size = rd(bar + 0x1824), rd(bar + 0x1828)
        hmb_req, hmb_align = rd(bar + 0x1808), rd(bar + 0x180c)
        log(f'CAP {cap:#x} VS {vs:#x} CC {cc:#x} CSTS {csts:#x} core_mask {core_mask:#x} '
            f'log_size {log_size:#x} HMB request {hmb_req:#x} align {hmb_align:#x}')
        result['hmb_request'] = (hmb_req, hmb_align)
        if not hmb_req or hmb_req == 0xffffffff:
            raise Stop('controller reports no HMB request')
        if hmb_req > self.hmb_size:
            raise Stop(f'HMB request {hmb_req:#x} exceeds the carve-out {self.hmb_size:#x}')
        self.dstrd = 4 << ((cap >> 32) & 0xf)
        timeout = max(30, min(120, ((cap >> 24) & 0xff) * 0.5))

        p.write32(bar + 0x14, 0)
        wait(bar + 0x1c, 0x3, 0, 2, 'controller disable')
        for tag in range(MAX_REQS):
            self.tcb_retire(tag)
        p.write32(bar + 0x1800, 0)
        p.write32(bar + 0x1810, NVMMU_IOVA)
        p.write32(bar + 0x1814, 0)
        p.write32(bar + 0x1818, self.hmb_size)          # Linux passes the whole carve-out
        rd(bar + 0x1818)

        self.setup_dart()
        self.asq, self.asq_iova = self.queue()
        self.acq, self.acq_iova = self.queue()
        self.iosq, self.iosq_iova = self.queue()
        self.iocq, self.iocq_iova = self.queue()
        self.depth = 16
        self.state = {'admin': [0, 0, 1], 'io': [0, 0, 1]}   # sq tail, cq head, phase
        p.write32(bar + 0x24, (self.depth - 1) << 16 | (self.depth - 1))
        for off, value in ((0x28, self.asq_iova), (0x30, self.acq_iova)):
            p.write32(bar + off, value & 0xffffffff)
            p.write32(bar + off + 4, value >> 32)
        p.write32(bar + 0x14, 1 | 6 << 16 | 4 << 20)
        started = time.monotonic()
        deadline = started + timeout
        while True:
            csts = rd(bar + 0x1c)
            if csts & 0x2:
                raise Stop(f'controller fatal (CSTS {csts:#x}) {time.monotonic() - started:.2f} s after enable; '
                           f'DART error {rd(DART0 + 0x10):#x} addr {rd(DART0 + 0x1c):#x}')
            if csts & 0x1:
                break
            if time.monotonic() > deadline:
                raise Stop(f'controller enable (RDY): timeout, CSTS {csts:#x}')
            time.sleep(0.01)
        log(f'controller ready after {time.monotonic() - started:.2f} s')

    def submit(self, kind, opcode, nsid=0, prp1=0, cdw10=0, cdw11=0, cdw12=0, flatdma=False):
        if (kind, opcode) not in READ_ONLY_OPCODES:
            raise Stop(f'refusing non-read-only opcode {kind} {opcode:#x}')
        sq, cq, qid = (self.asq, self.acq, 0) if kind == 'admin' else (self.iosq, self.iocq, 1)
        tail, head, phase = self.state[kind]
        cid = (tail + 1) & 0xffff
        cmd = struct.pack('<BBHI8xQQQIIIIII', opcode, 0x20 if flatdma else 0, cid, nsid, 0, prp1, 0,
                          cdw10, cdw11, cdw12, 0, 0, 0)
        iface.writemem(sq + tail * 64, cmd)
        p.dc_civac(sq + tail * 64, 64)
        tail = (tail + 1) % self.depth
        p.write32(BAR_CPU + 0x1000 + 2 * qid * self.dstrd, tail)
        deadline = time.monotonic() + 10
        while True:
            p.dc_civac(cq + head * 16, 16)
            dw0, dw1, sqhd, cid_status = struct.unpack('<IIII', iface.readmem(cq + head * 16, 16))
            if (cid_status >> 16) & 1 == phase:
                break
            if time.monotonic() > deadline:
                raise Stop(f'{kind} opcode {opcode:#x}: no completion; CSTS {rd(BAR_CPU + 0x1c):#x}')
        head = (head + 1) % self.depth
        if head == 0:
            phase ^= 1
        p.write32(BAR_CPU + 0x1000 + (2 * qid + 1) * self.dstrd, head)
        self.state[kind] = [tail, head, phase]
        status = (cid_status >> 17) & 0x7fff
        if status:
            raise Stop(f'{kind} opcode {opcode:#x}: status {status:#x}')
        return dw0

    def read_pages(self, kind, opcode, npages, tag, **fields):
        buf = zeroed(npages * 0x1000, 0x1000)
        pages = [buf + i * 0x1000 for i in range(npages)]
        flat = self.tcb_map(tag, pages, to_memory=True)
        try:
            self.submit(kind, opcode, prp1=flat, flatdma=True, **fields)
        finally:
            self.tcb_retire(tag)
        p.dc_civac(buf, npages * 0x1000)
        return iface.readmem(buf, npages * 0x1000)

    def identify_and_read(self):
        ctrl = self.read_pages('admin', 0x06, 1, 0, cdw10=1)
        serial, model, fw = (ctrl[a:b].decode(errors='replace').strip('\0 ') for a, b in ((4, 24), (24, 64), (64, 72)))
        log(f'Identify: model "{model}" firmware "{fw}"')
        result['identify'] = (model, fw)
        ns = self.read_pages('admin', 0x06, 1, 0, nsid=1, cdw10=0)
        nsze = struct.unpack_from('<Q', ns, 0)[0]
        flbas = ns[26] & 0xf
        lbads = ns[128 + 4 * flbas + 2]
        block = 1 << lbads
        log(f'namespace 1: {nsze} blocks of {block} bytes')
        self.submit('admin', 0x05, prp1=self.iocq_iova, cdw10=(self.depth - 1) << 16 | 1, cdw11=1)
        self.submit('admin', 0x01, prp1=self.iosq_iova, cdw10=(self.depth - 1) << 16 | 1, cdw11=1 << 16 | 1)
        count = 2 * 0x1000 // block if block < 0x1000 else 2
        data = self.read_pages('io', 0x02, 2, 1, nsid=1, cdw10=0, cdw11=0, cdw12=count - 1)
        header = data[block:block + 92]
        if header[:8] != b'EFI PART':
            raise Stop(f'LBA 1 is not a GPT header: {header[:16].hex()}')
        guid = uuid.UUID(bytes_le=header[56:72])
        entries_lba, n_entries, entry_size = struct.unpack_from('<QII', header, 72)
        log(f'GPT: disk GUID {guid}, {n_entries} entries of {entry_size} B at LBA {entries_lba}')
        result['gpt'] = str(guid)
        span = (n_entries * entry_size + block - 1) // block
        npages = min(MAX_PAGES, (span * block + 0xfff) // 0x1000)
        table = self.read_pages('io', 0x02, npages, 1, nsid=1, cdw10=entries_lba & 0xffffffff,
                                cdw11=entries_lba >> 32, cdw12=span - 1)
        for i in range(n_entries):
            entry = table[i * entry_size:(i + 1) * entry_size]
            if entry[:16] == bytes(16):
                continue
            first, last = struct.unpack_from('<QQ', entry, 32)
            name = entry[56:128].decode('utf-16-le', errors='replace').rstrip('\0')
            log(f'  part {i + 1}: {uuid.UUID(bytes_le=entry[:16])} {first}-{last} "{name}"')

    def shutdown(self):
        # Linux-style queue teardown, then iBoot's stop: SHN=01, SHST complete, EN=0, RDY=0.
        if rd(BAR_CPU + 0x1c) & 0x3 == 0x1:
            try:
                self.submit('admin', 0x00, cdw10=1)
                self.submit('admin', 0x04, cdw10=1)
            except Stop as error:
                log(f'queue delete: {error}')
        bar = BAR_CPU
        if rd(bar + 0x1c) & 0x2:
            log('controller is in fatal state; skipping shutdown notification')
        else:
            p.write32(bar + 0x14, (rd(bar + 0x14) & ~(3 << 14)) | 1 << 14)
            wait(bar + 0x1c, 0xc, 0x8, 10, 'shutdown complete (SHST)')
        p.write32(bar + 0x14, rd(bar + 0x14) & ~1)
        wait(bar + 0x1c, 0x1, 0, 10, 'controller disable after shutdown')
        for tag in range(MAX_REQS):
            self.tcb_retire(tag)
        log(f'ANS stopped: CC {rd(bar + 0x14):#x} CSTS {rd(bar + 0x1c):#x}')


def port_down():
    if not result.get('powered'):
        return
    if result.get('link'):
        rmw(cfg_addr(1) + 0x04, 0x4, 0)    # endpoint bus mastering off
    # Linux expects the port interrupt clear, as after power-on reset; a
    # pending link-down event left here storms IRQ 60. Mask before the link
    # goes down, clear while the port registers are still powered.
    p.write32(PORT0 + 0x104, 0xffffffff)   # mask everything
    rmw(PORT0 + 0x80, 1, 0)                # LTSSM off
    gpio_out(PERST_PIN, 0)                 # PERST# asserted
    p.write32(NVMMU + 0x20, 0)             # SART aperture off
    rmw(DART0 + 0x0c, 0xff, 0)             # DART stream 0 translation off, tables gone
    for idx in range(4):
        p.write32(DART0 + 0x40 + 4 * idx, 0)
    time.sleep(0.01)
    pending = rd(PORT0 + 0x100)
    p.write32(PORT0 + 0x100, pending)      # write-1-to-clear what the link-down raised
    log(f'port IRQ: pending {pending:#x}, now {rd(PORT0 + 0x100):#x}, mask {rd(PORT0 + 0x104):#x}')
    for reg in (0x124, 0x114, 0x100):      # iBoot's port-down order
        rmw(RC + reg, 1, 0)
    linksts, state = ltssm()
    log(f'port down: RC124 {rd(RC + 0x124):#x} RC114 {rd(RC + 0x114):#x} RC100 {rd(RC + 0x100):#x} '
        f'LINKSTS {linksts:#x}')
    if not args.keep_power:
        log(f'power off /arm-io/apcie: {p.pmgr_adt_power_disable("/arm-io/apcie")}')


nvme = None
outcome = 'PASS'
try:
    ep = bl01()
    log('BL-01 PASS')
    if not args.bl01_only:
        nvme = Nvme(ep)
        nvme.endpoint_config()
        nvme.setup_nvmmu()
        nvme.enable()
        nvme.identify_and_read()
        log('BL-02 PASS')
except Stop as error:
    outcome = f'STOP: {error}'
    log(outcome)
finally:
    try:
        if nvme is not None and hasattr(nvme, 'asq'):
            nvme.shutdown()
        port_down()
    except Stop as error:
        log(f'shutdown: {error}')
        outcome += f'; shutdown: {error}'
print(f'RESULT {outcome} {result}', flush=True)
