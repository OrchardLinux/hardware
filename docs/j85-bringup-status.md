# iPad mini 2 Wi-Fi (J85): native Linux bring-up status

This is a field report for the iPad mini 2 Wi-Fi (`iPad4,4`, A1489, J85,
Apple A7/S5L8960X). It records experiments on one physical device through
27 October 2026. It is not an Orchard device profile, kernel producer,
firmware recipe, launcher, or installation procedure. The [supported hardware](../README.md#supported-hardware)
list is unchanged.

## Measured on the J85 device

| Function | Observation | Boundary |
| --- | --- | --- |
| Boot | A tethered DFU/checkm8/PongoOS handoff booted an AArch64 Linux 7.3.0-rc1 test kernel into RAM. A second, versioned RAM image (`ipad-mini2-touch4-gpu-runtime9`) booted on 27 October and was identified over USB and Wi-Fi SSH. | No internal storage or stock bootloader write. Autonomous or installed boot has not been shown. The private RAM bundle is not a reproducible Orchard build. |
| Display | The 1536 × 2048 panel showed a CPU-rendered desktop using the loader-provided framebuffer and `simpledrm`. | There is no native display-pipe driver or verified GPU acceleration. A `simpledrm` card is not a GPU render node. |
| Input | Home, Power, volume and mute generated Linux input events. On a 24 October `touch4` RAM boot, the owner confirmed aligned one- and two-finger input; the evdev capture contained 1,592 records, four press/release cycles, and five tracking starts/ends. | The touch firmware and panel calibration came from private owner-side inputs. Cold-boot autoload and a packaged Orchard touch runtime have not been validated. |
| Wireless | On the 27 October `runtime9` RAM boot, the Broadcom device bound to `brcmfmac`, `wlan0` appeared, and Wi-Fi SSH responded after DHCP. | Credentials and firmware remain private. This does not qualify prolonged radio reliability or a clean Orchard install. |
| USB | The initial RAM boot exposed serial and USB networking and answered host pings. | Later handoffs were intermittent. USB/NBD root operation is not qualified for this profile. |
| Battery and charging | With the owner-confirmed Apple A1357 10 W wall adapter, bounded RAM-only 1 A input-limit trials produced positive gauge current during 16 timed cycles on one `runtime9` boot. The one-shot module rolled back and the earlier 500 mA supervisor resumed. | The gauge remained at 48% during that window. Source detection, sustained state-of-charge gain, computer-USB safety and an integrated charger driver are unverified. These measurements are not a general input-current recommendation. |

GPU work has reached only resource and power prerequisites. A short framework-owned GFX power transition returned to OFF on the `runtime9` boot. No GPU core identity, firmware handshake, submitted job, accelerated frame, or matching DRM/Mesa path has been demonstrated. Audio, Bluetooth, cameras and installed-root boot are also unqualified.

## Why this is not a device profile yet

The test kernel started from the same Hoolock/Orchard Linux source revision
recorded in [`kernel/upstream.lock`](../kernel/upstream.lock), but the J85
kernel/configuration, boot assembly and peripheral modules are local work.
They have not been isolated into a clean, redistributable patch series against
Orchard's pinned producer. The RAM bundle also needs owner-supplied Apple
firmware, panel calibration and network credentials. The J71b firmware recipe,
device tree and launcher do not apply to J85. P105's A5 boot and SGX data do
not apply to this A7 board either.

A useful follow-up would add a source-pinned J85 kernel/boot recipe, explicit
private-input gates, a manual RAM-boot profile and package tests. It should
build from a fresh checkout before any Orchard CLI adapter or release claim.
Each peripheral needs a separate physical test on that build. GPU support in
particular needs an actual rendered frame through a GPU DRM path.

No recovered Apple firmware, panel calibration, captured device tree, private
logs, device credentials or boot bundle is included here. The observations
above are a support boundary for contributors, not a recipe to apply untested
settings to another iPad.
