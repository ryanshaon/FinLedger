"""Server-rendered reviewer UI. No workflow decisions live here."""
from __future__ import annotations
from html import escape
from typing import Any


CSS = r"""
:root{--ink:#17211d;--muted:#66736c;--paper:#f7f8f4;--panel:#fff;--line:#d9dfda;--green:#174c3b;
--amber:#bd6b18;--amber-bg:#fff4df;--red:#a5362f;--red-bg:#fff0ed;--blue:#285a7b;--focus:#0b70d1}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.45 "Segoe UI",Arial,sans-serif}
a{color:inherit}.topbar{height:62px;background:var(--green);color:white;display:flex;align-items:center;justify-content:space-between;padding:0 24px}
.brand{font:700 18px Georgia,serif;letter-spacing:.2px}.brand small{font:400 12px "Segoe UI";opacity:.72;margin-left:10px}
.shell{max-width:1600px;margin:auto;padding:22px}.queue-tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);overflow:auto}
.queue-tabs a{padding:11px 14px;text-decoration:none;white-space:nowrap;border-bottom:3px solid transparent}.queue-tabs a.active{border-color:var(--amber);font-weight:650}
.count{display:inline-block;min-width:22px;text-align:center;background:#e7ece8;border-radius:12px;font-size:12px;margin-left:5px;padding:1px 6px}
.ledger{width:100%;border-collapse:collapse;background:white;margin-top:18px}.ledger th{text-align:left;color:var(--muted);font-size:12px;font-weight:600;padding:10px 14px;border-bottom:1px solid var(--line)}
.ledger td{padding:14px;border-bottom:1px solid #edf0ed}.ledger tr:hover td{background:#f9fbf9}.risk{font-weight:700}.risk.medium{color:var(--amber)}.risk.high{color:var(--red)}
.empty{background:white;border:1px dashed #b9c2bc;padding:56px;text-align:center;margin-top:20px}.empty h2{font:600 23px Georgia;margin:0 0 8px}
.review-head{display:flex;align-items:flex-end;justify-content:space-between;margin:0 0 16px}.review-head h1{font:600 25px Georgia;margin:0}.meta{color:var(--muted);font-size:13px}
.review-grid{display:grid;grid-template-columns:minmax(310px,1.15fr) minmax(285px,.95fr) minmax(340px,1.1fr);gap:1px;background:var(--line);border:1px solid var(--line);min-height:calc(100vh - 125px)}
.review-column{background:var(--panel);min-width:0}.column-head{height:49px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 16px;font-weight:650}
.source-frame{width:100%;height:calc(100vh - 177px);border:0;background:#4b514e}.source-missing{padding:34px;color:var(--muted)}
.fields,.voucher{padding:8px 16px 24px}.field{padding:12px 0;border-bottom:1px solid #e9edea}.field-name{font-size:12px;color:var(--muted);display:flex;justify-content:space-between}.field-value{font-size:15px;margin-top:3px;word-break:break-word}
.confidence{font-variant-numeric:tabular-nums;color:var(--green)}.mark{margin-top:7px;padding:7px 9px;background:var(--red-bg);border-left:3px solid var(--red);color:#6e2420;font-size:12px}
.band{padding:13px 15px;border-left:5px solid var(--amber);background:var(--amber-bg);display:flex;justify-content:space-between;align-items:center}.band.high{border-color:var(--red);background:var(--red-bg)}
.band strong{font-size:19px}.ai-note{font:15px/1.55 Georgia,serif;background:#f0f4f1;padding:14px;margin:15px 0}.reason{font-size:13px;padding:7px 0;border-bottom:1px solid #edf0ed}
.edit-row{display:grid;grid-template-columns:1fr auto;gap:8px;margin:14px 0}input,textarea,select{width:100%;border:1px solid #aeb9b2;background:white;padding:10px;font:inherit}
button{border:1px solid var(--green);background:var(--green);color:white;padding:10px 14px;font-weight:650;cursor:pointer}button.secondary{background:white;color:var(--green)}button.danger{border-color:var(--red);background:white;color:var(--red)}
.actions{position:sticky;bottom:0;background:white;border-top:1px solid var(--line);padding:13px 16px;display:flex;gap:8px;flex-wrap:wrap}a:focus-visible,button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
@media (max-width: 980px){.review-grid{grid-template-columns:1fr}.source-frame{height:65vh}.review-head{align-items:flex-start;flex-direction:column;gap:6px}.shell{padding:14px}.topbar{padding:0 14px}}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
"""


def _page(title, body):
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title><style>{CSS}</style></head><body><header class="topbar"><div class="brand">FinLedger <small>review desk</small></div><div>Control plane</div></header>{body}</body></html>'


