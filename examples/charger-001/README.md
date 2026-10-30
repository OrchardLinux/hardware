# charger-001

A RAM-only J71b environment with a manually invoked charger observation tool.
Everything that runs on the device is visible here:

```text
init                         # Device startup and USB serial shell
rootfs/
├── etc/motd                  # Manual test instructions
└── usr/bin/charger-test      # The actual observation script
```

```sh
cd examples/charger-001
orchard build --device j71b
orchard run
```

The CLI packages `init` and `rootfs/` directly; there are no Python build/run
wrappers. Host prerequisites and serial access are in [minimal-boot](../minimal-boot/).
The root launcher selects the editable CLI automatically; an activated workspace
terminal can also use bare `orchard`.

Only run when the iPad is released from other experiments. At the USB serial prompt:

```sh
charger-test
```

It records ten bounded, read-only power-supply snapshots under `/run/`. Missing
telemetry is reported rather than worked around by loading drivers. Save logs
through the terminal before rebooting; they live only in RAM.

## Edit and repeat

- Edit `init` to change device startup.
- Edit `rootfs/usr/bin/charger-test` for the experiment; retain its executable bit.
- Add files under `rootfs/` at the paths where they should appear on the device.
- Host tooling lives in `src/orchard/`: `build.py` builds/caches components,
  `ram.py` composes RAM payloads, and `boot.py` confirms/uploads them.
- Kernel/driver changes are patches in `kernel/patches/` or modules in
  `kernel/modules/`; the shared kernel tree applies them on the next build.

No Linux partition, distro image or release is needed; the orchard checkout you
work in is editable as it is. Upstream Git objects, built components, the kernel
tree and the compiler cache are shared across checkouts under `~/.cache/orchard/`
(or the configured cache root). Identical inputs reuse the kernel without another
checkout or compilation.

External charger-driver edits rebuild the device layer against the cached kernel's
matching SDK, not upstream Linux. Config/patch/in-tree edits select a new kernel
base, reusing local incremental compiler output where available. Experiment-only
edits just repack the payload. The first build after this cache-format change
populates the shared base/SDK; prior outputs and compiler state are not deleted.

Modules are now explicitly opt-in for manual driver investigation:

```sh
orchard build --modules
```

This includes matching modules but does not load them. The observation script
itself does not require staging modules. The original already-built payload still
contains them and remains usable.

Optional `--kernel-dir DIR --busybox FILE --loader FILE --pongo FILE` reuses local
components; `--cache DIR` overrides the cache. `run` uses the last successful build,
not a hidden rebuild. Rebuild after changing kernel inputs.

## Safety and status

The original charger payload booted on J71b. The current extraction is only
offline-tested, not newly hardware-qualified. No charger policy, leases, current
limit writes, arbitrary register probing, storage mounts or tests start
from the supplied `init`.

The serial shell is **unauthenticated root over physical USB**. Use a trusted host
and dedicated device. Prototype PID `05ac:4144` is not the recovery/discovery RPC
endpoint. No new protocol is required.

This is a read-only observation baseline, not the advanced charging research.
Loading a driver can itself mutate hardware. Review replacement experiments and
obtain explicit authorization for elevated current, source swaps or suspend tests.
RAM boot does not mean kernel initialization has no hardware side effects.

## Offline tests

```sh
orchard python -m unittest discover -s tests -p test_ram.py -v
```

Tests use synthetic inputs and mocked USB, not the iPad.
