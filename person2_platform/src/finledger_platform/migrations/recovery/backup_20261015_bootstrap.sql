-- Recovery prerequisite for an EMPTY replacement Supabase project.
-- The normal Python migrator creates this table before running 001; a hosted
-- SQL restore must do the same. Apply with 001-011 in ONE transaction so no
-- intermediate schema is exposed before migration 006 revokes Data API grants.
-- Recovery files live in this subdirectory so the app migrator never runs them.
create table if not exists public.schema_migrations (
  name text primary key,
  applied_at timestamptz not null default now()
);
