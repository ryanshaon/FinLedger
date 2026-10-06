"""Vendor-facing pages for the unique link. Server-rendered, no external assets, works without JavaScript."""
from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_CSS = """
:root{--paper:#eef2f5;--sheet:#fff;--ink:#1c2430;--muted:#566273;--rule:#c9d3dd;--blue:#1d3a8a;--blue-2:#16306f;
--stamp:#5b3fa0;--err:#a3261b;--err-bg:#fbecea}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans",sans-serif}
main{max-width:34rem;margin:0 auto;padding:2rem 1rem 3rem}
.sheet{background:var(--sheet);border:1px solid var(--rule);border-radius:6px;padding:1.5rem 1.25rem}
h1{font-size:1.5rem;line-height:1.25;margin:0 0 .5rem;letter-spacing:-.01em}
p{margin:0 0 1rem}
.lede{color:var(--muted)}
label{display:block;font-weight:600;margin:1.25rem 0 .35rem}
.hint{font-weight:400;color:var(--muted);font-size:.875rem}
.drop{position:relative;display:block;border:2px dashed var(--rule);border-radius:6px;padding:1.5rem 1rem;text-align:center;
background:#f7f9fb;cursor:pointer;margin-top:1rem}
.drop:hover,.drop.over{border-color:var(--blue);background:#f1f4fb}
.drop strong{display:block;color:var(--blue);font-size:1.05rem}
.drop input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%}
.drop:focus-within{outline:3px solid var(--blue);outline-offset:2px}
#picked{list-style:none;padding:0;margin:.75rem 0 0;font-size:.9rem}
#picked li{padding:.35rem 0;border-bottom:1px solid var(--rule);overflow-wrap:anywhere}
input[type=text],textarea{width:100%;font:inherit;padding:.6rem .7rem;border:1px solid var(--rule);border-radius:4px;background:#fff;color:var(--ink)}
textarea{min-height:4.5rem;resize:vertical}
input[type=text]:focus,textarea:focus,button:focus-visible{outline:3px solid var(--blue);outline-offset:1px}
button{margin-top:1.5rem;width:100%;font:inherit;font-weight:600;font-size:1.05rem;padding:.85rem 1rem;border:0;border-radius:4px;
background:var(--blue);color:#fff;cursor:pointer}
button:hover{background:var(--blue-2)}
button[disabled]{opacity:.6;cursor:progress}
.fine{font-size:.875rem;color:var(--muted);margin:1.25rem 0 0}
.fine code{font-family:inherit;font-weight:600;color:var(--ink);overflow-wrap:anywhere}
.stamp{display:inline-block;border:3px solid var(--stamp);color:var(--stamp);border-radius:8px;padding:.6rem 1rem .5rem;
transform:rotate(-3deg);margin:.25rem 0 1.25rem;animation:press .35s ease-out both}
.stamp b{display:block;font-size:1.6rem;line-height:1;letter-spacing:.06em}
.stamp span{font-size:.85rem;font-variant-numeric:tabular-nums}
@keyframes press{from{transform:rotate(-3deg) scale(1.25);opacity:0}to{transform:rotate(-3deg) scale(1);opacity:1}}
@media (prefers-reduced-motion:reduce){.stamp{animation:none}}
table{width:100%;border-collapse:collapse;font-size:.9rem;margin:0 0 1rem}
th,td{text-align:left;padding:.45rem .25rem;border-bottom:1px solid var(--rule);vertical-align:top;overflow-wrap:anywhere}
th{color:var(--muted);font-weight:600}
td.ref{font-variant-numeric:tabular-nums;white-space:nowrap}
.problem{background:var(--err-bg);border-left:4px solid var(--err);padding:.75rem 1rem;margin:0 0 1rem;border-radius:0 4px 4px 0}
.problem h2{font-size:1rem;margin:0 0 .35rem;color:var(--err)}
.problem ul{margin:0;padding-left:1.1rem}
a{color:var(--blue)}
"""

