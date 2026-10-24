-- Unpriced model calls. 012 made llm_usage_rows.cost_usd NOT NULL DEFAULT 0, so a call with no approved tariff
-- could not be stored without pretending it was free, and its hold stayed 'held' forever. 012 is applied as written;
-- this makes NULL mean "cost unknown": the row still records the call's tokens, which count toward the cap once the
-- hold is reconciled, and nothing may read NULL as 0. Dropping the default stops an omitted cost becoming 0.

alter table llm_usage_rows alter column cost_usd drop default;
alter table llm_usage_rows alter column cost_usd drop not null;
