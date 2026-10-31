"""Typed host adapter boundary, not a device/plugin or boot-method framework.

Adapters remain ordinary modules. These records describe the current supported
USB/checkm8 workflow; common orchestration does not interpret chip/board values.
Runtime artifact/identity verification remains in the existing implementations.
"""
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Protocol, Tuple, TypedDict, Union, runtime_checkable

from .progress import Clock, Progress
from .state import BootState

USBLocation = Union[int, str]


class _SelectionDetails(TypedDict, total=False):
    chip: Optional[int]
    board: Optional[int]
    product: Optional[str]
    serial_port: Optional[str]
    pwned: bool
    boot_id: Optional[str]


class SelectedDevice(_SelectionDetails):
    """Validated discovery result; resumed is explicit, never inferred by the host."""
    location: USBLocation
    mode: Literal['dfu', 'pongo', 'linux']
    ecid: int
    resumed: bool


class BootAssets(TypedDict, total=False):
    """Verified local artifacts; Pongo is required only when starting from DFU.

    A running recovery needs no upload; otherwise recovery must be supplied.
    root is used by installation, not by the adapter's boot transport.
    """
    pongo: Path
    recovery: Path
    root: Path


class BootReceipt(TypedDict):
    sha256: str


class BootRequest(TypedDict):
    """Minimum adapter boot input, shared by resolved and explicit-file requests."""
    assets: Mapping[str, BootReceipt]


class MixinReceipt(TypedDict):
    schema: Literal[1]
    kind: Literal['private-mixin']
    bytes: int
    sha256: str


@dataclass(frozen=True)
class Connection:
    argv: List[str]
    scope: BootState
    check: Callable[[], None]


@runtime_checkable
class Adapter(Protocol):
    """Required interface of a registered adapter module.

    Discovery is read-only and raises ValueError when no unique supported device
    is present. Boot must never retry uncertain mutations. connect(device) binds
    the selected ECID/boot.
    Exceptions propagate: the host owns confirmation/reporting, not recovery
    recipes. There are no optional members or legacy fallbacks.
    """
    PROFILE: str
    NAME: str
    REBOOT_MESSAGE: str
    # USB product of the device's recovery system; None until it has one.
    PRODUCT: Optional[str]
    # Support data from the hardware descriptor ([support], [features]).
    INSTALL: bool
    STATUS: str
    CAVEATS: List[str]
    FEATURES: Dict[str, str]
    # Board DTB file name a boot entry must carry (from [kernel] dtb).
    DEVICETREE: Optional[str]

    def matches_dfu(self, device: Dict[str, Any]) -> bool: ...

    def list_devices(self) -> List[Dict[str, Any]]: ...

    def discover(self) -> SelectedDevice: ...

    def boot(self, device: SelectedDevice, assets: BootAssets,
             manifest: BootRequest, progress: Progress) -> None: ...

    def start_loader(self, device: SelectedDevice, loader: Dict[str, Path], progress: Progress) -> str: ...

    def connect(self, device: SelectedDevice) -> AbstractContextManager[Connection]: ...

    def capture_adt(self, device: Optional[SelectedDevice] = None) -> bytes: ...

    def firmware_inputs(self, clock: Clock, progress: Progress, *, allow_download: bool = True) -> Path: ...

    def prepare_mixin(self, firmware_dir: Path, outdir: Path) -> Tuple[Path, MixinReceipt]: ...
