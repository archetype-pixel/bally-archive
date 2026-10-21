# 360 spin frames: recovery handoff (Claude -> Codex)

Codex proved recovery of the complete 24-frame spin for SHOI0000035 (384 zoom tiles, reassembled to
2000x1064 frames, checksummed). The same method is needed for every spin in the collection.

## Job list
Table `public.spins` in the Bally Archive Supabase project (`glfiqywdzqcnrhpsjwwp`): 11,894 sessions,
11,826 objects. Columns: `session_id`, `object_guid`, `set_id`, `set_guid`, `shot_type` (all ROTATION),
`cdn_url` (the vendor's viewer .htm on cdn.ballyheritage.com), `folder_name` (e.g. `SHOI0000035_SN000237_HSL360`),
`frames` (24), `storage_prefix` (null until recovered).

Read access: the project's publishable key can select from `spins`; or use the read-only DB role in the owner's
hands. Do not modify `spins` directly except to set `storage_prefix` when a spin is complete.

## Target layout in Supabase Storage (bucket `archive`)
```
display/spins/<session_id>/00.jpg ... 23.jpg     1000px-wide JPEG frames for the website viewer (quality 85)
originals/spins/<session_id>/<folder_name>/...   the recovered full-resolution frames/tiles, as recovered
```
The website viewer (Lovable project "Bally Archive Platform") reads `display/spins/<session_id>/<n>.jpg`
for n = 0..23 (zero-padded two digits are fine; the viewer will accept either, confirm in `src/components/Spin360.tsx`)
once `spins.storage_prefix = 'display/spins/<session_id>'` is set.

## Completion signal
For each finished spin: upload the 24 display frames, then `update spins set storage_prefix='display/spins/<session_id>' where session_id=<id>`.
Keep a manifest per spin (tile checksums, source URLs) under `originals/spins/<session_id>/manifest.json`.

## Update (frames found on the Azure share)
The 24 frames per spin exist on the share as `<INV>_<SN>_000.jpg ... _023.jpg` next to the object's shoot folders.
`migrate/copy_files.py` now matches them (table `azure_frames` + `spin_copy`, function `match_pending_spins()`) and copies
them per shard into `display/spins/<session_id>/0..23.jpg` (1000px) and `originals/spins/<session_id>/00..23.jpg`.
The CDN tile route below is only a fallback for spins whose frames are missing from the share.

## Notes
- Spin folders with `HSL360` names are not on the share; the plain frame files are.
- 7 spins belong to objects whose admin card lists no inventory number; recover them anyway, they are linked by object_guid.
- Rate: be polite to the CDN; a few spins per second is plenty. 11,894 spins x 384 tiles is ~4.6M requests.
