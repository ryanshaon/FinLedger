import { ArrowRight } from "lucide-react";
import { useEffect, useState } from "react";
import { errorMessage } from "@/api";
import { Button } from "@/components/ui/button";
import { asAmount, formatDay, rupees } from "@/format";
import { loadFirm, type LiveBill } from "@/live";
import { PageHeader } from "@/pages/Placeholder";
import { CardSkeleton, TableSkeleton } from "@/pages/states";
import { invoiceHref } from "@/router";
import type { ClientRow } from "@/types";

export function Dashboard({ onNavigate }: { onNavigate: (url: string) => void }) {
  const [clients, setClients] = useState<ClientRow[] | null>(null);
  const [bills, setBills] = useState<LiveBill[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    loadFirm()
      .then((firm) => {
        if (!live) return;
        setClients(firm.clients);
        setBills(firm.bills);
      })
      .catch((reason: unknown) => {
        if (live) setError(errorMessage(reason, "The desk could not load the firm."));
      });
    return () => { live = false; };
  }, []);

  if (error) {
    return (
      <div className="space-y-4">
        <PageHeader title="Dashboard" description="Accounts payable across the firm." />
        <p role="alert" className="rounded-xl border border-border bg-card px-4 py-6 text-sm">{error}</p>
      </div>
    );
  }

  if (!clients || !bills) {
    return (
      <div className="space-y-4">
        <PageHeader title="Dashboard" description="Accounts payable across the firm." />
        <div className="grid gap-3 sm:grid-cols-3">
          <CardSkeleton /><CardSkeleton /><CardSkeleton />
        </div>
        <TableSkeleton rows={5} />
      </div>
    );
  }

  const waiting = bills.filter((bill) => bill.queue === "needs_review" || bill.queue === "new");
  const exceptions = bills.filter((bill) => bill.queue === "exception");
  const openAmount = bills.reduce((sum, bill) => sum + asAmount(bill.total), 0);
  const recent = [...bills].sort((a, b) => (b.invoice_date || "").localeCompare(a.invoice_date || "")).slice(0, 5);
  const first = clients[0];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <PageHeader title="Dashboard" description="Accounts payable across the firm." />
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => onNavigate("/clients")}>Clients</Button>
          {first && <Button onClick={() => onNavigate(`/clients/${first.id}`)}>Open {first.name}</Button>}
        </div>
      </div>
      <section className="grid gap-2.5 sm:grid-cols-3">
        <Metric label="Waiting for review" value={String(waiting.length)} detail="New and needs review" />
        <Metric label="Open amount" value={rupees(openAmount)} detail="Across every queue" />
        <Metric label="Exceptions" value={String(exceptions.length)} detail="Stopped and need a person" />
      </section>
      {clients.length === 0 ? (
        <section className="rounded-xl border border-border bg-card px-4 py-8 text-center text-sm text-muted-foreground">
          No clients are assigned to this firm.
        </section>
      ) : (
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1.4fr)_minmax(260px,0.8fr)]">
          <section className="rounded-xl border border-border bg-card">
            <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">Recent bills</h2>
            {recent.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-muted-foreground">No bills are on file yet.</p>
            ) : (
              <ul>
                {recent.map((bill) => {
                  const href = invoiceHref(bill.clientId, bill.document_id);
                  return (
                    <li key={`${bill.clientId}-${bill.document_id}`} className="border-b border-border last:border-0">
                      <a
                        href={href}
                        onClick={(event) => { event.preventDefault(); onNavigate(href); }}
                        className="flex items-start justify-between gap-3 px-3 py-2 text-[13px] hover:bg-muted/70"
                      >
                        <span>
                          <span className="font-medium">{bill.invoice_no || bill.filename || "Untitled"}</span>
                          <span className="text-muted-foreground"> · {bill.vendor_name || "Unresolved vendor"}</span>
                          <span className="mt-0.5 block text-muted-foreground">{bill.clientName} · {bill.state_label}</span>
                        </span>
                        <span className="shrink-0 text-right tabular-nums">
                          <span className="block">{rupees(bill.total)}</span>
                          <span className="text-xs text-muted-foreground">{formatDay(bill.invoice_date)}</span>
                        </span>
                      </a>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
          <div className="space-y-4">
            <section className="rounded-xl border border-border bg-card p-4">
              <h2 className="text-sm font-semibold">Note</h2>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                {waiting.length === 0
                  ? "Nothing is waiting for a person."
                  : `${waiting.length} ${waiting.length === 1 ? "bill still needs" : "bills still need"} a person before approval.`}
                {exceptions.length > 0 ? ` ${exceptions.length} ${exceptions.length === 1 ? "is" : "are"} held as exceptions.` : ""}
              </p>
            </section>
            <section className="rounded-xl border border-border bg-card p-4">
              <h2 className="text-sm font-semibold">Quick actions</h2>
              <div className="mt-3 flex flex-col gap-2">
                {first && <Action href={`/clients/${first.id}`} label={`Open ${first.name}`} onNavigate={onNavigate} />}
                <Action href="/clients" label="See every client" onNavigate={onNavigate} />
              </div>
            </section>
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="rounded-xl border border-border bg-card px-3 py-3">
      <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums tracking-tight">{value}</p>
      <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p>
    </article>
  );
}

function Action({ href, label, onNavigate }: { href: string; label: string; onNavigate: (url: string) => void }) {
  return (
    <a href={href} onClick={(event) => { event.preventDefault(); onNavigate(href); }} className="flex items-center justify-between rounded-md px-2 py-2 text-sm hover:bg-muted">
      {label}
      <ArrowRight className="size-4 text-muted-foreground" />
    </a>
  );
}
