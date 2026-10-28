# Kernel base migration: Hoolock fork → torvalds + patch series

## Goal

Build every device from a torvalds/linux tag plus ordered patch series with clear
provenance, instead of a fork plus one large per-device patch. This lets Orchard
absorb other projects' work, share drivers between devices, and upstream cleanly.

## Today

- `upstream.lock`: OrchardLinux/linux mirror of HoolockLinux/linux `6831bc70`
  ("Merge branch 'bits/090-dart' into hoolock"), based on torvalds `v7.3-rc1`.
- `patches/j71b/0001-t8010-j71b-device-support.patch` (336 KB, 47 files) contains
  Pauli1Go's A10 work (J172 / iPhone 7) plus Orchard's J71b fixes and J71b-only parts.
- Measured: torvalds `v7.3-rc1` → Hoolock `6831bc70` is 104 files, +6639/−178,
  almost entirely additive Apple drivers, bindings and DTs.

## Target layout: one stack, many contributors

Orchard integrates every Linux-on-iOS-device effort (all checkm8 devices and
beyond) as contributors to one shared stack:

- Hoolock (Nick Chan): the staging-upstream layer;
- Pauli1Go (Paul Praschl): iPad 7, iPhone 7/7 Plus;
- henrikklee: iPad 6;
- brkosebay (Berkay Kösebay): iPad 5;
- and further repos as they are found.

Series are organized by layer and subsystem, not by contributor, because
contributions interleave (fixes land in other people's drivers). Authorship
stays in each patch (`From:`, `Co-developed-by:`), as in mainline.

```
kernel/upstream.lock          torvalds tag (shallow)
kernel/sources.toml           every absorbed repo: url, pinned commit, contributor,
                              devices, license, status (found/reviewed/integrated/upstreamed)
kernel/patches/
  hoolock/<bits>/             staging-upstream layer, hash-verified against Hoolock
  shared/<subsystem>/         A9/A10 drivers, any author (nvme-ans, pcie-h9p, sio,
                              touch, hdq-battery, charging, …)
  <device>/                   board DT and quirks only (j71b, j172, d10-d11, j71t, …)
CONTRIBUTORS.md               generated from sources.toml and patch authors
```

The build applies hoolock, then shared, then the device's own series.

Importing a new repo: add it to `sources.toml`, fetch its commits shallowly
into the shared cache, replay them onto the stack, then run the semantic review
(step 3) to decide shared vs device-specific vs drop.

## Steps (each verified before the next)

1. **Hoolock series. ✅ Done.** Hoolock has no tags, so its commits after
   `v7.3-rc1` were fetched with `--shallow-since` into the torvalds entry of
   Orchard's shared git cache (+1 MB). `tools/linearize-merges.py` replays each
   `bits/*` merge in order (3-way, in the bare repo) and exports
   `patches/hoolock/<branch>/`: 98 patches. One duplicate commit present in two
   branches is dropped.
   **Verified:** tree(`v7.3-rc1` + hoolock) == tree(`6831bc70`) == `445bf947`.
2. **Pauli1Go series. ✅ Imported.** Paul Praschl's 117 kernel commits (README and
   photo commits skipped) were replayed onto step 1 with `tools/replay-commits.py`
   into `patches/import/pauli1go/`, keeping his authorship. 113 applied 3-way
   unchanged. 4 conflicted with mainline changes since his base and were ported
   by hand; each is marked `[Orchard: ...]`:
   - `nvme-pci: Add Apple ANS FlatDMA support`: structs moved to `pci-internal.h`
     keep mainline's `__guarded_by` annotations; `CONTEXT_ANALYSIS` stays on.
   - three `tsl2583` commits: mainline removed `tsl2583_set_pm_runtime_busy()`;
     Paul's paths use the direct `pm_runtime_*` calls instead.

   **Verified:** for each of Paul's files, the replay equals a whole-file 3-way
   merge, except the three hand-ported files and the skipped README.
   torvalds + hoolock + pauli1go (215 patches) applies to exactly the replayed tree.
3. **Semantic review of Orchard's delta.** Our current tree compared with Paul's
   latest, over the files `0001` touches:
   - 15 files identical to Paul's (no Orchard delta);
   - ~480 lines of J71b-only additions (DTs, readiness touch driver, NVMe
     read-only policy) → `patches/j71b/`;
   - ~550 lines of real changes to Paul's drivers (DART, NVMe/ANS, H9P PCIe,
     SIO core, pinctrl, bq27xxx HDQ, samsung UART, serdev, Kconfig/Makefile).

   Our code ran on J71b but is not assumed correct: it may carry copies older
   than Paul's or mainline's, or workarounds. Each hunk is compared with Paul's
   latest and current mainline/Hoolock, and gets one verdict in `REVIEW.md`:
   J71b-specific / real fix (own `orchard/` commit with rationale, offered
   upstream to Paul) / older copy or regression (drop) / workaround (drop or flag).

   Also evaluated: Paul's pieces we don't use yet (`i2c-pasemi` T8010 recovery,
   in-tree `gpio-apple-d2365`, Bluetooth, NVMEM Wi-Fi calibration, D2365
   charger, Bellatrix source), adopted where they replace ours.

   **Check:** no longer tree equality, because changes are intended. Instead:
   kernel builds, then a J71b RAM-boot checklist over Wi-Fi SSH (storage fence,
   touch, Wi-Fi, Bluetooth, battery, charging, backlight) against today's kernel.
   The current stack stays as the fallback until this passes.
4. **Build switch.** `kernel/build` applies `hoolock/`, `pauli1go/`, `orchard/`,
   then `<device>/`. `upstream.lock` points at torvalds. Rebuild and compare
   `Image.gz`/modules with the cached build; boot once on J71b.
5. **Integrate further devices** onto the same stack: J71t (hardware#6), J172 and
   iPhone 7 profiles (Pauli1Go), then charging as shared drivers (D2365 charger,
   CBTL1610 source, PMIC event IRQ chip). P105/iPad mini 1 (A5, arm32) needs arm32
   tooling support first and is a separate track.

## Maintenance

- Builds stay shallow: one tag per cache entry, keyed by URL in `~/.cache/orchard/git`.
- A kernel bump fetches only the new release range (`--shallow-exclude=<old tag>`),
  rebases the series and uses patch-ids to drop patches that landed upstream.
- No full torvalds clone is needed.

## Notes

- Never check out Linux on macOS for this work. The tree has case-only filename
  pairs (`xt_CONNMARK.h`/`xt_connmark.h`) that a case-insensitive filesystem
  corrupts. Use bare-repo operations (`merge-tree`, `apply --cached`).
- Rerun step 1 on a Hoolock update:
  `kernel/tools/linearize-merges.py --repo <cache> --base <torvalds tag commit> --tip <hoolock> --output kernel/patches/hoolock`
