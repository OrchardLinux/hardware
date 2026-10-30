# Device protocol v1

The recovery exposes `orchard-device` with three operations. Installing does not
go through it: the CLI copies its own install script
(`src/orchard_cli/install_device.py`) to the recovery and runs it over SSH, so
the host and device side of an install always come from the same CLI version.

## Ownership

- Host: device confirmation, USB boot transport, release and cache resolution,
  Apple firmware acquisition, target selection, password and PIN prompts, the
  install script and its plan.
- Recovery: its live device and boot identity, SSH key authorization, and
  bringing internal storage up read-only at boot (`/run/orchard/storage-ready`).
- Private bundle: exactly the device firmware data plus `mixin.json`; the
  install script writes it to the installed root's `/usr/lib/firmware`.

## Operations

`orchard-device OPERATION [BASE64_JSON]`; requests may include `protocol: 1`.

| Operation | Request | Result |
|---|---|---|
| `bootstrap` | public key, profile, ECID, boot ID (over USB serial) | Authorizes the key in RAM, starts the SSH daemon, returns the pinned host key and endpoint. |
| `status` | none | Release, profile, ECID and boot ID. |
| `start` | root and boot partition PARTUUIDs | Right after an install: PID 1 switches into that root if its boot entry boots the running kernel, so the first start needs no second DFU boot. A failed check leaves the recovery running, with the reason in `/run/orchard/start.log`. |

Stdout is newline-delimited JSON with `protocol: 1`: one `result`, or an
`error` with `retry: false`.

## Install script

`install_device.py inspect` reports the partition layout as a `layout` event.
`install_device.py run PLAN.json < ROOTFS` checks the live table against the
plan, writes only what the plan names and reports each step as a JSON event,
also in `/run/orchard/install.log`. A lost connection means an unknown
outcome: run `orchard install` again, which starts from a fresh `inspect`.
