-- Supabase's apply_migration tracks its own history, while our Python migrator
-- tracks filenames in public.schema_migrations. Record the already-applied auth
-- migrations in both trackers so a later app startup never attempts to replay DDL.
insert into public.schema_migrations (name)
values ('009_staff_sessions.sql'),
       ('010_mfa_session_rekey.sql'),
       ('011_hosted_auth_migration_history.sql')
on conflict (name) do nothing;
