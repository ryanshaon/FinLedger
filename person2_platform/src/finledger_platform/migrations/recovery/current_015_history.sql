-- Run only AFTER the exact committed migrations 012-015 have succeeded, in
-- their transaction. Keep the app tracker aligned with hosted migration DDL.
insert into public.schema_migrations (name) values
  ('012_ai_durable_state.sql'),
  ('013_llm_orphaned_holds.sql'),
  ('014_llm_unpriced_usage.sql'),
  ('015_purchase_orders_goods_receipts.sql')
on conflict (name) do nothing;
