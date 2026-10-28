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

## Target layout

```
kernel/upstream.lock        torvalds tag (shallow; v7.3-rc1 now)
kernel/patches/
  hoolock/                  Nick Chan's series, pinned to 6831bc70; drop as it lands upstream
  pauli1go/                 Paul Praschl's A10 series (J172, D10/D11/D111), authorship kept
  orchard/                  our changes, one series per subsystem
  <device>/                 device DT and quirks only (j71b, later j71t, j172, …)
```

The build applies the shared series, then the device's own, in order.

## Steps (each verified before the next)

1. **Hoolock series. ✅ Done.** Hoolock has no tags, so its commits after
   `v7.3-rc1` were fetched with `--shallow-since` into the torvalds entry of
   Orchard's shared git cache (+1 MB). `tools/linearize-merges.py` replays each
   `bits/*` merge in order (3-way, in the bare repo) and exports
   `patches/hoolock/<branch>/`: 98 patches. One duplicate commit present in two
   branches is dropped.
   **Verified:** tree(`v7.3-rc1` + hoolock) == tree(`6831bc70`) == `445bf947`.
2. **Pauli1Go series.** Fetch Paul's branch the same way, rebase his non-Hoolock
   commits onto step 1, and export them.
   **Check:** each commit applies; record which `0001` hunks it reproduces.
3. **Split `0001`.** Our tree = step 2 + remaining hunks, grouped by subsystem
   (NVMe/ANS, PCIe/pwrctrl, SIO DMA, touch, HDQ/serdev, J71b DT/readiness).
   **Check:** tree(final stack) == tree(`6831bc70` + current j71b patches).
   That makes the migration a proven no-op for the running kernel.
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
