# m1n1 build

`build` fetches the pinned Asahi m1n1 revision (`upstream.lock`), applies the
ordered patch layers in `series` and compiles `m1n1.bin` with chainloading
enabled. Git and Podman are required.

```sh
./boot/m1n1/build j71b --output out/m1n1.bin
```

Layers, in order:

- `patches/hoolock/`: Nick Chan's A7-A11 staging tree for upstream m1n1,
  exported verbatim. The base is Hoolock's merge base with Asahi, so base plus
  this layer equals Hoolock's tree (see `sources.toml`).
- `patches/orchard/`: Orchard's loader changes. Board data comes from the ADT,
  never from board compatibles.

When Hoolock lands a commit in Asahi, its patch leaves `patches/hoolock/` with
the next base move. To refresh the layer from a newer Hoolock revision:

```sh
git format-patch --zero-commit --no-signature -o boot/m1n1/patches/hoolock BASE..TIP
```

Leave out CI-only `[DOWNSTREAM]` commits and check that base plus the layer
gives the same tree as Hoolock's tip before updating `sources.toml`.

Scratch stays in ignored `boot/m1n1/.work/<device>/`. `--resume` reuses it only
if the recorded patch set is unchanged.
