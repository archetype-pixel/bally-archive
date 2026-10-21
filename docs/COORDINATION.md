# Shared implementation handoff

## Ownership and coordination

The owner has asked Claude and Codex to collaborate in this private repository
and the existing Bally Archive Supabase project. Claude's active browser sessions
and extraction jobs must not be interrupted. Use pull requests and shared schema
documentation for integration; do not assume locally recovered counts describe
Claude's work or the Azure backup.

Claude's existing source-code capture, extraction workers, schema, and normalized
export contract have not yet been committed to this repository. The repository
contained only its initial README when inspected. Please commit those materials
or their private source locations, with secrets and media excluded, so the team
can build from the same code and data contract.

## Observed Supabase staging contract

Read-only dashboard inspection confirmed project `glfiqywdzqcnrhpsjwwp` is healthy.
The following public-schema tables exist:

| Table | Observed columns / purpose |
| --- | --- |
| staging_records | id int8, search_key text, page int4, asset_id int8, guid text, data jsonb, created_at timestamptz |
| staging_blobs | Exists; contents and contract not yet inspected |
| file_copy | asset_id int8, guid text, name text, binary_update text, azure_path text, azure_size int8, match_note text, status text |
| azure_files | path text, dir text, name text, size int8, listed_at timestamptz |
| azure_dirs_done | Exists; contents and contract not yet inspected |

The staging_records dashboard estimated 110.4K rows; file_copy estimated 75.4K.
These are mixed staging rows and estimates, not certified complete object or file
counts. The first visible file_copy rows were pending with null Azure matches.
The azure_files view showed zero rows at inspection. That does not establish that
Azure is empty: the owner reports all photos have been copied. Identify the real
Azure account/container and completed manifest before duplicating that work.

The Supabase dashboard reports no repository connected. Linking the repository
to Supabase branching is separate from using Supabase as the application database;
do not grant new integrations or broaden permissions without owner approval.

## Codex deliverables now in Git

- Local catalogue/CMS, search and facets, galleries, inventory and collection views.
- Source-export importers, raw metadata preservation, asset relationship ingestion.
- ZIP and individual-original verification, SHA-256 identity and provenance.
- Consistent SQLite backup and restore-verification tools.
- Independent photo-spin viewer code.

No original archive files, source database, source exports, browser credentials,
screenshots, or secret keys are included. The empty fixtures allow a fresh local
startup. Production identity and access control are not implemented by this local
reference server.

## Integration approach

1. Preserve immutable staging evidence and Claude's source IDs. Do not overwrite
   raw staging with edited catalogue data.
2. Reconcile using entity type plus source GUID; a GUID alone must not conflate
   object, inventory, media, and package identities. Preserve numeric asset IDs.
3. Join photos using source GUID/path provenance first. Exact filenames provide
   candidates, but duplicate names require sizes, hashes, version information or
   other evidence. Ambiguous matches remain unresolved.
4. Separate source asset identities from file-content hashes: multiple assets can
   reference the same byte-identical file. Keep original names and relationships.
5. Use one Supabase project for normalized application metadata, authentication
   and access rules. Keep Azure originals in Bally-controlled storage unless an
   explicit operational reason justifies moving them. Use a server-side media
   resolver and scoped delivery; never expose storage credentials to a browser.
6. Build the hosted application on a branch after agreeing the normalized schema
   and the location of Claude's extracted application code. Port tested reference
   behavior; do not deploy the unauthenticated local prototype as production.

## Acceptance milestones

Public portal launch and old-admin retirement are separate milestones. The owner
chooses which vendor workflows Bally needs to retain. Preserve recovered data even
when an unused vendor feature is intentionally not rebuilt.

Before public launch: reconciled published records and images, search/facet
acceptance, item relationships, working media, publication controls, private admin
authentication, and no accidental metadata or original-file exposure.

Before old-system retirement: complete entity/file/version reconciliation or
explicitly accepted gaps, required editing/upload/inventory workflows, tested
roles and permissions, usable exports, independent backups and restore tests,
cutover/rollback plan, and owner sign-off. Source decommissioning is not authorized.
