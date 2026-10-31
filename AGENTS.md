# Notes for coding agents

Several agents and contributors build from different checkouts and worktrees
at the same time. They share one Orchard cache (`~/.cache/orchard`): a source
tree and compiler cache per kernel platform, bare upstream repositories and
built components. `orchard` keeps that sharing safe; working around it breaks
other people's builds.

## Build and run

- Build with `orchard build` from your checkout: `--platform apple-16k`,
  `--device j71b`, or a payload directory. Run on a device with `orchard run`
  (RAM boot) or `orchard launch` (installed system). `orchard` in a checkout
  runs that checkout, including its patches and its m1n1.
- Don't call the producers directly: `kernel/build`, `kernel/build-modules`,
  `boot/m1n1/build` and the other recipe scripts. `orchard build` runs them
  with its cache directories, its lock on the shared kernel tree and the
  switch of that tree between patch series.

## The cache is orchard's

- Never write into `~/.cache/orchard` yourself: no edits in `kernel-work/*`
  trees, no worktrees, branches or commits in the bare repositories under
  `git/`, no index tricks. `orchard build` checks these trees and uses the
  bare repositories to compute and switch patch series.
- Reading is fine.

## Kernel and m1n1 changes

- Changes enter the kernel and m1n1 only as patch files in this repository
  (`kernel/patches/…`, `boot/m1n1/patches/orchard/…`); see CONTRIBUTING.md
  for where they go and how they're written.
- To write or regenerate a patch, use a separate temporary repository of your
  own: the files the patch touches, at the state the series gives them, then
  your change and `git format-patch --zero-commit`. Build the result with
  `orchard build`.

## Devices

- Bound every command you run on a device with a timeout (`timeout -k 5 N …`).
- Coordinate device time with whoever else is testing; one person holds the
  device at a time.
