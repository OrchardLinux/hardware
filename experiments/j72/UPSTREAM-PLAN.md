# Implementation contribution after the lab code release

PR #43 is held in draft at the owner's request. It now contains this development implementation and its report;
it does not yet boot J72 through Orchard. The sources in this directory make the working development
path available for review and reuse now.

## Required production form

1. **S5L SPI:** port the measured explicit-transfer policy to Orchard's
   current `spi-apple` match-data design, add the compatible's binding and
   controller data, and keep existing T7000/S8000/T8010 behavior intact.
   Record the source commits and preserve authorship. The preserved local
   baseline targets Linux 7.0.12; current Orchard targets 7.3-rc1.
2. **Touch:** express J72 N1 personality and packet/layout differences as
   `apple_z2` match data. Add the binding and board DT. Model the shared
   analog supply, PWM clock, reset, attention, chip select and display sync
   through their owning drivers instead of direct overlay MMIO. Keep
   checksummed framing, bounded recovery and release-on-timeout behavior.
3. **Private-input recipe:** add the model firmware converter/recipe and
   calibration forwarding through the supported boot path. Validate with
   owner-local inputs; no embedded packet arrays or captured DT dump in
   the PR. The current include renderer deliberately does not implement
   firmware extraction or claim recipe completeness.
4. **Battery:** retain the tested UART protocol and restricted identity
   operations, but replace machine checks/provisional matching with
   bindings and compatible-selected data. Carry serial API and battery
   changes in logical commits, preserve original authors, resolve required
   options in both platform configs and explain every config change.
   Capacity pairing needs a data-driven quirk rather than a board check in
   the shared getter.
5. **Device and host path:** provide a J72 descriptor with features limited
   to what the submitted build demonstrates. Integrate the A7 boot method
   and required initramfs behavior. Leave installation disabled. Submit
   desktop/keyboard integration separately to omarchy-a with dependencies
   and merge order stated.
6. **Qualification:** apply/build the exact patch series, run Orchard's
   suite, then boot that exact revision on J72. Test physical touch,
   display, Home, battery and clean recovery; identify other devices using
   each shared driver/config and arrange their regression checks before
   merge. The old lab image does not qualify a port to the newer stack.

## Charging remains experimental

The shared 425 mA worker is a recorded development experiment, not a charger
driver. Source identification, protections, sustainable net charging,
USB reconnect behavior and a desktop regression remain qualification work.
The prepared 850 mA test stays separate from the implementation PR until
its physical result exists. No charging success or internal-storage
installation is implied by publishing the source.
