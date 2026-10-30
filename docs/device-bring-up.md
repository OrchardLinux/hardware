# Device bring-up

Bring an unknown device, reuse the closest port, attempt a RAM boot, and contribute what you learn. A boot log or partial port is enough for a PR. A desktop and installer can wait; no distribution is needed.

## 1. Set up

```sh
git clone https://github.com/OrchardLinux/orchard
cd orchard
ln -sf "$PWD/bin/orchard" ~/.local/bin/orchard   # once; needs uv
git worktree add ../orchard-BOARD -b BOARD       # your bring-up branch
cd ../orchard-BOARD
orchard --version
```

See [BUILD.md](../BUILD.md) for compiler/container dependencies. `orchard` runs the checkout you are in, so this worktree's patches and device data are what you build; see the [README](../README.md#develop).

Charge the device and build artifacts before entering DFU.

## 2. Find a starting point

Connect the device in DFU and inspect it:

```sh
orchard devices
```

Record the `chip` and `board` fields. Discovery lists unknown devices too. The USB `product` name may just say DFU, not the model.

Get the model from Settings → General → About or the case's model number. For the Apple product identifier, such as `iPad7,5`, run `ideviceinfo -k ProductType` with iOS running and the computer trusted. That optional tool comes from libimobiledevice. Missing a model name is not a reason to stop experimenting.

- Find an existing kernel/DTB for that board or the closest SoC. Keep its source revision, patches and licenses.
- Keep any working boot tools and payloads. Change one thing per attempt.
- Do not copy another board's GPIO, regulator or charging writes blindly.

## 3. Build and attempt a RAM boot

```sh
cd examples/minimal-boot
orchard build --device BOARD
orchard run
# Without a prompt:
orchard run --yes
```

To keep your own experiment, copy an example anywhere (`cp -r examples/minimal-boot ~/board-tests`); with an `orchard.local.toml` naming this worktree (`orchard = "/path/to/orchard-BOARD"`, `device = "BOARD"`), `orchard build` there uses it.

The reference kernel/DTB is a starting experiment, not a guarantee it fits your board.

An experiment contains `init` and optional `rootfs/`. Put your startup and diagnostics there. No installer or device-side RPC is needed.

Reuse compiled components. Changing `init` should not rebuild Linux. The kernel tree and compiler cache in `~/.cache/orchard/` are shared by every checkout; check `orchard cache` before starting another full compile.

## 4. Add your board's recipe

In your worktree:

| Path | Add |
| --- | --- |
| `devices/BOARD/device.toml` | Model name, product identifiers, chip/board IDs and compatible strings. |
| `kernel/configs/BOARD/config` | Kernel configuration. |
| `kernel/devicetree/BOARD/build` | Commands producing the final DTB. Accepts `--kernel-build`, `--headers`, `--output`. |
| `kernel/patches/BOARD/` | Kernel patches, if needed. |
| `kernel/modules/BOARD/` | External modules, if needed. |
| `boot/m1n1/patches/BOARD/` | Applicable loader patches when using the m1n1 producer. |

Use J71b's files as format examples, not hardware settings to copy wholesale. See the [component API](ram-development.md) to build a named recipe or assemble explicit kernel/DTB/loader inputs.

## 5. Find where it stops

| Observation | Check next |
| --- | --- |
| Still in DFU | Boot-chain tool output and USB transport. |
| Pongo runs, no kernel output | Payload, loader, DTB and console routing. |
| Kernel hangs or panics | Last messages, config, DTB and failing driver. |
| Kernel runs, no init | Initramfs, executable `/init` and boot arguments. |
| Init runs, no USB shell | Controller driver, gadget setup and host serial endpoint. |

Save commands, revisions and logs for each attempt. Successful upload does not prove Linux booted. Missing USB serial does not prove it failed. Inspect an uncertain result before retrying.

## 6. Send the PR

Include:

- Exact device tested and how to reproduce the build/boot.
- Source revisions, patch attribution and relevant logs.
- Furthest boot stage; working, broken and untested hardware.
- Any shared changes that could affect existing ports.

Keep firmware bytes, calibration, raw ADT captures, ECIDs and credentials out of public commits/logs. Contribute firmware recipes instead.

Hardware data, recipes and host tooling are one PR here; see [CONTRIBUTING.md](../CONTRIBUTING.md) for how to split topics. Distribution changes go to the distribution.

Run the tests (`orchard python -m unittest discover -s tests`). Report physical boot results separately from compilation/test results.
