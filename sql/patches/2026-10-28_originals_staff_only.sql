-- Originals in the private 'archive' bucket were readable by ANY signed-in user (public signup is on).
-- Restrict to active archive staff, matching the 2026-10-27 staff-only change. Applied 2026-10-28.
alter policy "staff read originals" on storage.objects
  using ((bucket_id = 'archive') and (name like 'originals/%') and is_archive_staff());
