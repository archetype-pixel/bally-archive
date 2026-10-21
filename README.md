# Bally Archive

Shared Bally-controlled source repository for the heritage archive replacement.

## Current code

`reference/local-archive/` contains Codex's working local archive application and
ingestion tools: search, facets, catalogue and inventory browsing, editorial CMS,
file delivery, source provenance, checksums, and backup/restore verification.
It is a reference implementation, not a production web service. It binds to
loopback, has no hosted identity enforcement, and must not be exposed publicly.

Run with Python 3.9 or later:

```sh
cd reference/local-archive
python3 server.py
```

Open http://127.0.0.1:8765. The repository intentionally starts with an empty
catalogue; preserved archive records and originals remain outside Git.
Workbook-preview and image-verification utilities may use Pillow; the core
application uses the Python standard library.

`reference/360-viewer/index.html` preserves the independent viewer implementation.
Its media and provenance manifests stay outside Git.

## Shared system

- Supabase project: `glfiqywdzqcnrhpsjwwp` (Bally Archive).
- Metadata staging currently exists in that project; do not replace it with a
  second database or overwrite it from the local reference archive.
- The owner reports all archive photos are in Azure. Reconcile the Azure file
  manifest with source asset GUIDs before deciding which additional downloads or
  storage transfers are needed.
- See [the coordination handoff](docs/COORDINATION.md) for observed tables,
  division of work, integration requirements, and remaining acceptance checks.

## Reconciliation

Export the local verified-file register without modifying its database:

```sh
python3 tools/export_verified_originals.py \
  --database /path/to/archive.sqlite3 \
  --output reconciliation-output/local-originals.json
```

This reads existing verification records; it does not rehash files or certify an
Azure backup. Keep its output private and outside source control.

Run the portable checks with `python3 -m unittest discover -s tests`.
