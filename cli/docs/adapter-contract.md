# Host adapter contract

`orchard_cli.contracts.Adapter` is the typed structural interface for registered **module-based** adapters. `devices.load()` returns this interface; `choose()` returns `(Adapter, SelectedDevice)`. There is no base class, plugin discovery, optional operation, compatibility alias or boot-method registry.

## Required metadata and operations

| Member | Contract and effects |
| --- | --- |
| `PROFILE`, `NAME`, `REBOOT_MESSAGE` | Registered profile key, human-readable name and fresh-boot instructions. The profile must match its registry entry. |
| `list_devices()` | Raw discovery observations for display; read-only, no boot/preparation or implicit adapter selection. Vendor-specific observation fields are not interpreted by the common workflow. |
| `discover()` | Read-only identification of exactly one supported device. Return a validated `SelectedDevice`; raise `ValueError` when no unique supported device is available. |
| `boot(device, assets, manifest, progress)` | Enter recovery using already-verified boot artifacts. A resumed recovery is not rebooted/uploaded. Keep identity/port checks and timeout budgets; never retry an uncertain mutation. |
| `connect(device)` | Context-managed serial bootstrap and pinned SSH access bound to the selected ECID/boot. No host-side device storage/service recipes. |
| `capture_adt(device=None)` | Fresh, bounded, private Apple ADT capture for this adapter's model. When supplied, bind the confirmed device selection to the captured identity. Do not log or implicitly retain the raw tree. |
| `firmware_inputs(clock, progress)` | Return verified cached/acquired host firmware inputs; account download time separately. No device hardware or storage mutation. |
| `prepare_mixin(adt, firmware_dir, outdir)` | Return `(Path, MixinReceipt)` for a private data-only archive bound to the selected device/recipe. Preserve firmware hash, containment, calibration and output guards. |

Arguments and return types are defined in `contracts.py`, not repeated in a second runtime schema. Image-facing protocol validation, manifest validation and hardware identity verification remain in their existing owners.

## Records

- `SelectedDevice`: required `mode`, `location`, non-null validated `ecid` and explicit boolean `resumed`; optional observed chip/board/product/serial details. `resumed` is true for running recovery and false for DFU/Pongo. Common orchestration requires this explicit state. A USB location may change across enumeration and is not persistent device identity.
- `Connection`: SSH arguments, validated `BootState` and a current-endpoint check performed before every RPC. Private state is keyed by profile/ECID/boot, never by USB port.
- `BootAssets`: local paths for the selected operation. Running recovery needs no upload; other states require recovery, and DFU additionally requires Pongo. Installation root is not interpreted by the adapter's boot method.
- `BootRequest`: minimum boot input, including the recovery SHA receipt. Both manifest-resolved and explicit-file boot use it; no source-specific fallback adapter API exists.
- `MixinReceipt`: schema 1, `private-mixin`, exact compressed byte count and SHA-256.

TypedDict records describe the boundary. They do not replace validation of untrusted USB data or manifests, and do not imply that every dictionary constructed by an arbitrary caller is safe.

## Enforcement

The registry checks required member presence, profile identity and presentation metadata without invoking discovery. Python runtime Protocol checks do not verify function signatures; static checking does that. The real J71b module is assigned directly to `Adapter` in `tests/typecheck_adapter.py`, so signature drift is rejected without creating a wrapper implementation.

From `cli/`:

```sh
uv tool run --from 'mypy==1.18.2' mypy --config-file pyproject.toml
```

The development-only checker is declared in the dev dependency group, not runtime dependencies. It targets Python 3.11 and checks the contract, registry, registered implementation and host command/workflow call sites. The wheel includes `py.typed` for consumers of this interface.

Unit tests cover conformance/member/profile checks, loader non-mutation and explicit resume state. Existing CLI tests continue covering identity binding, uncertain-outcome/no-retry behavior, private archives, offline imports, source-denied wheel operation and SSH retirement. These contract checks require no physical device.
