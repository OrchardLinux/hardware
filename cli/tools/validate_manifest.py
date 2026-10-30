#!/usr/bin/env python3
"""Offline entrypoint using the installed Orchard manifest implementation."""
from orchard_cli.manifests import (ManifestError, load, local_file, resolve_local,
                                   validate, verify_file, verify_local_receipts, main)

if __name__ == "__main__":
    raise SystemExit(main())
