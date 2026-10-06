"""Transactional control-plane service. UI/API call this; neither writes workflow state directly."""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from .boundary import validate_draft, validate_invoice
from .risk import RiskContext, score_invoice
from .workflow import Action, Policy, Role, TransitionContext, decide_route, transition, InvalidTransition


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
                self._set_status(client_id, document_id, "tax_checked", "approved", "policy_clear", actor_id)
                self.conn.execute("insert into approvals(client_id,document_id,kind,revision) values(%s,%s,'policy_clear',1)", (client_id, document_id))
                self._enqueue_post(client_id, document_id, 1)
            return {"route": route.value, "status": "in_review" if route.value == "review" else "approved", "revision": 1}

    def _enqueue_post(self, client_id, document_id, revision):
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

    def approve(self, client_id, document_id, actor_id, *, post, expected_revision):
        with self._tx(client_id):
            row = self.conn.execute("""select d.status,v.revision,c.maker_id from documents d
                join voucher_drafts v on v.document_id=d.id join canonical_invoices c on c.document_id=d.id
                where d.id=%s for update of d,v""", (document_id,)).fetchone()
            if not row: raise NotFound("review not found")
            if row["status"] == "approved" and row["revision"] == expected_revision:
                if post: self._enqueue_post(client_id, document_id, row["revision"])
                return {"status":"approved","revision":row["revision"]}
            if row["revision"] != expected_revision: raise Conflict(f"revision changed from {expected_revision} to {row['revision']}")
            roles = self._roles(actor_id, client_id)
            try: new = transition(row["status"], Action.APPROVE_DRAFT if not post else Action.APPROVE,
                                  TransitionContext(str(actor_id), roles, maker_id=str(row["maker_id"])))
            except InvalidTransition as exc: raise Forbidden(str(exc)) from exc
            self.conn.execute("update documents set status=%s,updated_at=now() where id=%s", (new, document_id))
            self._event(client_id, document_id, row["status"], new, "approve_and_post" if post else "approve_draft", actor_id)
            self.conn.execute("update review_tasks set status='approved',decided_by=%s,decided_at=now() where document_id=%s and status='open'", (actor_id, document_id))
            self.conn.execute("insert into approvals(client_id,document_id,kind,actor_id,revision) values(%s,%s,'human',%s,%s) on conflict do nothing",
                              (client_id, document_id, actor_id, row["revision"]))
            if post: self._enqueue_post(client_id, document_id, row["revision"])
            return {"status":new,"revision":row["revision"]}

    def edit_field(self, client_id, document_id, actor_id, field, value, *, expected_revision):
        allowed = {"party_ledger", "narration", "bill_wise.ref", "bill_wise.due_date"}
        if field not in allowed: raise Forbidden("field is not editable here")
        with self._tx(client_id):
            row = self.conn.execute("""select v.payload,v.revision,d.status,
                exists(select 1 from jobs j where j.document_id=d.id and j.queue='post' and j.state in ('running','done')) active_post
                from voucher_drafts v join documents d on d.id=v.document_id where v.document_id=%s for update of v,d""", (document_id,)).fetchone()
            if not row: raise NotFound("draft not found")
            if row["status"] in {"posted","reconciled","closed","scheduled","paid"}: raise Conflict(f"cannot edit {row['status']} document")
            if row["active_post"]: raise Conflict("cannot edit while an active post attempt exists")
            if row["revision"] != expected_revision: raise Conflict("draft changed; refresh before saving")
            payload = row["payload"]; target, leaf = (payload, field)
            if "." in field:
                head, leaf = field.split(".",1); target = payload.setdefault(head,{})
            old = target.get(leaf); target[leaf] = value; revision = row["revision"] + 1
            self.conn.execute("update voucher_drafts set payload=%s,revision=%s,updated_at=now() where document_id=%s", (Jsonb(payload), revision, document_id))
            if row["status"] == "approved":
                self.conn.execute("update approvals set superseded_at=now(),superseded_by_revision=%s where document_id=%s and superseded_at is null", (revision,document_id))
                self.conn.execute("update jobs set state='cancelled',updated_at=now() where document_id=%s and queue='post' and state='queued'", (document_id,))
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

    def erp_result(self, client_id, document_id, *, ok, job_id, revision, idempotency_key, erp_id=None, error=None):
        with self._tx(client_id):
            row = self.conn.execute("""select d.status,p.id,p.revision,p.idempotency_key,p.result
                from documents d join posting_attempts p on p.document_id=d.id
                where d.id=%s and p.job_id=%s for update of d,p""", (document_id,job_id)).fetchone()
            if not row: raise NotFound("document not found")
            if row["revision"] != revision: raise Conflict("posting callback revision mismatch")
            if row["idempotency_key"] != idempotency_key: raise Conflict("posting callback idempotency key mismatch")
            if row["result"] != "pending": return {"status":"posted" if row["result"] == "posted" else "exception"}
            action = Action.POSTED if ok else Action.ERP_FAILED
            try: new = transition(row["status"], action, TransitionContext("person4", set()))
            except InvalidTransition as exc: raise Conflict(str(exc)) from exc
            self.conn.execute("update documents set status=%s,status_reason=%s,updated_at=now() where id=%s", (new,error,document_id))
            self.conn.execute("update posting_attempts set result=%s,erp_id=%s,error=%s,completed_at=now() where id=%s",
                              ("posted" if ok else "failed",erp_id,error,row["id"]))
            self._event(client_id,document_id,row["status"],new,"erp_posted" if ok else "erp_failed",metadata={"erp_id":erp_id,"error":error})
            return {"status":new}

    def post_job(self, client_id, document_id):
        with self._tx(client_id):
            row = self.conn.execute("select id,payload,state from jobs where document_id=%s and queue='post' order by id desc limit 1", (document_id,)).fetchone()
            if not row: raise NotFound("post job not found")
            return dict(row)

    def queue_counts(self, client_id):
        with self._tx(client_id):
            rows = self.conn.execute("select status,count(*) from documents group by status").fetchall()
            d = {r["status"]: r["count"] for r in rows}; return {"new":d.get("received",0)+d.get("extracted",0),"needs_review":d.get("in_review",0),
                                    "approved":d.get("approved",0),"posted":d.get("posted",0),"exception":d.get("exception",0)}

    def post_job_count(self, client_id, document_id):
        with self._tx(client_id): return self.conn.execute("select count(*) as count from jobs where document_id=%s and queue='post'", (document_id,)).fetchone()["count"]

    def corrections(self, client_id, document_id):
        with self._tx(client_id):
            rows=self.conn.execute("select field,old_value,new_value,vendor_gstin,created_at from correction_events where document_id=%s order by id desc",(document_id,)).fetchall()
            return [dict(r) for r in rows]

    def inbox(self, client_id, queue="needs_review"):
        statuses = {"new": ("received","extracted","scored","vendor_resolved","matched","coded","tax_checked"),
                    "needs_review": ("in_review",), "approved": ("approved","scheduled","paid"),
                    "posted": ("posted","reconciled","closed"), "exception": ("exception","rejected","resubmit")}
        if queue not in statuses: raise ValueError("unknown queue")
        with self._tx(client_id):
            rows = self.conn.execute("""select d.id as document_id,d.original_filename,d.status,d.received_at,
                 c.payload->>'invoice_no' as invoice_no,c.payload->'vendor'->>'name' as vendor_name,
                 coalesce((c.payload->>'total')::numeric,0) as total,r.score,r.band
              from documents d left join canonical_invoices c on c.document_id=d.id
              left join lateral(select score,band from risk_assessments where document_id=d.id order by id desc limit 1) r on true
              where d.status=any(%s) order by d.received_at desc""", (list(statuses[queue]),)).fetchall()
            return [dict(r) for r in rows]

    def review_detail(self, client_id, document_id, source_url=None):
        with self._tx(client_id):
            row = self.conn.execute("""select d.id as document_id,d.original_filename as filename,d.status,d.received_at,d.object_path,
                c.payload as invoice,v.payload as draft,v.revision,r.score,r.band,r.marks
              from documents d join canonical_invoices c on c.document_id=d.id
              join voucher_drafts v on v.document_id=d.id
              join lateral(select score,band,marks from risk_assessments where document_id=d.id order by id desc limit 1) r on true
              where d.id=%s""", (document_id,)).fetchone()
            if not row: raise NotFound("review not found")
            out = dict(row); out["risk"]={"score":out.pop("score"),"band":out.pop("band"),"marks":out.pop("marks")}
            out["ai_paragraph"] = out["draft"].get("ai_paragraph", "No AI summary was supplied with this draft.")
            out["source_url"] = source_url(out.pop("object_path"), out["filename"]) if source_url else ""
            return out


class _TenantTransaction:
    def __init__(self, conn, client_id): self.conn,self.client_id=conn,client_id
    def __enter__(self):
        self.tx=self.conn.transaction(); self.tx.__enter__()
        self.conn.execute("select set_config('app.client_id',%s,true)",(str(self.client_id),)); return self.conn
    def __exit__(self,*args): return self.tx.__exit__(*args)

