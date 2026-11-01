-- Run only AFTER the exact committed migrations 001-011 have succeeded, in
-- their transaction. Dates record restoration, not the original deployment.
insert into public.schema_migrations (name) values
  ('001_tenancy.sql'),
  ('002_documents_queue.sql'),
  ('003_rls_functions.sql'),
  ('004_control_review.sql'),
  ('005_security_workflow.sql'),
  ('006_supabase_data_api_hardening.sql'),
  ('007_supabase_security_advisors.sql'),
  ('008_supabase_auth_subject.sql'),
  ('009_staff_sessions.sql'),
  ('010_mfa_session_rekey.sql'),
  ('011_hosted_auth_migration_history.sql')
on conflict (name) do nothing;
