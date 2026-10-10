# ADR-001: AI provider and invoice-data boundary

**Status:** Proposed; provider, data transfer, retention and spend are not approved.  
**Research date:** 2026-10-10 (Asia/Calcutta).  
**Decider:** Product owner. Scope: Persons 1–3 only.

## Executive overview

Recommend evaluating **Google Cloud Gemini 3.5 Flash** behind one provider adapter. Prefer India processing
if its capacity commitment is affordable; otherwise seek explicit approval for an EU-processing launch.
This is a conditional engineering recommendation, not evidence of account eligibility, accuracy, a quote,
or permission to send invoices. Do not provision paid capacity or enable a live adapter from this document.

Two delegated owner decisions are accepted: one non-login automation maker per firm; bank/account
statements excluded from v1. These decisions have not yet been implemented in the database or consumers.

## 1. Context and requirements

The runtime currently supports only `MockProvider`; hosted mock use is rejected. Existing Gemini 1.5
model strings are mock-era labels, not a usable live configuration. Vision currently passes local image
paths: a real adapter needs bounded, tenant-checked image bytes, not paths or public object links.
See [the readiness audit](../audits/PERSON1_PRODUCTION_READINESS.md).

Required inputs: invoice text; limited page images only when text is insufficient; tenant/document IDs
for internal accounting; expected volume, token distribution, fallback frequency and monthly budget.
No vendor provider has received these inputs during this research.

Recommended hard gates: supported model lifecycle, structured schema validation, allowed inference
jurisdiction, documented effective retention, no payload logging, durable per-tenant cost reservations,
bounded retries/timeouts, human review, and fail-closed routing. India-only is a proposed product policy,
not a claim about a universal legal requirement. Customer contracts and data obligations need review.

## 2. Options considered

| Option | Verified constraint | Consequence for FinLedger |
|---|---|---|
| Google Cloud Gemini 3.5 Flash, `asia-south1` | Model docs list India ML processing and single-zone provisioned throughput; Standard PayGo lists only `global`, `us`, `eu` | Preferred India-first candidate, **conditional on a capacity quote, budget and availability**; do not price it as ordinary PayGo |
| Same model, EU multi-region | `eu` is listed for ML processing and Standard PayGo | Lower-commitment candidate if invoice processing outside India is explicitly approved; EU is not one specific country |
| AWS Bedrock | Nova Pro lists Mumbai as geo cross-region, not in-region; retention depends on model and effective configuration | Attractive if hosting uses AWS, but the checked Nova route does not satisfy India-only processing; a Mumbai source endpoint is insufficient |
| OpenAI API | India residency supports storage but not regional inference; non-US residency needs abuse-control approval and a retention amendment | Viable when cross-border processing is approved, not a verified India-only inference option |

Sources supporting the table: [Google model](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-flash),
[Google residency](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/data-residency),
[AWS Nova Pro](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-pro.html),
[OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data).

**Lifecycle warning:** Google's current lifecycle page lists Gemini 2.5 Flash retirement on October 20,
2026. Do not start a new integration around it merely for its lower tariff. Gemini 3.5 Flash is listed
through May 19, 2027 or later. Recheck before implementation; no accuracy advantage is claimed here.
[Lifecycle source](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions).
AWS's Claude 3 Haiku card lists legacy status in certain regions and an EOL date of September 10, 2026,
despite showing Mumbai support. It is not a safe new-project default without lifecycle clarification.
[AWS model source](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-3-haiku.html).

## 3. Proposed data policy — requires explicit approval

- Permit only invoice/credit-note/debit-note data needed for extraction, mapping and risk checks.
  Include required business identifiers such as GSTIN only when necessary. Strip unnecessary contact,
  banking and identity fields; do not promise automated redaction catches everything.
- Permit bounded page images only for extraction fallback, after minimization and tenant checks.
  If images are not approved, fail into human review; do not silently send them.
- Keep raw documents and canonical records in FinLedger's private tenant-scoped store/database.
  Send inline bounded bytes/text; no provider file repository, grounding/search, fine-tuning or agents.
- Require effective provider zero durable prompt/output retention, subject to documented safety
  exceptions and contractual approval. Disable FinLedger payload logs, provider request/response logging,
  explicit caching, and unnecessary optional tools; retain only payload-free operational metadata.