_JS = """
const input=document.getElementById('files'),list=document.getElementById('picked'),drop=input.closest('.drop');
input.addEventListener('change',()=>{list.replaceChildren(...[...input.files].map(f=>{const li=document.createElement('li');
li.textContent=f.name+' ('+Math.max(1,Math.round(f.size/1024))+' KB)';return li}))});
['dragenter','dragover'].forEach(e=>drop.addEventListener(e,()=>drop.classList.add('over')));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,()=>drop.classList.remove('over')));
document.querySelector('form').addEventListener('submit',e=>{if(!input.files.length){e.preventDefault();input.focus();return}
const b=e.target.querySelector('button');b.disabled=true;b.textContent='Sending…'});
"""


def _page(title: str, body: str, script: str = "") -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
            f'<title>{escape(title)}</title><style>{_CSS}</style></head><body><main>{body}</main>'
            + (f"<script>{script}</script>" if script else "") + "</body></html>")


def _doors_note(client_name: str, inbound_address: str) -> str:
    return (f'<p class="fine">{escape(client_name)} only receives bills sent to this page or emailed to '
            f'<code>{escape(inbound_address)}</code>. Bills sent anywhere else, including personal email or '
            f'WhatsApp, are not received.</p>')


def upload_page(client_name: str, action: str, inbound_address: str, max_mb: int) -> str:
    name = escape(client_name)
    body = f"""
<div class="sheet">
  <h1>Send a bill to {name}</h1>
  <p class="lede">Your invoice goes straight to {name}'s accounts team. One invoice per file works best.</p>
  <form method="post" action="{escape(action)}" enctype="multipart/form-data">
    <label class="drop" for="files"><strong>Choose invoice files</strong>
      <span class="hint">PDF, photo (JPG, PNG), Excel statement or a ZIP. Up to {max_mb} MB.</span>
      <input id="files" name="files" type="file" multiple required
             accept=".pdf,.jpg,.jpeg,.png,.xlsx,.zip,application/pdf,image/jpeg,image/png">
    </label>
    <ul id="picked" aria-live="polite"></ul>
    <label for="po">PO number <span class="hint">if {name} gave you one</span></label>
    <input id="po" name="po_number" type="text" maxlength="60" autocomplete="off">
    <label for="note">Note for the accounts team <span class="hint">optional</span></label>
    <textarea id="note" name="note" maxlength="500"></textarea>
    <button type="submit">Send bill</button>
  </form>
  {_doors_note(client_name, inbound_address)}
</div>"""
    return _page(f"Send a bill to {client_name}", body, _JS)


def receipt_page(client_name: str, received: list[dict], rejected: list[dict], again_url: str,
                 inbound_address: str) -> str:
    name = escape(client_name)
    now = datetime.now(IST).strftime("%d %b %Y, %H:%M IST")
    parts = ['<div class="sheet">']
    if received:
        rows = "".join(f'<tr><td>{escape(r["filename"])}</td><td class="ref">{escape(r["document_id"][:8].upper())}</td></tr>'
                       for r in received)
        parts.append(f'<div class="stamp" role="img" aria-label="Received"><b>RECEIVED</b><span>{now}</span></div>')
        parts.append(f"<h1>{name} has your bill</h1>")
        parts.append("<p class=\"lede\">Keep the reference if you need to follow up. "
                     "If anything is unclear, the accounts team will ask you to resend.</p>")
        parts.append(f"<table><thead><tr><th>File</th><th>Reference</th></tr></thead><tbody>{rows}</tbody></table>")
    else:
        parts.append("<h1>Nothing was sent</h1>")
    if rejected:
        items = "".join(f"<li>{escape(r['filename'])}: {escape(r['reason'])}</li>" for r in rejected)
        parts.append(f'<div class="problem"><h2>Not accepted</h2><ul>{items}</ul></div>')
    parts.append(f'<p><a href="{escape(again_url)}">Send another bill</a></p>')
    parts.append(_doors_note(client_name, inbound_address))
    parts.append("</div>")
    return _page(f"Bill received by {client_name}" if received else "Nothing was sent", "".join(parts))


def not_found_page() -> str:
    body = """<div class="sheet"><h1>This upload link is not active</h1>
<p>The link may have been replaced with a new one. Ask your customer's accounts team for their current bill upload link
or invoice email address.</p></div>"""
    return _page("Upload link not active", body)


def message_page(title: str, text: str) -> str:
    return _page(title, f'<div class="sheet"><h1>{escape(title)}</h1><p>{escape(text)}</p></div>')
