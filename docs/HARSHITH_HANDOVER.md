# Handover for Harshith

2026-10-22. Branch `feat/ruthvik-post-harshith`, [PR #7](https://github.com/jastiruthvik-web/FinLedger/pull/7).
Behavior commit `614d47d87475ea611722bdb247e345cb7c490556`. Local suites on that commit: AI **165**,
platform **360**, UI **69**, mock **3/3 + 6/6**, 0 failed, 0 skipped. No hosted SQL was applied.

**Status 2026-10-24:** the tasks below are done on the local completion branch (durable wiring in
`default_extract(conn)`, migration `014_llm_unpriced_usage.sql` for unpriced calls, `score-worker`). See
[HARSHITH_WORK.md §2a](assignments/HARSHITH_WORK.md).

## What this branch changed

**Expired holds.** Option 3: accept the rule already in migration `013`. Expired rows stay for audit and do not count toward the cap. With `max_attempts = 5`, five dead attempts that had already reached the provider can leave the cap (about 5× that bill). That is accepted. A timeout with no body can stay uncounted. Do not edit `012` or `013` for this, and do not add `014` for it. The existing expiry tests stay the regression.

**Settlement.** `ModelRouter(settings=None, gemini_transport=None, kill_switch=None)` now takes a kill switch. `PostgresKillSwitch.reserve` opens a short `tenant()` transaction, commits, and returns. The model call stays outside that transaction. `PostgresKillSwitch.settle_usage` then inserts one `llm_usage_rows` row whose `reservation_id` is the hold and calls `llm_reconcile_tokens` in the same transaction. Thoughts are already inside `tokens_out`. Unknown usage with a fixture tariff reconciles at the reserved estimate, not at 0/0. A fixture tariff stores that nonzero `cost_usd`. `PostgresUsageWriter.log_usage` no longer allocates a new reservation id; pass the hold's id. `ExtractWorker` does not insert a second usage row when `complete()` already set `usage_persisted`. `staging` and `production` still refuse both `mock` and `gemini`. `GeminiHttpClient` is still not constructed unless the caller passes it.

**Unpriced Gemini.** `llm_usage_rows.cost_usd` cannot store NULL. This branch does not coerce that to `0`. A Gemini success with no tariff does not insert a usage row. The hold stays `held`, so the estimate still counts, until you can store "unpriced". `MockProvider` may still store its explicit `0.0`. The researched `$1.65` / `$9.90` figures are not configuration.

**Score payload.** `validate_invoice` now accepts Person 1's `vendor.pan` and `vendor.state`, and a string `bank_upi`. `extra="forbid"` remains. An unknown vendor field still fails. A mock extract with a positive total and at least one line validates. No score-worker command was added.

**Approval.** The automation user stays `canonical_invoices.maker_id`. `approve` also refuses a human whose id is `voucher_drafts.created_by` or `correction_events.actor_id` for that document. A different human approver, who did not create the draft and did not write a correction, can approve. The automation user still has no Auth subject, no API token, no `firm_admin` flag, and no client role. No migration and no posting or payment grant.

`default_extract()` was not changed. It still builds `ExtractWorker(UsageWriter())`, which still builds `ModelRouter()` with no arguments.

## What you still have to do

Wire the injected switch and writer into `default_extract()` after this PR, using the worker pool and short `tenant()` transactions. Do not hold a transaction open across the model call. The connection you pass in must not already be inside one. A pooled autocommit connection is fine: each adapter call opens and commits its own `tenant()`.

```python
switch = PostgresKillSwitch(conn, limit=settings.per_bill_token_cap, enabled=settings.kill_switch_enabled)
router = ModelRouter(settings, kill_switch=switch)  # pass gemini_transport only when a caller supplies it
worker = ExtractWorker(PostgresUsageWriter(conn), router)
```

`complete()` reserves, calls the model, and settles. The writer is still required so a call that did not persist (unpriced, or the in-memory path) does not invent a second reservation id. Do not remove the `staging` / `production` refusal.

Write migration `014` only for the unpriced cost column (nullable `cost_usd`, or a separate status). Do not write it to change the expired-hold rule. Do not edit `012` or `013`.

Apply `012` and `013` on the hosted database only after independent SQL review and Ryan's apply approval. They are committed and not applied there. Create the app login (`fl_api` and the other session-pooler roles in the runbook) after Ryan supplies the password. Storage, scanner, email, deploy, rollback, and the staging URL stay yours and stay open.

Score-consumer ownership is still not agreed. Do not start a `finledger-platform` score worker until that is explicit. If you write it, follow the extract-worker pattern and leave review logic in Person 3. The payload mismatch that would have made `validate_invoice` reject a mock invoice is fixed on this branch. Extraction still does not post or pay.
