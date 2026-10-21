# Bally Heritage replacement workspace

This is a working private local migration workspace and archive application, not a completed retirement of CHALCO.

## Open

Run `python3 server.py` from this directory, then open http://127.0.0.1:8765.
The server binds only to this computer. It has no external dependencies and performs no requests or writes to CHALCO. Do not expose this local server directly to the internet. Hosted deployment, identity, private object delivery and access policy must be implemented and reviewed before hosting.

## Current implementation

- Search source metadata and object codes; filter category, decade and gender.
- Paginated object catalogue; image galleries; source URLs and identifiers; tags and inventory / linked-item / collection relationships.
- Durable SQLite records, raw metadata, editorial fields and before/after audit trail.
- CMS editor for titles, category, dates, tags, inventory numbers, linked items, collections and notes.
- XLSX, CSV and JSON ingestion: validation before commit, invalid-batch rejection, unchanged-record detection, raw export retention and SHA-256 checksums.
- Embedded-workbook preview recovery with explicit Excel row → object-code → media relationships. These are derivatives, never marked as originals.
- Original-file ingestion with path validation, SHA-256 checks, content-addressed storage, duplicate-content reporting and object-to-file relationships.
- Portable record JSON exports and consistent local backup script.

## Authoritative evidence

`data/browser-evidence.json` retains exact category URLs and observed counts, sample source text and browser asset transfer manifests. `data/inventory.json` is the discovery inventory. `data/imports/` retains exact imported export bytes by SHA-256. `data/official-*.xlsx` retains named source exports. `data/*-previews.json` records every extracted preview and its workbook member, row, checksum and object relationship.

The user states sole ownership of Bally, its IP and this system. Ownership authority is recorded in pilot metadata. Source copyright and author fields are retained as catalogue facts.

## Metadata import

Validate first:

```sh
python3 server.py --import data/official-inventory-full.xlsx 'https://ballyheritage.com/DigProd/PRJSearch/Search/64?assetKey=heritageOBJECT&buttonKey=browseHeritageObjects'
```

Commit by adding `--commit`. The command-line importer accepts up to 256 MB; the browser upload limit is 32 MB. XLSX expansion is limited to 250 MB. Header-only exports are rejected. Import summaries must match the source result count before being considered complete.

Recover preview images after importing rows:

```sh
python3 extract_workbook_previews.py data/official-inventory-full.xlsx
```

## Original-file ingestion

Create a manifest with an export root and per-file `record_id`, relative `path`, `filename`, `source_guid`, `source_url`, and optional expected `sha256`. Run `python3 ingest_originals.py manifest.json` to validate and hash, then repeat with `--commit`. Files are copied, never moved or deleted. Original byte hashes and source GUIDs remain distinct: identical files may legitimately belong to multiple source assets.

## Backups

`python3 backup.py` creates a consistent SQLite snapshot, image and original files, imported export bytes and a checksum manifest in a timestamped archive beside this folder. Source code and evidence should also be version-controlled in a Bally-owned repository. Restoration should be tested before cutover.

## Remaining work before retiring the source

1. Complete inventory and non-inventory exports, then inventory-number records and all location / availability fields.
2. Export all media and recover original filenames, bytes, GUIDs, versions, object/inventory links and file roles. A thumbnail export does not replace original TIFFs, documents, films or 360° packages.
3. Recover collection memberships, linked-item edges, taxonomy identifiers and vocabulary hierarchies, attachments and historical versions.
4. Inventory source users/roles, workgroups, workflows, tasks/projects, integrations, export templates, audit logs and shooting-solution scope. These are not yet replicated.
5. Reconcile object totals, category membership, inventories, media counts and byte hashes. Flag differences, orphan links and duplicates; never silently merge records.
6. Add full object creation, original upload/download UI, taxonomy and collection administration, media playback and 360° viewing, version history and authorized identity / roles.
7. Prepare a private hosted service in a Bally-controlled account with database backups, private object storage, signed media delivery, operational monitoring and tested restore.
8. Obtain explicit hosting approval; run acceptance checks and agree a cutover point. Retain a verified full source backup and rollback path.

## Constraints and discoveries

The current backend is CHALCO Brand Life Suite. Browser authentication works. The inventory-object search exposes official Excel export and all-result selection; several other actions are disabled when their bulk-item limit is exceeded. The media search exposes per-file and selected downloads. The exact media-download batch limit is not yet established. The first single-record export returned only headers; the subsequent full inventory export returned exactly 12,282 rows, showing why row-count validation is required.

The frontend shows eight category totals summing to 17,221. Backend inventory and non-inventory object totals are 12,282 + 4,951 = 17,233, a difference of 12 that must be explained. Digital assets are a separate unit: 75,360 observed in the backend.

Chalco's vendor confirms an API layer: https://www.hyphen-group.com/en/integrated-digital-content-hub-chalco-brandlife/ . Live Story's integration documentation describes token access configured using an App ID, DAM API URL and application user credentials: https://www.livestory.io/documentation/articles/chalco/ . Those documents do not establish Bally's actual API endpoint or credentials. The user selected browser exports for now. No guessed endpoints or copied browser credentials are used.
