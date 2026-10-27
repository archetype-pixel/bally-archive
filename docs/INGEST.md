# Loading photos into the Bally archive

Three ways in. All three end up in the same place and are processed automatically.

| Source | How | Who |
|---|---|---|
| One-off additions | Staff upload page on the site (drag a folder; object codes are read from file and folder names) | Archivist |
| Studio shoots | Capture software saves into a synced "drop folder" | Studio |
| Bulk (the Schönenwerd server / hard drive) | rclone copy to `archive/intake/<batch>/` | Tech team |

## Naming rule (the one thing that makes matching automatic)

Put the object code in the file name or the folder name, for example
`SHOO0010407_front.tif`, or a folder `SHOO0010407/` with any file names inside.
A 360 is a folder of numbered frames: `SHOO0010407/spin_01.jpg … spin_36.jpg`.
Files without a recognisable code are not lost: they go to the review queue with
"looks like" suggestions from visual search.

## Bulk load from the hard drive (tech team)

1. Get an S3 access key for the archive from M (Supabase dashboard, Storage, S3 Configuration,
   "New access key"). Revoke it when the load is finished.
2. Install rclone (https://rclone.org/install/) and add a remote:

   ```
   rclone config create bally s3 provider=Other \
     endpoint=https://glfiqywdzqcnrhpsjwwp.storage.supabase.co/storage/v1/s3 \
     region=us-west-1 access_key_id=<KEY ID> secret_access_key=<SECRET>
   ```

3. Copy the drive, keeping its folder structure. Use one batch name per drive, e.g. `schoenenwerd-2026-11`:

   ```
   rclone copy "/Volumes/BallyArchive" bally:archive/intake/schoenenwerd-2026-11 \
     --transfers 16 --checkers 32 --s3-chunk-size 64M --s3-upload-concurrency 8 \
     --exclude ".DS_Store" --exclude "Thumbs.db" --progress
   ```

   rclone resumes where it stopped if the connection drops; just run the same command again.
   15 TB takes roughly 1.5 days on a 1 Gbit/s line and about 2 weeks on 100 Mbit/s.

4. Nothing else. Every 20 minutes the archive picks up new files. For a large load, start
   "Intake (bulk drive load)" in GitHub Actions with 16 shards to process it in parallel.

Originals are never changed or moved. Processing makes web and zoom copies, reads embedded
photo data, matches to the Collection, writes an AI description with English translations,
and adds the photos to visual search.

## Studio drop folder

Point the capture software's output (Capture One, Phocus) at a folder named by object code,
and sync that folder with rclone every few minutes to `bally:archive/intake/studio/`:

```
rclone copy "<capture output folder>" bally:archive/intake/studio --min-age 2m --transfers 8
```

(`--min-age 2m` waits until the camera software has finished writing each file.)
