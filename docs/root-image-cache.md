# Public root-image cache

Installer recovery advertises the optional `root-image-cache-v1` capability.
The shared launcher does not advertise or accept it. Older clients can continue
streaming roots without requesting caching; older recovery images do not gain
this feature until rebuilt with the updated runtime.

The cache is `/.orchard-root-cache` **on the selected Linux-data ext4
filesystem**, beside (not inside) `/omarchy` or another installation directory.
It does not create, resize or repurpose any partition. The directory is owned by
root with mode `0700`; files are root-owned, single-link regular files with mode
`0600`. Symlinks, unexpected names, other filesystems and unsafe metadata fail
closed. Cache operations use no-follow directory descriptors and never recurse.

Only the original public compressed root archive is cached. Private firmware,
ADT, SSH keys and installed-root modifications are not cached. The existing
device-bound private mixin is still freshly prepared and checked on every
installation, including a cache hit.

## Protocol

All operations retain protocol version 1 and the existing device operation
lock, target selection token, inventory checks and storage fencing.

- `destination` accepts optional boolean `cache_root`. When true, it fully
  verifies an exact cache candidate from the recovery's baked root receipt.
  `needed_bytes` includes rounded compressed archive allocation and metadata on
  a miss, but not on a hit. `inodes_sufficient` also includes cache needs.
  `cache_reclaimable_bytes` reports only recognized incomplete cache files that
  can be removed before writing; this is separate from the existing destination
  directory's `reclaimable_bytes` and does not require replacing that directory.
  The previous completed cache archive is **not** counted as reclaimable space.
- `root-cache` accepts the selected `target`, `selection_token`, `directory`,
  and exact trusted `sha256` and integer `bytes`. It mounts read-only and returns
  `{ "cached": true|false, "sha256": "...", "bytes": N }`. No cache is
  created or repaired by this probe. Missing, truncated, oversized or
  hash-mismatched archives are misses; unsafe namespaces are errors.
- `install` accepts optional `root_cache: "reuse"` or `"store"`. `reuse`
  consumes no stdin and rehashes the exact archive on the final writable mount,
  under the operation lock and **before deleting an existing destination**. A
  candidate changed since the probe aborts without deleting that destination.
  `store` performs the normal full transfer while teeing incoming compressed
  bytes into a unique incomplete file. Different images are full transfers;
  there are no deltas or resume protocol.
- Successful cache-aware installation adds `root_cache: "reused"` or
  `"stored"` to its result. Omitting `root_cache` retains the existing streaming
  behavior and does not inspect or modify the cache.

Read-only cache inspection may take time because it hashes the entire file;
clients should allow a suitably long timeout for both `destination` and
`root-cache`. Progress phases distinguish `verifying-root-cache`, `cache-hit`,
`cache-store`, `stream-extraction` and `cached-extraction`.

During cached extraction, protocol-v1 `progress` events carry
`phase: "cached-extraction"`, integer `bytes` and `total` compressed-archive
counts, and boolean `complete`. An initial zero is followed by updates at most
four times per second. The final `bytes == total, complete: true` event is sent
only after archive integrity and filesystem extraction finish successfully;
root policy validation and installation finalization still follow. These are
local extraction counts, not USB traffic. Older clients may log these events
as diagnostics without affecting installation. Streaming installs retain their
existing host-side transfer progress.

## Integrity, interruption and retention

Reuse requires the exact hash **and** compressed byte count from the recovery's
trusted receipt. Cache filenames are only lookup keys, not evidence of trust.
The constrained extractor still checks all compressed bytes and the expanded
size, rejects unsafe archive members, and runs the same public root, privilege
and session checks. A new cache entry is finalized only after those checks pass,
using file fsync, atomic rename and directory fsync. Private mixins are applied
afterward and remain subject to their existing checks.

Normally one completed archive is retained, plus at most one in-flight store.
The previous completed archive remains until the new public archive validates.
After publication, older completed entries are removed. A power loss between
rename and pruning can leave two completed entries; the next writer collapses
this back to one before starting another transfer. Caught failures remove their
incomplete file. Cold-boot incomplete files are never hits and are safely removed
before the next cache-aware install writes its new root. Discovery is bounded
and rejects excessive or unknown entries rather than deleting them speculatively.

Byte and inode availability are rechecked on the writable mount after journal
replay and immediately before replacement deletion, then again after deletion.
Cache storage must fit alongside the new extracted root and any retained old
archive. Streaming misses keep the existing concurrent transfer/extraction
workflow: a later transfer or extraction failure can still occur after an old
root has been deleted. This feature is not a rollback system.

## Verification

`python -m unittest discover -s tests -v` covers cache helpers, real gzip/tar
extraction over temporary files with mocked storage, cold reopen/replacement,
hash/size mismatches, unsafe namespaces, interrupted stores, bounded retention,
space/inode budgets, private mixin isolation, old-client behavior, locking, and
wheel/runtime integrity staging. These tests do **not** qualify physical iPad
storage fencing, power-cut recovery, flash performance or boot behavior; those
still need the normal supported-hardware qualification before release.
