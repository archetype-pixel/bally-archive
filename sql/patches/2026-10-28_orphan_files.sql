-- Inventory of stored files with no catalogue record (PDF, film, PNG, zip under azure-mirror/), filled by migrate/orphan_files.py.
create table if not exists public.orphan_files (
  path text primary key, kind text, size_mb numeric,
  display_path text, thumb_path text, width int, height int, pages int, duration_s numeric,
  text_excerpt text, zip_listing text, attached_to text, note text, updated_at timestamptz default now());
alter table public.orphan_files enable row level security;
create policy "staff read orphan_files" on public.orphan_files for select to authenticated using (archive_tier() is not null);
