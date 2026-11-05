"""Transactional control-plane service. UI/API call this; neither writes workflow state directly."""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.errors import LockNotAvailable
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from finledger_platform.purchase_documents import find_goods_receipt, find_purchase_order

from .boundary import validate_draft, validate_invoice
from .risk import RiskContext, score_invoice
from .three_way import PHASE1_REASONS, assess_three_way, phase1_posting_block, posting_view
from .workflow import Action, Policy, Role, TransitionContext, decide_route, transition, InvalidTransition


LIVE_TEST_NAME_PREFIX = "FinLedger Live Test"  # person4 preflight.LIVE_TEST_NAME_PREFIX; pinned by a person4 contract test


class Conflict(RuntimeError): pass
class Forbidden(RuntimeError): pass
class NotFound(RuntimeError): pass


class ControlService:
    def __init__(self, conn): self.conn = conn

    def _tx(self, client_id):
        return _TenantTransaction(self.conn, client_id)

    def _roles(self, user_id, client_id) -> set[Role]:
        raw = self.conn.execute("select user_client_roles(%s,%s) as roles", (user_id, client_id)).fetchone()["roles"] or []
        return {Role(x) for x in raw if x in {r.value for r in Role}}

    def _event(self, client_id, document_id, old, new, action, actor_id=None, metadata=None):
        self.conn.execute("""insert into workflow_events(client_id,document_id,from_status,to_status,action,actor_id,metadata)
                             values (%s,%s,%s,%s,%s,%s,%s)""",
                          (client_id, document_id, old, new, action, actor_id, Jsonb(metadata or {})))

    def _set_status(self, client_id, document_id, expected, new, action, actor_id=None, metadata=None):
        row = self.conn.execute("select status from documents where id=%s for update", (document_id,)).fetchone()
        if not row: raise NotFound("document not found")
        old = row["status"]
        if old not in ({expected} if isinstance(expected, str) else set(expected)):
            raise Conflict(f"expected status {expected}; found {old}")
        self.conn.execute("update documents set status=%s,status_reason=null,updated_at=now() where id=%s", (new, document_id))
        self._event(client_id, document_id, old, new, action, actor_id, metadata)

    def score(self, client_id, document_id, invoice, actor_id, *, llm_score=0, llm_marks=()):
        try:
            invoice, _ = validate_invoice(invoice, document_id)
        except (ValidationError, ValueError) as exc:
            raise Conflict(f"invalid canonical invoice: {exc}") from exc
        with self._tx(client_id):
            doc = self.conn.execute("select status,phish_flag from documents where id=%s for update", (document_id,)).fetchone()
            if not doc: raise NotFound("document not found")
            if doc["status"] != "extracted": raise Conflict(f"expected extracted; found {doc['status']}")
            client = self.conn.execute("select gstins,po_required,po_required_above,match_mode,msme_clock,itc_180_warning from clients where id=%s", (client_id,)).fetchone()
            gstin = str((invoice.get("vendor") or {}).get("gstin") or "")
            vendor = self.conn.execute("select msme,name from vendors where gstin=%s", (gstin,)).fetchone()
            duplicate = self.conn.execute("""select exists(select 1 from canonical_invoices c
                where c.document_id<>%s and c.payload->'vendor'->>'gstin'=%s and c.payload->>'invoice_no'=%s
                  and c.payload->>'invoice_date' is not null and extract(year from (c.payload->>'invoice_date')::date)=extract(year from %s::date)
                  and (c.payload->>'total')::numeric=%s) as duplicate""",
                (document_id, gstin, invoice.get("invoice_no"), invoice.get("invoice_date"), invoice.get("total"))).fetchone()["duplicate"]
            median = self.conn.execute("""select percentile_cont(.5) within group(order by (payload->>'total')::numeric) as median
                                          from canonical_invoices where payload->'vendor'->>'gstin'=%s""", (gstin,)).fetchone()["median"]
            ctx = RiskContext(tuple(client["gstins"]), bool(vendor), bool(vendor and vendor["msme"]),
                              vendor["name"] if vendor else None, Decimal(str(median)) if median else None,
                              duplicate=duplicate, phish_flag=doc["phish_flag"], po_required=client["po_required"],
                              po_required_above=Decimal(str(client["po_required_above"])) if client["po_required_above"] is not None else None,
                              match_mode=client["match_mode"], msme_clock=client["msme_clock"], itc_180_warning=client["itc_180_warning"])
            result = score_invoice(invoice, ctx, llm_score=llm_score, llm_marks=llm_marks)
            self.conn.execute("""insert into canonical_invoices(document_id,client_id,payload,llm_score,llm_marks,maker_id)
                values(%s,%s,%s,%s,%s,%s) on conflict(document_id) do update set payload=excluded.payload,
                llm_score=excluded.llm_score,llm_marks=excluded.llm_marks,revision=canonical_invoices.revision+1,updated_at=now()""",
                (document_id, client_id, Jsonb(invoice), llm_score, Jsonb(list(llm_marks)), actor_id))
            self.conn.execute("insert into risk_assessments(client_id,document_id,score,band,marks) values(%s,%s,%s,%s,%s)",
                              (client_id, document_id, result.score, result.band, Jsonb([m.to_dict() for m in result.marks])))
            self._set_status(client_id, document_id, "extracted", "scored", "score", actor_id,
                             {"score": result.score, "band": result.band})
            return result.to_dict()

    def submit_draft(self, client_id, document_id, draft, actor_id):
        try:
            draft, voucher_total = validate_draft(draft, document_id)
        except (ValidationError, ValueError) as exc:
            raise Conflict(f"invalid voucher draft: {exc}") from exc
        with self._tx(client_id):
            row = self.conn.execute("""select d.status,r.band,c.auto_post_cap,c.maker_checker,
                     (i.payload->>'total')::numeric as invoice_total
              from documents d join lateral(select band from risk_assessments where document_id=d.id order by id desc limit 1) r on true
              join canonical_invoices i on i.document_id=d.id
              join clients c on c.id=d.client_id where d.id=%s for update of d""", (document_id,)).fetchone()
            if not row or row["status"] != "scored": raise Conflict("document must be scored before mapping")
            invoice_total = Decimal(str(row["invoice_total"]))
            if abs(voucher_total - invoice_total) > Decimal("0.01"):
                raise Conflict(f"voucher total {voucher_total} does not reconcile to invoice total {invoice_total}")
            self.conn.execute("insert into voucher_drafts(document_id,client_id,payload,created_by) values(%s,%s,%s,%s)",
                              (document_id, client_id, Jsonb(draft), actor_id))
            current = "scored"
            for nxt, action in (("vendor_resolved","vendor_resolved"),("matched","matched"),("coded","coded"),("tax_checked","tax_checked")):
                self.conn.execute("update documents set status=%s,updated_at=now() where id=%s", (nxt, document_id))
                self._event(client_id, document_id, current, nxt, action, actor_id); current = nxt
            route = decide_route(row["band"], float(invoice_total), Policy(float(row["auto_post_cap"]), row["maker_checker"]))
            if route.value == "review":
                reason = f"{row['band']} risk" if row["band"] != "low" else "policy requires approval"
                self.conn.execute("insert into review_tasks(client_id,document_id,risk_band,reason,draft_revision) values(%s,%s,%s,%s,1)",
                                  (client_id, document_id, row["band"], reason))
                self._set_status(client_id, document_id, "tax_checked", "in_review", "send_review", actor_id)
            else:
                # Policy-clear approves the bill but never posts it: every post job needs a human approval.
                self._set_status(client_id, document_id, "tax_checked", "approved", "policy_clear", actor_id)
                self.conn.execute("insert into approvals(client_id,document_id,kind,revision) values(%s,%s,'policy_clear',1)", (client_id, document_id))
            return {"route": route.value, "status": "in_review" if route.value == "review" else "approved", "revision": 1}

    def _phase1_block(self, client_id, document_id, revision, *, human_approved=None) -> str | None:
        """Caller already holds the client transaction. ``human_approved`` overrides the lookup for an approval being
        recorded in the same transaction."""
        link = self.conn.execute("select posting_enabled_at,company_guid from erp_links where client_id=%s", (client_id,)).fetchone()
        draft = self.conn.execute("select payload from voucher_drafts where document_id=%s", (document_id,)).fetchone()
        invoice = self.conn.execute("select payload from canonical_invoices where document_id=%s", (document_id,)).fetchone()
        if human_approved is None:
            human_approved = bool(self.conn.execute(
                "select 1 from approvals where document_id=%s and kind='human' and revision=%s and superseded_at is null",
                (document_id, revision)).fetchone())
        return phase1_posting_block(draft["payload"] if draft else None, invoice["payload"] if invoice else None,
                                    posting_enabled=bool(link and link["posting_enabled_at"] and link["company_guid"]),
                                    human_approved=human_approved)

    def _enqueue_post(self, client_id, document_id, revision):
        block = self._phase1_block(client_id, document_id, revision)
        if block: raise Conflict(PHASE1_REASONS[block])  # the last gate; every post job in the product passes here
        draft = self.conn.execute("select payload from voucher_drafts where document_id=%s", (document_id,)).fetchone()["payload"]
        payload = {"document_id": str(document_id), "client_id": str(client_id), "revision": revision,
                   "idempotency_key": f"{document_id}:r{revision}", "voucher_draft": draft}
        self.conn.execute("""insert into jobs(client_id,document_id,queue,idem_key,payload)
            values(%s,%s,'post',%s,%s) on conflict(queue,document_id,idem_key) do nothing""",
            (client_id, document_id, f"r{revision}", Jsonb(payload)))
        job = self.conn.execute("select id,payload from jobs where document_id=%s and queue='post' and idem_key=%s",
                                (document_id, f"r{revision}")).fetchone()
        import hashlib, json
        request_hash = hashlib.sha256(json.dumps(job["payload"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.conn.execute("""insert into posting_attempts(client_id,document_id,revision,job_id,idempotency_key,request_hash)
            values(%s,%s,%s,%s,%s,%s) on conflict(job_id) do nothing""",
            (client_id, document_id, revision, job["id"], payload["idempotency_key"], request_hash))

    def _editor_ids(self, document_id, created_by) -> frozenset[str]:
        rows = self.conn.execute(
            "select actor_id from correction_events where document_id=%s", (document_id,)
        ).fetchall()
        editors = {str(created_by)}
        editors.update(str(row["actor_id"]) for row in rows)
        return frozenset(editors)

    def _open_match(self, client_id, document_id):
        """Caller already holds the client transaction."""
        mode = self.conn.execute("select match_mode from clients where id=%s", (client_id,)).fetchone()
        invoice_row = self.conn.execute(
            "select payload from canonical_invoices where document_id=%s", (document_id,)).fetchone()
        draft_row = self.conn.execute(
            "select payload from voucher_drafts where document_id=%s", (document_id,)).fetchone()
        invoice = invoice_row["payload"] if invoice_row and isinstance(invoice_row["payload"], dict) else {}
        draft = draft_row["payload"] if draft_row and isinstance(draft_row["payload"], dict) else {}
        purchase_order = find_purchase_order(self.conn, invoice.get("po_number"))
        goods_receipt = find_goods_receipt(self.conn, invoice.get("grn_number"), invoice.get("po_number"))
        match = assess_three_way(invoice, purchase_order, goods_receipt, (mode or {}).get("match_mode") or "invoice_only")
        posting = posting_view(draft)
        if match["match_mode"] != "three_way" or not posting["blocked"]:
            revision_row = self.conn.execute("select status from documents where id=%s", (document_id,)).fetchone()
            draft_revision = self.conn.execute("select revision from voucher_drafts where document_id=%s", (document_id,)).fetchone()
            in_review = bool(revision_row and revision_row["status"] == "in_review")
            code = self._phase1_block(client_id, document_id, draft_revision["revision"] if draft_revision else 0,
                                      human_approved=True if in_review else None)
            posting = {"blocked": code is not None, "reason": PHASE1_REASONS[code] if code else None, "code": code}
        return match, posting

    def bill_match(self, client_id, document_id):
        with self._tx(client_id):
            match, posting = self._open_match(client_id, document_id)
            return {"match": match, "posting": posting}

    def _approval_body(self, status, revision, *, post, block_post, posting):
        body = {"status": status, "revision": revision}
        if post and block_post:
            body["posting_blocked"] = True
            body["posting_reason"] = posting["reason"]
        return body

    def approve(self, client_id, document_id, actor_id, *, post, expected_revision):
        with self._tx(client_id):
            row = self.conn.execute("""select d.status,v.revision,c.maker_id,v.created_by from documents d
                join voucher_drafts v on v.document_id=d.id join canonical_invoices c on c.document_id=d.id
                where d.id=%s for update of d,v""", (document_id,)).fetchone()
            if not row: raise NotFound("review not found")
            match, posting = self._open_match(client_id, document_id)
            if match["holds_review"]:
                raise Conflict(match["review_note"])
            block_post = posting["blocked"]
            enqueue = bool(post) and not block_post
            actor, maker = str(actor_id), str(row["maker_id"])
            editors = self._editor_ids(document_id, row["created_by"])
            roles = self._roles(actor_id, client_id)
            if (row["status"] == "approved" and row["revision"] == expected_revision and actor != maker and actor not in editors
                    and roles & {Role.APPROVER, Role.FIRM_ADMIN}):
                if enqueue: self._enqueue_post(client_id, document_id, row["revision"])
                return self._approval_body(row["status"], row["revision"], post=post, block_post=block_post, posting=posting)
            if row["revision"] != expected_revision: raise Conflict(f"revision changed from {expected_revision} to {row['revision']}")
            try: new = transition(row["status"], Action.APPROVE_DRAFT if not enqueue else Action.APPROVE,
                                  TransitionContext(actor, roles, maker_id=maker, editor_ids=editors))
            except InvalidTransition as exc: raise Forbidden(str(exc)) from exc
            self.conn.execute("update documents set status=%s,updated_at=now() where id=%s", (new, document_id))
            self._event(client_id, document_id, row["status"], new, "approve_and_post" if enqueue else "approve_draft", actor_id)
            self.conn.execute("update review_tasks set status='approved',decided_by=%s,decided_at=now() where document_id=%s and status='open'", (actor_id, document_id))
            self.conn.execute("insert into approvals(client_id,document_id,kind,actor_id,revision) values(%s,%s,'human',%s,%s) on conflict do nothing",
                              (client_id, document_id, actor_id, row["revision"]))
            if enqueue: self._enqueue_post(client_id, document_id, row["revision"])
            return self._approval_body(new, row["revision"], post=post, block_post=block_post, posting=posting)

    def edit_field(self, client_id, document_id, actor_id, field, value, *, expected_revision):
        allowed = {"party_ledger", "narration", "bill_wise.ref", "bill_wise.due_date"}
        if field not in allowed: raise Forbidden("field is not editable here")
        with self._tx(client_id):
            row = self.conn.execute("""select v.payload,v.revision,d.status,
                exists(select 1 from posting_attempts p where p.document_id=d.id and p.result='outcome_uncertain') uncertain_post
                from voucher_drafts v join documents d on d.id=v.document_id where v.document_id=%s for update of v,d""", (document_id,)).fetchone()
            if not row: raise NotFound("draft not found")
            if row["status"] in {"posted","reconciled","closed","scheduled","paid"}: raise Conflict(f"cannot edit {row['status']} document")
            if row["uncertain_post"]: raise Conflict("cannot edit while a post outcome is uncertain")
            if row["revision"] != expected_revision: raise Conflict("draft changed; refresh before saving")
            try:
                active_post = self.conn.execute('select lock_post_jobs_for_edit(%s) as active', (document_id,)).fetchone()['active']
            except LockNotAvailable:
                raise Conflict('post job is busy; try again') from None
            if active_post:
                raise Conflict("cannot edit while an active post attempt exists")
            payload = row["payload"]; target, leaf = (payload, field)
            if "." in field:
                head, leaf = field.split(".",1); target = payload.setdefault(head,{})
            old = target.get(leaf); target[leaf] = value; revision = row["revision"] + 1
            self.conn.execute("update voucher_drafts set payload=%s,revision=%s,updated_at=now() where document_id=%s", (Jsonb(payload), revision, document_id))
            if row["status"] == "approved":
                self.conn.execute("update approvals set superseded_at=now(),superseded_by_revision=%s where document_id=%s and superseded_at is null", (revision,document_id))
                self.conn.execute('select cancel_unfinished_post_jobs(%s)', (document_id,))
                self.conn.execute("insert into review_tasks(client_id,document_id,risk_band,reason,draft_revision) select %s,%s,band,'material edit after approval',%s from risk_assessments where document_id=%s order by id desc limit 1", (client_id,document_id,revision,document_id))
                self.conn.execute("update documents set status='in_review',updated_at=now() where id=%s", (document_id,))
                self._event(client_id,document_id,"approved","in_review","approval_invalidated",actor_id,{"revision":revision})
            inv = self.conn.execute("select payload from canonical_invoices where document_id=%s", (document_id,)).fetchone()["payload"]
            item = (inv.get("line_items") or [{}])[0]
            self.conn.execute("""insert into correction_events(client_id,document_id,vendor_gstin,field,old_value,new_value,hsn,description,actor_id)
              values(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (client_id,document_id,(inv.get("vendor") or {}).get("gstin"),field,Jsonb(old),Jsonb(value),item.get("hsn_sac"),item.get("desc"),actor_id))
            return {"revision":revision,"field":field,"value":value}

    def reject(self, client_id, document_id, actor_id, note, *, resubmit, expected_revision):
        with self._tx(client_id):
            row = self.conn.execute("select d.status,v.revision,c.maker_id from documents d join voucher_drafts v on v.document_id=d.id join canonical_invoices c on c.document_id=d.id where d.id=%s for update of d", (document_id,)).fetchone()
            if not row or row["revision"] != expected_revision: raise Conflict("review revision changed")
            roles = self._roles(actor_id, client_id)
            try: new = transition(row["status"], Action.ASK_RESUBMIT if resubmit else Action.REJECT,
                                  TransitionContext(str(actor_id), roles, maker_id=str(row["maker_id"])))
            except InvalidTransition as exc: raise Forbidden(str(exc)) from exc
            self.conn.execute("update documents set status=%s,status_reason=%s,updated_at=now() where id=%s", (new,note,document_id))
            self.conn.execute("update review_tasks set status=%s,decision_note=%s,decided_by=%s,decided_at=now() where document_id=%s and status='open'", (new,note,actor_id,document_id))
            self._event(client_id,document_id,row["status"],new,"ask_resubmit" if resubmit else "reject",actor_id,{"note":note})
            return {"status":new,"revision":row["revision"]}

    def erp_result(self, client_id, document_id, *, ok, job_id, revision, idempotency_key, erp_id=None, error=None,
                   uncertain=False, request_sha256=None, response_sha256=None):
        if ok and uncertain: raise Conflict("a posting result cannot be both posted and uncertain")
        outcome = "posted" if ok else "outcome_uncertain" if uncertain else "failed"
        with self._tx(client_id):
            row = self.conn.execute("""select d.status,p.id,p.revision,p.idempotency_key,p.result,p.erp_id
                from documents d join posting_attempts p on p.document_id=d.id
                where d.id=%s and p.job_id=%s for update of d,p""", (document_id,job_id)).fetchone()
            if not row: raise NotFound("document not found")
            if row["revision"] != revision: raise Conflict("posting callback revision mismatch")
            if row["idempotency_key"] != idempotency_key: raise Conflict("posting callback idempotency key mismatch")
            if row["result"] != "pending":
                body = {"status":"posted" if row["result"] == "posted" else "exception"}
                if row["result"] != outcome or (ok and row["erp_id"] != erp_id):
                    self._event(client_id,document_id,row["status"],row["status"],"erp_result_conflict",
                                metadata={"recorded":row["result"],"reported":outcome})
                    body["conflict"] = True
                return body
            action = Action.POSTED if ok else Action.ERP_FAILED
            try: new = transition(row["status"], action, TransitionContext("person4", set()))
            except InvalidTransition as exc: raise Conflict(str(exc)) from exc
            self.conn.execute("update documents set status=%s,status_reason=%s,updated_at=now() where id=%s", (new,error,document_id))
            self.conn.execute("""update posting_attempts set result=%s,erp_id=%s,error=%s,request_sha256=%s,response_sha256=%s,
                completed_at=now() where id=%s""", (outcome,erp_id,error,request_sha256,response_sha256,row["id"]))
            action_name = "erp_posted" if ok else "erp_uncertain" if uncertain else "erp_failed"
            self._event(client_id,document_id,row["status"],new,action_name,metadata={"erp_id":erp_id,"error":error})
            if ok and new == "posted":
                from finledger_platform.client_index import index_posted_document
                index_posted_document(self.conn, client_id, document_id, erp_id)
            return {"status":new}

    def posting_job(self, client_id, document_id):
        """Latest post job and attempt of a document, for the desk; None when it has never had a post job."""
        with self._tx(client_id):
            return self._latest_post(document_id)

    def _latest_post(self, document_id):
        row = self.conn.execute("""select j.id as job_id,j.state as job_state,j.claim_token,p.id as attempt_id,p.revision,
            p.result as attempt_result,p.erp_id,p.error,p.reconcile_decision
            from jobs j left join posting_attempts p on p.job_id=j.id
            where j.document_id=%s and j.queue='post' order by j.id desc limit 1""", (document_id,)).fetchone()
        return dict(row) if row else None

    def reconcile_post(self, client_id, document_id, actor_id, *, decision, erp_id, note):
        """A person checked Tally and records what they found. Never sends anything. The caller checks it is the owner."""
        with self._tx(client_id):
            status = self.conn.execute("select status from documents where id=%s for update", (document_id,)).fetchone()
            if not status: raise NotFound("document not found")
            post = self._latest_post(document_id)
            if not post or not (post["attempt_result"] == "outcome_uncertain" or post["job_state"] == "needs_reconciliation"):
                raise Conflict("this bill has no uncertain post to reconcile")
            if post["reconcile_decision"]: raise Conflict("this post has already been reconciled")
            current = status["status"]
            actor = TransitionContext(str(actor_id), self._roles(actor_id, client_id))
            if decision == "posted":
                guid = (self.conn.execute("select company_guid from erp_links where client_id=%s", (client_id,)).fetchone() or {}).get("company_guid")
                if not guid or not str(erp_id or "").startswith(f"{guid}-"):
                    raise Conflict("the Tally ID must belong to the paired company")
                try: new = transition(current, Action.RECONCILE_POSTED, actor)
                except InvalidTransition as exc: raise Conflict(str(exc)) from exc
                self.conn.execute("update posting_attempts set result='posted',erp_id=%s,completed_at=now() where id=%s", (erp_id, post["attempt_id"]))
                job_state = "done"
            else:
                new = "exception"
                self.conn.execute("update posting_attempts set result='outcome_uncertain',completed_at=now() where id=%s", (post["attempt_id"],))
                job_state = "dead"
            self.conn.execute("""update posting_attempts set reconcile_decision=%s,reconciled_by=%s,reconciled_at=now(),reconcile_note=%s
                where id=%s""", (decision, actor_id, note, post["attempt_id"]))
            self.conn.execute("update jobs set state=%s,claim_token=null,locked_until=null,updated_at=now() where id=%s", (job_state, post["job_id"]))
            self.conn.execute("update documents set status=%s,status_reason=%s,updated_at=now() where id=%s",
                              (new, None if decision == "posted" else "reconciled absent; this bill cannot post again in Phase 1", document_id))
            self._event(client_id, document_id, current, new, f"reconcile_{decision}", actor_id, {"erp_id": erp_id, "note": note})
            if new == "posted":
                from finledger_platform.client_index import index_posted_document
                index_posted_document(self.conn, client_id, document_id, erp_id)
            return {"status": new, "reconcile_decision": decision}

    def return_to_review(self, client_id, document_id, actor_id, note, *, expected_revision):
        """A clear Tally rejection goes back to review as a new revision and needs a fresh approval. Uncertain posts never do."""
        with self._tx(client_id):
            row = self.conn.execute("""select d.status,v.revision from documents d join voucher_drafts v on v.document_id=d.id
                where d.id=%s for update of d,v""", (document_id,)).fetchone()
            if not row: raise NotFound("document not found")
            if row["revision"] != expected_revision: raise Conflict("draft changed; refresh before saving")
            post = self._latest_post(document_id)
            if not post or post["attempt_result"] != "failed":
                raise Conflict("only a bill Tally clearly rejected can return to review")
            try: new = transition(row["status"], Action.RETURN_TO_REVIEW, TransitionContext(str(actor_id), self._roles(actor_id, client_id)))
            except InvalidTransition as exc: raise Forbidden(str(exc)) from exc
            revision = row["revision"] + 1
            self.conn.execute("update voucher_drafts set revision=%s,updated_at=now() where document_id=%s", (revision, document_id))
            self.conn.execute("update approvals set superseded_at=now(),superseded_by_revision=%s where document_id=%s and superseded_at is null", (revision, document_id))
            self.conn.execute("insert into review_tasks(client_id,document_id,risk_band,reason,draft_revision) select %s,%s,band,'returned after Tally rejection',%s from risk_assessments where document_id=%s order by id desc limit 1", (client_id, document_id, revision, document_id))
            self.conn.execute("update documents set status=%s,updated_at=now() where id=%s", (new, document_id))
            self._event(client_id, document_id, row["status"], new, "return_to_review", actor_id, {"revision": revision, "note": note})
            return {"status": new, "revision": revision}

    def posting_switch(self, client_id):
        with self._tx(client_id):
            row = self.conn.execute("""select company_name,company_guid,posting_enabled_at,posting_enabled_by,last_seen_at
                from erp_links where client_id=%s""", (client_id,)).fetchone()
            return dict(row) if row else None

    def enable_posting(self, client_id, actor_id, *, company_guid, confirm_company_name):
        """The caller checks it is the owner. Only a company named as a disposable test company can be enabled."""
        with self._tx(client_id):
            row = self.conn.execute("select company_name from erp_links where client_id=%s for update", (client_id,)).fetchone()
            if not row or not row["company_name"]: raise Conflict("pair a Tally company first")
            if row["company_name"] != confirm_company_name: raise Conflict("the company name doesn't match the paired company")
            if not row["company_name"].startswith(LIVE_TEST_NAME_PREFIX):
                raise Conflict(f"Phase 1 can only post to a company named {LIVE_TEST_NAME_PREFIX} <date>")
            entry = Jsonb([{"action": "enable", "actor": str(actor_id), "company_guid": company_guid}])
            self.conn.execute("""update erp_links set company_guid=%s,posting_enabled_at=now(),posting_enabled_by=%s,
                posting_switch_history=posting_switch_history || %s::jsonb where client_id=%s""",
                              (company_guid, actor_id, entry, client_id))
        return self.posting_switch(client_id)

    def disable_posting(self, client_id, actor_id):
        """Always allowed. A job already claimed finishes under its lease; nothing new can be claimed."""
        with self._tx(client_id):
            entry = Jsonb([{"action": "disable", "actor": str(actor_id)}])
            self.conn.execute("""update erp_links set posting_enabled_at=null,posting_enabled_by=null,
                posting_switch_history=posting_switch_history || %s::jsonb where client_id=%s""", (entry, client_id))
        return self.posting_switch(client_id)

    def post_job(self, client_id, document_id):
        with self._tx(client_id):
            row = self.conn.execute("select id,payload,state from jobs where document_id=%s and queue='post' order by id desc limit 1", (document_id,)).fetchone()
            if not row: raise NotFound("post job not found")
            return dict(row)

    def queue_counts(self, client_id):
        with self._tx(client_id):
            rows = self.conn.execute("select status,count(*) from documents group by status").fetchall()
            d = {r["status"]: r["count"] for r in rows}
            def total(*names):
                return sum(d.get(name, 0) for name in names)
            return {
                "new": total("received", "extracted", "scored", "vendor_resolved", "matched", "coded", "tax_checked"),
                "needs_review": total("in_review"),
                "approved": total("approved", "scheduled", "paid"),
                "posted": total("posted", "reconciled", "closed"),
                "exception": total("exception", "rejected", "resubmit", "quarantined"),
            }

    def post_job_count(self, client_id, document_id):
        with self._tx(client_id): return self.conn.execute("select count(*) as count from jobs where document_id=%s and queue='post'", (document_id,)).fetchone()["count"]

    def corrections(self, client_id, document_id):
        with self._tx(client_id):
            rows=self.conn.execute("select field,old_value,new_value,vendor_gstin,created_at from correction_events where document_id=%s order by id desc",(document_id,)).fetchall()
            return [dict(r) for r in rows]

    def inbox(self, client_id, queue="needs_review"):
        statuses = {"new": ("received","extracted","scored","vendor_resolved","matched","coded","tax_checked"),
                    "needs_review": ("in_review",), "approved": ("approved","scheduled","paid"),
                    "posted": ("posted","reconciled","closed"),
                    "exception": ("exception","rejected","resubmit","quarantined")}
        if queue not in statuses: raise ValueError("unknown queue")
        with self._tx(client_id):
            rows = self.conn.execute("""select d.id as document_id,d.original_filename,d.status,d.status_reason,
                 d.document_class,c.payload->>'invoice_no' as invoice_no,c.payload->'vendor'->>'name' as vendor_name,
                 c.payload->>'invoice_date' as invoice_date,
                 (c.payload->>'total')::numeric as total,r.score,r.band,ej.extract_state
              from documents d left join canonical_invoices c on c.document_id=d.id
              left join lateral(select score,band from risk_assessments where document_id=d.id order by id desc limit 1) r on true
              left join lateral(select state as extract_state from jobs
                                where document_id=d.id and queue='extract' order by id desc limit 1) ej on true
              where d.status=any(%s) order by d.received_at desc""", (list(statuses[queue]),)).fetchall()
            return [dict(r) for r in rows]

    def review_detail(self, client_id, document_id, source_url=None, *, actor_id=None):
        with self._tx(client_id):
            row = self.conn.execute("""select d.id as document_id,d.original_filename as filename,d.status,d.received_at,d.object_path,d.virus_ok,
                c.payload as invoice,c.maker_id,v.payload as draft,v.created_by,v.revision,r.score,r.band,r.marks
              from documents d left join canonical_invoices c on c.document_id=d.id
              left join voucher_drafts v on v.document_id=d.id
              left join lateral(select score,band,marks from risk_assessments where document_id=d.id order by id desc limit 1) r on true
              where d.id=%s""", (document_id,)).fetchone()
            if not row: raise NotFound("review not found")
            out = dict(row); out["risk"]={"score":out.pop("score"),"band":out.pop("band"),"marks":out.pop("marks") or []}
            out["invoice"], out["draft"] = out["invoice"] or {}, out["draft"] or {}
            out["revision"] = out["revision"] or 0
            out["ai_paragraph"] = out["draft"].get("ai_paragraph", "No AI summary was supplied with this draft.")
            path, clean = out.pop("object_path"), out.pop("virus_ok") is True
            out["source_url"] = source_url(path, out["filename"]) if source_url and clean and out["status"] != "quarantined" else ""
            maker, created_by = out.pop("maker_id"), out.pop("created_by")
            if actor_id is not None:
                roles = self._roles(actor_id, client_id)
                own_edit = str(actor_id) == str(maker) or str(actor_id) in self._editor_ids(document_id, created_by)
                out["can_edit"] = bool(out["draft"]) and out["status"] not in {"scheduled", "paid", "posted", "reconciled", "closed"} and bool(roles & {Role.AP_CLERK, Role.APPROVER, Role.FIRM_ADMIN})
                out["can_approve"] = bool(out["draft"]) and out["status"] in {"in_review", "approved"} and not own_edit and bool(roles & {Role.APPROVER, Role.FIRM_ADMIN})
                out["approval_note"] = "Another reviewer must approve a bill you made or edited." if own_edit and out["draft"] else ""
            return out


class _TenantTransaction:
    def __init__(self, conn, client_id): self.conn,self.client_id=conn,client_id
    def __enter__(self):
        self.tx=self.conn.transaction(); self.tx.__enter__()
        self.conn.execute("select set_config('app.client_id',%s,true)",(str(self.client_id),)); return self.conn
    def __exit__(self,*args): return self.tx.__exit__(*args)