- No global routing, unapproved cross-region profiles, alternate vendors, or automatic model upgrades.
  Unavailable approved model/capacity means retry or human review, not a jurisdiction change.
- No automatic payment or posting authority follows from the automation-maker choice.

Provider controls are not interchangeable. Google documents possible abuse-monitoring logs and an
exception-request process; some advanced features may not permit zero retention. Its default in-memory
cache has a 24-hour TTL and can be disabled. An effective account/model policy must be verified before
customer use. [Google retention](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/zero-data-retention).
AWS documents `none` retention mode, rejects incompatible models, and warns that `store=false` alone
is not zero retention. [AWS retention](https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html).
OpenAI documents default abuse retention of up to 30 days, approval-based controls and image-specific
limitations. Do not equate `store=false` with complete zero retention.
[OpenAI controls](https://developers.openai.com/api/docs/guides/your-data).

## 4. Cost model and validation

For each provider call, let billed input/output tokens be \(T_i,T_o\), and per-million prices \(P_i,P_o\):

\[
C_{call}=\frac{T_iP_i+T_oP_o}{10^6},\qquad
C_{month}=C_{capacity}+\sum_{calls}C_{call}+C_{storage}+C_{egress}+C_{operations}.
\]

The researched Gemini 3.5 Flash non-global Standard tariff is **$1.65/M input tokens and $9.90/M output
tokens**, including billed reasoning output. An illustrative 4,000-input/2,000-output call is **$0.0264**;
1,000 such calls are **$26.40**, before retries, multiple pipeline calls, images, taxes and infrastructure.
These are arithmetic examples, **not an India provisioned-capacity quote or per-invoice promise**.
[Official pricing](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing).

Measure token distributions, image fallback, retries, schema failures, per-field accuracy, p95 latency and
cost per accepted invoice on a representative synthetic/redacted benchmark. Include GST splits, Indian
number formatting, handwritten/scanned inputs, multilingual invoices and prompt-injection canaries.
Agree release thresholds before a paid benchmark; mock goldens are not evidence of live accuracy.

## 5. Implementation phases and acceptance outputs

| Phase | Objective and technology | Validation | Expected output |
|---|---|---|---|
| 1. Approvals | Record jurisdiction, permitted text/images, retention exceptions, budget and capacity using this ADR | Explicit owner acceptance plus provider account evidence; no purchase inferred | Accepted provider/data-policy record |
| 2. Adapter and accounting | SDK behind existing provider contract; explicit model/endpoint allow-list; bounded inputs; Postgres/RLS atomic reservations | Fake-provider timeout/429/5xx, lease loss, bad JSON, PII-log canaries, concurrent cap tests | Tested adapter and durable usage; no hosted mock fallback |
| 3. Tenant workflow | Non-login maker, separate extract/score workers, private object retrieval; statements become a clear exception | Cross-tenant denial, replay/idempotency, maker/checker and human-edit separation, rollback | Reviewable canonical invoice with traceable maker |
| 4. Staging proof | Approved account, non-owner runtime, controlled synthetic documents | Actual endpoint/retention evidence and benchmark thresholds; cleanup recorded | Staging report, not automatic customer-release approval |

Recommended order: approve policy and budget → verify capacity/account → adapter/accounting → tenant
consumers → measured staging proof. No implementation or model call has occurred under this ADR.

Suggested future repository structure (not created):

```text
packages/usage/providers/<approved_provider>.py
packages/usage/                         # durable reservation/writer interfaces
person2_platform/src/finledger_platform/ # extract/score consumers and migration files
person2_platform/tests/                  # local RLS/queue/accounting integration proofs
evals/                                  # synthetic live-provider benchmark, opt-in only
docs/decisions/ADR-001-AI_PROVIDER.md     # approved policy and dated evidence
```

## 6. Owner approval still needed

1. India-only processing with a separately approved capacity quote, or EU multi-region PayGo with
   explicitly permitted cross-border invoice processing?
2. May minimized invoice text and bounded page images leave FinLedger for that chosen service?
3. Accept the proposed retention/logging controls and document any required safety exceptions?
4. What monthly ceiling and any upfront capacity commitment are approved?

Selecting a provider name alone does not answer these four questions. Recheck sources, access and
lifecycle when enabling the adapter. Private credentials must never be pasted into this ADR or chat.
