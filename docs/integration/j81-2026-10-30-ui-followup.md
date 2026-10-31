# J81 Omarchy UI and peripheral follow-up

Date: 2026-10-30. Device: J81 (iPad Air 2 Wi-Fi). Contributor-run observations from an experimental native Omarchy installation and tethered boot flow. These do not qualify the Orchard release image or J82.

## Confirmed working

- **Desktop:** The owner confirmed Omarchy reached the iPad display and responded to mouse/keyboard through the host control path. A later boot attempt stopped at Linux text after payload transfer, without the expected Linux USB interface; iPadOS returned. Desktop boot is observed, but repeatable cold boot is not qualified.
- **Brightness:** The desktop brightness slider visibly changed panel brightness.
- **Wi-Fi:** Association and network access passed in the earlier tethered J81 native-root qualification documented above.
- **Battery UI:** Battery telemetry appeared. The charge-only gauge provides charge_full in microamp-hours; Omarchy's contributor patch displays it as mAh when energy_full is absent, without inventing Wh from a nominal voltage. This is reporting, not capacity calibration.
- **Charging indication:** The desktop showed the charging icon when UPower reported external power connected. The contributor patch preserves unknown if UPower omits the property. This is UI evidence only; charging current/rate and charging from the MacBook supply were not verified.
- **Power profiles:** Power Saver, Balanced and Performance were exercised. CPU frequency was observed from 300 MHz to 1512 MHz, then Balanced was restored. This is not a benchmark or hardware limit qualification.
- **Display power:** The screen-power helper turned the display off and restored it. This does not establish suspend/resume.
- **Native root persistence:** The previous contributor experiment booted a native ext4 root and verified persistence across reboot; see [native-root results](j81-2026-10-29-followup/NATIVE-ROOT.md). This is not a stock-like installation flow or release qualification.

## Still unqualified or failing

- **Touch:** Two temporary touch candidates were tested; the owner reported no response to taps or drags.
- **Audio:** Codec identification succeeded, but no ALSA playback/capture stream is qualified. Keep the experimental SmartIO DMA path disabled while its stop contract is unresolved.
- **Bluetooth:** No successful HCI reset or pairing.
- **Ambient light:** Both CT819 sensors probe and calibration loads, but ADC/lux remained zero; automatic brightness is off.
- **Charging current:** No current/rate qualification. Do not port the J71t/D2365 high-current path to J81 without J81 evidence.
- **Repeatable boot:** Open; a later cold boot did not repeat the desktop result and the host subsequently showed iPadOS with no J81 ECM interface.
- **Suspend and J82:** Untested.

## Omarchy A source changes proposed separately

The corresponding UI/source changes are in the separate [Omarchy A PR #1](https://github.com/micro23/omarchy-a/pull/1): report charge_full as mAh, avoid remapping screen surfaces when only geometry size changes, and preserve an unknown external-power state through the power panel. These are UI/reporting changes, not new kernel-driver support.

No Wi-Fi credentials or unique device identifiers are included. No internal-storage partition changes were part of these UI/peripheral tests.