def render_login(csrf_token: str, error: str = "") -> str:
    alert = f'<p role="alert" class="mark">{escape(error)}</p>' if error else ""
    body = (f'<main class="shell" style="max-width:480px"><h1>Sign in</h1>{alert}'
            f'<form method="post" action="/login"><input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">'
            '<p><label>Email<br><input name="email" type="email" autocomplete="username" required maxlength="320"></label></p>'
            '<p><label>Password<br><input name="password" type="password" autocomplete="current-password" required maxlength="1024"></label></p>'
            '<button type="submit">Sign in</button></form></main>')
    return _page("Sign in · FinLedger", body)


def render_home(clients: list[dict], csrf_token: str) -> str:
    links = ''.join(f'<li><a href="/clients/{escape(str(c["id"]), quote=True)}/review">{escape(c["name"])}</a></li>'
                    for c in clients)
    body = (f'<main class="shell"><div class="review-head"><h1>Your clients</h1>'
            f'<form method="post" action="/logout"><input type="hidden" name="csrf_token" '
            f'value="{escape(csrf_token, quote=True)}"><button class="secondary">Sign out</button></form></div>'
            f'<ul>{links}</ul></main>')
    return _page("Clients · FinLedger", body)


def render_mfa(csrf_token: str, factor_id: str = "", secret: str = "", error: str = "") -> str:
    alert = f'<p role="alert" class="mark">{escape(error)}</p>' if error else ""
    if factor_id:
        setup = (f'<p>Add this setup key to your authenticator app: <code>{escape(secret)}</code></p>'
                 if secret else '<p>Enter the six-digit code from your authenticator app.</p>')
        form = (f'<form method="post" action="/mfa/verify"><input type="hidden" name="csrf_token" '
                f'value="{escape(csrf_token, quote=True)}"><input type="hidden" name="factor_id" '
                f'value="{escape(factor_id, quote=True)}"><label>Authenticator code<br>'
                '<input name="code" inputmode="numeric" autocomplete="one-time-code" '
                'pattern="[0-9]{6}" maxlength="6" required></label><p><button>Verify code</button></p></form>')
    else:
        setup = '<p>Set up two-step verification with a TOTP authenticator app.</p>'
        form = (f'<form method="post" action="/mfa/enroll"><input type="hidden" name="csrf_token" '
                f'value="{escape(csrf_token, quote=True)}"><button>Start setup</button></form>')
    return _page("Two-step verification · FinLedger",
                 f'<main class="shell" style="max-width:500px"><h1>Two-step verification</h1>{alert}{setup}{form}</main>')


def render_inbox(items, counts, active, client_name):
    labels=(("new","New"),("needs_review","Needs review"),("approved","Approved"),("posted","Posted"),("exception","Exception"))
    tabs=''.join(f'<a class="{"active" if k==active else ""}" href="?queue={k}">{v}<span class="count">{counts.get(k,0)}</span></a>' for k,v in labels)
    if items:
        rows=''.join(f'<tr><td><a href="review/{escape(str(x["document_id"]))}">{escape(str(x.get("invoice_no") or x.get("filename") or "Untitled"))}</a></td><td>{escape(str(x.get("vendor_name") or "Unresolved vendor"))}</td><td>₹{float(x.get("total") or 0):,.2f}</td><td><span class="risk {escape(str(x.get("band") or ""))}">{escape(str(x.get("band") or "pending").title())} {x.get("score") if x.get("score") is not None else ""}</span></td><td>{escape(str(x.get("status") or ""))}</td></tr>' for x in items)
        content=f'<table class="ledger"><thead><tr><th>Invoice</th><th>Vendor</th><th>Amount</th><th>Risk</th><th>State</th></tr></thead><tbody>{rows}</tbody></table>'
    else:
        empty={"needs_review":"No bills need review","new":"No new bills","approved":"No approved drafts","posted":"No posted bills","exception":"No exceptions"}[active]
        content=f'<div class="empty"><h2>{empty}</h2><p>The queue will update as bills move through controls.</p></div>'
    return _page(f'{client_name} · Review',f'<main class="shell"><div class="review-head"><div><h1>{escape(client_name)}</h1><div class="meta">Accounts payable control queue</div></div></div><nav class="queue-tabs" aria-label="Review queues">{tabs}</nav>{content}</main>')


