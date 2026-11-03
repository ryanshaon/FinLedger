import { useEffect, useState, type FormEvent } from "react";
import { ApiError, api, errorMessage } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { bandText, rupees, safeSource, shown } from "@/format";
import { PageHeader } from "@/pages/Placeholder";
import { queueHref } from "@/router";
import type { InvoiceResponse, Mark } from "@/types";
import { partyLedgerName, voucherRows } from "@/voucherView";

const EDIT_ROLES = ["ap_clerk", "approver", "firm_admin"];
const APPROVE_ROLES = ["approver", "firm_admin"];
const TAX_FIELDS = [
  ["taxable", "Taxable value"],
  ["cgst", "CGST"],
  ["sgst", "SGST"],
  ["igst", "IGST"],
  ["cess", "Cess"],
  ["round_off", "Round off"],
] as const;

export function InvoicePage({
  clientId,
  documentId,
  onNavigate,
}: {
  clientId: string;
  documentId: string;
  onNavigate: (url: string) => void;
}) {
  const [page, setPage] = useState<InvoiceResponse | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [ledger, setLedger] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    setPage(null);
    setError("");
    setNotice("");
    api.invoice(clientId, documentId)
      .then((next) => {
        if (!live) return;
        setPage(next);
        setLedger(next.invoice.draft?.party_ledger || "");
      })
      .catch((reason: unknown) => {
        if (live) setError(errorMessage(reason, "This bill is not ready for review yet."));
      });
    return () => { live = false; };
  }, [clientId, documentId]);

  async function reload() {
    const next = await api.invoice(clientId, documentId);
    setPage(next);
    setLedger(next.invoice.draft?.party_ledger || "");
  }

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      await reload();
      setNotice(done);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        try {
          await reload();
          setError(`${errorMessage(reason, "This bill changed.")} The latest version is shown below; check it and try again.`);
        } catch {
          setError("This bill changed, but the latest version could not be loaded. Refresh before trying again.");
        }
      } else {
        setError(errorMessage(reason, "The desk could not save that."));
      }
    } finally {
      setBusy(false);
    }
  }

  function save(event: FormEvent) {
    event.preventDefault();
    if (!page) return;
    void run(
      () => api.saveChange(clientId, documentId, page.invoice.revision, ledger),
      "Party ledger saved.",
    );
  }

  if (error && !page) {
    return (
      <div className="space-y-4">
        <PageHeader title="Invoice" description="Review stays on this desk." />
        <div className="rounded-xl border border-border bg-card px-4 py-8 text-center">
          <p role="alert" className="text-sm">{error}</p>
          <Button variant="outline" className="mt-4" onClick={() => onNavigate(queueHref(clientId, "needs_review"))}>Back to the queue</Button>
        </div>
      </div>
    );
  }

  if (!page) {
    return (
      <div className="space-y-4">
        <PageHeader title="Invoice" description="Opening the bill." />
        <div className="h-64 animate-pulse rounded-xl border border-border bg-card" />
      </div>
    );
  }

  const detail = page.invoice;
  const invoice = detail.invoice || {};
  const vendor = invoice.vendor || {};
  const draft = detail.draft || {};
  const risk = detail.risk || {};
  const marks = risk.marks || [];
  const conf = invoice.confidences || {};
  const source = safeSource(detail.source_url);
  const partyLedger = partyLedgerName(draft);
  const rows = voucherRows(draft.lines);
  const hasRole = (allowed: string[]) => page.roles.some((role) => allowed.includes(role));
  const processed = detail.revision > 0;
  const inReview = processed && detail.status === "in_review";
  const canEdit = processed && (detail.status === "in_review" || detail.status === "approved")
    && (detail.can_edit ?? hasRole(EDIT_ROLES));
  const canApprove = inReview && (detail.can_approve ?? hasRole(APPROVE_ROLES));
  const canSendBack = inReview && hasRole(APPROVE_ROLES);
  const back = queueHref(clientId, "needs_review");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <PageHeader
          title={detail.filename || invoice.invoice_no || "Invoice"}
          description={`${page.client.name} · Revision ${detail.revision} · ${detail.state_label}`}
        />
        <div className="flex gap-2">
          <Button variant="outline" disabled={busy} onClick={() => void run(() => Promise.resolve(), "Bill refreshed.")}>Refresh bill</Button>
          <Button variant="outline" onClick={() => onNavigate(back)}>Back to the queue</Button>
        </div>
      </div>
      {error ? <p role="alert" className="rounded-md border border-border bg-muted px-3 py-2 text-sm">{error}</p> : null}
      {notice ? <p role="status" className="rounded-md border border-border bg-card px-3 py-2 text-sm">{notice}</p> : null}
      {!processed ? (
        <p role="status" className="rounded-md border border-border bg-card px-3 py-2 text-sm">
          This bill has been received but is not ready for review yet. The original file is kept below.
        </p>
      ) : null}
      <div className="grid gap-3 xl:grid-cols-3">
        <section className="rounded-xl border border-border bg-card" aria-label="Source document">
          <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold">Source document</h2>
            {source ? (
              <a href={source} target="_blank" rel="noopener noreferrer" className="text-sm font-medium underline">Open original</a>
            ) : null}
          </div>
          {source ? (
            <iframe title="Source document" src={source} className="h-80 w-full rounded-b-xl bg-background" />
          ) : (
            <p className="px-4 py-8 text-sm text-muted-foreground">The original file is kept. Preview stays unavailable until the file passes its safety checks.</p>
          )}
        </section>
        <section className="rounded-xl border border-border bg-card" aria-label="Extracted fields">
          <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">Extracted fields</h2>
          <dl className="divide-y divide-border px-4">
            <Field label="Vendor" value={shown(vendor.name)} />
            <Field label="GSTIN" value={shown(vendor.gstin)} extra={percent(conf.gstin)} />
            <Field label="Invoice number" value={shown(invoice.invoice_no)} extra={percent(conf.invoice_no)} />
            <Field label="Invoice date" value={shown(invoice.invoice_date)} extra={percent(conf.date)} />
            <Field label="Buyer GSTIN" value={shown(invoice.buyer_gstin)} />
            {TAX_FIELDS.filter(([key]) => present(invoice[key])).map(([key, label]) => (
              <Field key={key} label={label} value={rupees(invoice[key])} />
            ))}
            <Field label="Grand total" value={rupees(invoice.total)} extra={percent(conf.total)} />
          </dl>
        </section>
        <section className="rounded-xl border border-border bg-card" aria-label="Proposed voucher">
          <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">Proposed voucher</h2>
          <div className="space-y-4 p-4">
            <p className="rounded-md border border-border bg-muted px-3 py-2 text-sm">
              <span className="block text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">Risk gate</span>
              <span className="font-medium">{bandText(risk.band, risk.score)}</span>
            </p>
            <div>
              <h3 className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">Checks</h3>
              {marks.length === 0 ? (
                <p className="mt-1 text-sm text-muted-foreground">No checks have run yet.</p>
              ) : (
                <ul className="mt-1 space-y-1 text-sm">
                  {marks.map((mark, index) => (
                    <li key={`${mark.check ?? mark.field ?? "check"}-${index}`}>
                      <span className="font-medium">{checkLabel(mark)}</span>: {markState(mark)}
                      {mark.note ? <span className="text-muted-foreground"> — {mark.note}</span> : null}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="space-y-3">
              <div>
                <h3 className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">Party ledger</h3>
                <p className="mt-1 text-sm font-medium">{shown(partyLedger)}</p>
              </div>
              <table className="w-full border-collapse text-sm" aria-label="Voucher lines">
                <thead>
                  <tr className="border-b border-border text-left text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
                    <th className="py-1.5 font-medium">Ledger</th>
                    <th className="py-1.5 font-medium">Side</th>
                    <th className="py-1.5 text-right font-medium">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 ? (
                    <tr>
                      <td colSpan={3} className="py-2 text-muted-foreground">No voucher lines</td>
                    </tr>
                  ) : rows.map((row, index) => (
                    <tr key={`${row.ledger}-${index}`} className="border-b border-border last:border-0">
                      <td className="py-1.5">{row.ledger}</td>
                      <td className="py-1.5">{row.side}</td>
                      <td className="py-1.5 text-right">{rupees(row.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <dl className="divide-y divide-border">
              <Field label="GST treatment" value={shown(draft.gst?.treatment)} />
              <Field label="Bill reference" value={shown(draft.bill_wise?.ref)} />
            </dl>
            {detail.ai_paragraph ? <p className="text-sm leading-6 text-muted-foreground">{detail.ai_paragraph}</p> : null}
            <ul className="space-y-1 text-sm text-muted-foreground">
              {(draft.reasons || []).length === 0
                ? <li>No mapping reasons supplied.</li>
                : draft.reasons?.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
            {canEdit ? (
              <form className="space-y-2" onSubmit={save}>
                <label className="block text-sm font-medium">
                  Edit party ledger
                  <Input className="mt-1.5" value={ledger} maxLength={4000} onChange={(event) => setLedger(event.target.value)} />
                </label>
                <Button type="submit" variant="outline" disabled={busy}>Save edit</Button>
              </form>
            ) : null}
            {canApprove || canSendBack ? (
              <div className="flex flex-wrap gap-2">
                {canApprove ? (
                  <Button disabled={busy} onClick={() => void run(() => api.approve(clientId, documentId, detail.revision, false), "Approved.")}>
                    Approve
                  </Button>
                ) : null}
                {canSendBack ? (
                  <Button variant="outline" disabled={busy} onClick={() => void run(() => api.sendBack(clientId, documentId, detail.revision), "Sent back for a correction.")}>
                    Ask vendor to resend
                  </Button>
                ) : null}
              </div>
            ) : null}
            {inReview && !canApprove && detail.approval_note ? (
              <p className="text-sm text-muted-foreground">{detail.approval_note}</p>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}

function Field({ label, value, extra }: { label: string; value: string; extra?: string }) {
  return (
    <div className="py-2">
      <dt className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
        {label}{extra ? <span className="ml-2 normal-case tracking-normal">{extra}</span> : null}
      </dt>
      <dd className="mt-0.5 text-sm">{value}</dd>
    </div>
  );
}

function present(value: unknown): boolean {
  return value !== null && value !== undefined && value !== "";
}

function markState(mark: Mark): string {
  if (mark.ok === true) return "Passed";
  if (mark.ok === false) return "Needs attention";
  return "Not checked";
}

function checkLabel(mark: Mark): string {
  const text = (mark.check || mark.field || "control").replace(/_/g, " ");
  return text.slice(0, 1).toUpperCase() + text.slice(1);
}

function percent(value: number | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return `${Math.round(value * 100)}%`;
}
