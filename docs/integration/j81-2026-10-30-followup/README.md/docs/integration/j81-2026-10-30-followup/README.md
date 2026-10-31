# J81 Omarchy desktop and peripheral follow-up

Date: 2026-10-30. Device: J81 / iPad Air 2 Wi-Fi (iPad5,3). Contributor-run tests on an experimental native Omarchy installation and boot flow; these do not qualify the Orchard release image or J82.

## Confirmed working

| Area | Result | Evidence / limit |
|---|---|---|
| Native Omarchy boot | Reached the Omarchy desktop on the iPad display; desktop responded to mouse and keyboard through the host control path. | Owner-confirmed live test. Later boot attempts sometimes stopped at Linux text without exposing the expected USB endpoint; repeatable cold boot is not established. |
| Display brightness | Brightness slider changed the visible panel brightness. | Owner-confirmed live test. |
| Wi-Fi | Previously qualified on a tethered J81 session; association and network access passed. | See the 2026-10-29 J81 native-root qualification. Not a standalone network configuration test in this session. |
| Battery UI | Battery telemetry appeared. The charge-only gauge reports charge_full in microamp-hours; contributor-side Omarchy change displays it as mAh without inventing a voltage-based Wh value. | Reporting only; no capacity calibration. Change pending a separate Omarchy A PR. |
| Charging indicator | Desktop showed charging when UPower reported external power connected. Contributor-side change preserves unknown when UPower omits the value. | UI only, not input-current/charge-rate evidence or proof of charging from the MacBook USB-C supply. Change pending separate Omarchy A PR. |
| Power profiles | Power Saver, Balanced, and Performance controls exercised; CPU frequency observed between 300 MHz and 1512 MHz; Balanced restored. | No benchmark or device-specific thermal/current tuning claim. |
| Screen power | Helper turned display off and restored it in a separate check. | Does not establish suspend/resume. |

## Still unqualified or failing

- **Touch:** Two temporary touchscreen candidates were tried. Owner tapped and dragged with no desktop response. Not working/qualified.
- **Audio:** Codec identification succeeded, but no ALSA playback/capture stream is qualified. SmartIO DMA stop contract remains unresolved; keep experimental audio DMA disabled.
- **Bluetooth:** Transport/power candidate does not complete HCI reset or pairing.
- **Ambient light:** Both CT819 sensors probe and calibration data loads, but sampled ADC/lux readings remained zero; automatic brightness is off.
- **Charging current:** UI icon is not charge-rate evidence. Do not copy a J71t/D2365 high-current path to J81 without hardware evidence.
- **Repeatable boot:** Latest native boot attempt reached Pongo and sent the payload, but the host did not see the expected Linux USB interface; owner saw Linux text. Latest host session later showed iPadOS and no J81 ECM interface. Earlier desktop boot succeeded, but cold-boot repeatability remains open.
- **Installation:** Earlier contributor experiment mounted a native ext4 root and verified persistence across reboot (see [native-root results](../j81-2026-10-29-followup/NATIVE-ROOT.md)). This is not a stock-like installation flow or release qualification.

## Omarchy A source changes prepared for review

Contributor-side changes address (1) screen remapping that should ignore geometry notifications where only screen size changed, (2) charge_full reporting in mAh when energy_full is unavailable, and (3) preserving unknown external-power state when UPower omits the value. These are desktop/source changes, not new kernel driver support; they are separate from this hardware repository.

## Scope and safety

No internal-storage partition change is part of these UI/peripheral tests. No Wi-Fi credentials or device-specific identifiers are included. J82 remains untested. Treat touch, audio, Bluetooth, ambient light, charging current, suspend, and repeated cold boot as open work.