def render_review(d: dict[str,Any], roles: set[str], csrf_token: str = ""):
    inv,risk,draft=d["invoice"],d["risk"],d["draft"]; marks=risk.get("marks") or []
    def field(name,label,value,conf=None):
        failures=[m for m in marks if m.get("field") in {name, name.replace("vendor.","")} and not m.get("ok")]
        flag=''.join(f'<div class="mark">{escape(str(m.get("check","control")))}: {escape(str(m.get("note","")))}</div>' for m in failures)
        c=f'<span class="confidence">{float(conf)*100:.0f}%</span>' if conf is not None else ''
        return f'<div class="field"><div class="field-name"><span>{escape(label)}</span>{c}</div><div class="field-value">{escape(str(value if value not in (None,"") else "—"))}</div>{flag}</div>'
    conf=inv.get("confidences") or {}; vendor=inv.get("vendor") or {}
    fields=''.join((field("vendor.name","Vendor",vendor.get("name")),field("vendor.gstin","GSTIN",vendor.get("gstin"),conf.get("gstin")),field("invoice_no","Invoice number",inv.get("invoice_no"),conf.get("invoice_no")),field("invoice_date","Invoice date",inv.get("invoice_date"),conf.get("date")),field("buyer_gstin","Buyer GSTIN",inv.get("buyer_gstin")),field("po_number","PO number",inv.get("po_number")),field("total","Grand total",f'₹{float(inv.get("total") or 0):,.2f}',conf.get("total"))))
    source=f'<iframe class="source-frame" title="Source document" src="{escape(str(d.get("source_url") or ""),quote=True)}"></iframe>' if d.get("source_url") else '<div class="source-missing">Source preview is unavailable. Open it from the document record.</div>'
    band=str(risk["band"]); band_title=f'{band.title()} · {risk["score"]}'
    reasons=''.join(f'<div class="reason">{escape(str(x))}</div>' for x in draft.get("reasons",[])) or '<div class="reason">No mapping reasons supplied.</div>'
    line='; '.join(f'{x.get("ledger","Unmapped")} — ₹{float(x.get("amount") or 0):,.2f}' for x in draft.get("lines",[]))
    voucher=''.join((field("party_ledger","Party ledger",draft.get("party_ledger")),field("lines","Voucher lines",line),field("gst","GST treatment",(draft.get("gst") or {}).get("treatment")),field("tds","TDS","Applicable" if (draft.get("tds") or {}).get("applicable") else "Not applicable"),field("bill_wise.ref","Bill reference",(draft.get("bill_wise") or {}).get("ref"))))
    csrf=f'<input type="hidden" name="csrf_token" value="{escape(csrf_token,quote=True)}">'
    editable=d.get("status") not in {"scheduled","paid","posted","reconciled","closed"}
    edit=(f'<form class="edit-row" method="post" action="{escape(str(d["document_id"]))}/edit">{csrf}<input type="hidden" name="revision" value="{d["revision"]}"><input type="hidden" name="field" value="party_ledger"><label><span class="field-name">Edit party ledger</span><input name="value" value="{escape(str(draft.get("party_ledger") or ""),quote=True)}"></label><button class="secondary">Save edit</button></form>' if editable else '')
    actions=''
    if roles & {"approver","firm_admin"}:
        actions=f'<div class="actions"><form method="post" action="{d["document_id"]}/approve">{csrf}<input type="hidden" name="revision" value="{d["revision"]}"><input type="hidden" name="post" value="true"><button>Approve &amp; post</button></form><form method="post" action="{d["document_id"]}/approve">{csrf}<input type="hidden" name="revision" value="{d["revision"]}"><input type="hidden" name="post" value="false"><button class="secondary">Approve draft only</button></form><form method="post" action="{d["document_id"]}/reject">{csrf}<input type="hidden" name="revision" value="{d["revision"]}"><input type="hidden" name="resubmit" value="true"><button class="danger">Ask vendor to resend</button></form></div>'
    body=f'<main class="shell"><div class="review-head"><div><h1>{escape(str(d.get("filename") or "Invoice review"))}</h1><div class="meta">Revision {d["revision"]} · {escape(str(d["status"]))}</div></div><a href="../review">Back to queue</a></div><section class="review-grid"><article class="review-column source" aria-label="Source document"><div class="column-head">Source document</div>{source}</article><article class="review-column extracted" aria-label="Extracted fields"><div class="column-head">Extracted fields</div><div class="fields">{fields}</div></article><article class="review-column proposed" aria-label="Proposed voucher"><div class="column-head">Proposed voucher</div><div class="band {escape(band)}"><span>Risk gate</span><strong>{escape(band_title)}</strong></div><div class="voucher">{voucher}<div class="ai-note">{escape(str(d.get("ai_paragraph") or ""))}</div>{reasons}{edit}</div>{actions}</article></section></main>'
    return _page(f'{d.get("filename","Invoice")} · Review',body)

