import { Button } from "@/components/ui/button";
import { countOf } from "@/deskSearch";
import { asAmount, formatDay, rupees } from "@/format";
import { useFirmSummary } from "@/live";
import { PageHeader } from "@/pages/Placeholder";
import { CardSkeleton, LoadError, TableSkeleton } from "@/pages/states";
import { invoiceHref, queueHref, type QueueKey } from "@/router";

const SHORTCUTS: { queue: QueueKey; label: string }[] = [
  { queue: "needs_review", label: "Needs review" },
  { queue: "new", label: "New" },
  { queue: "exception", label: "Exceptions" },
];

export function Dashboard({ onNavigate }: { onNavigate: (url: string) => void }) {
  const { firm, error, loading, loadedAt, reload } = useFirmSummary("The desk could not load the firm.");
  const header = (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <PageHeader title="Dashboard" description="Accounts payable across the firm." />
      <div className="flex items-center gap-3">
        {loadedAt ? (
          <span className="text-xs text-muted-foreground">
            Updated {loadedAt.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}
          </span>
        ) : null}
        <Button variant="outline" disabled={loading} onClick={reload}>{loading && firm ? "Refreshing…" : "Refresh"}</Button>
      </div>
    </div>
  );

  if (!firm) {
    return (
      <div className="space-y-4">
        {header}
        {error ? (
          <LoadError message={error} busy={loading} onRetry={reload} />
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <CardSkeleton /><CardSkeleton /><CardSkeleton /><CardSkeleton />
            </div>
            <TableSkeleton rows={5} />
          </>
        )}
      </div>
    );
  }

  const total = (key: string) => firm.summaries.reduce((sum, item) => sum + countOf(item.counts, key), 0);
  const reviewBills = firm.summaries.flatMap((item) => item.reviewBills);
  const reviewAmount = reviewBills.reduce((sum, bill) => sum + asAmount(bill.total), 0);
  const waiting = [...reviewBills].sort((a, b) => (b.invoice_date || "").localeCompare(a.invoice_date || "")).slice(0, 6);
  const byId = new Map(firm.summaries.map((item) => [item.client.id, item] as const));
  const open = (event: { preventDefault: () => void }, url: string) => {
    event.preventDefault();
    onNavigate(url);
  };

  return (
    <div className="space-y-4">
      {header}
      {error ? <LoadError message={error} busy={loading} onRetry={reload} /> : null}
      {firm.failed.length > 0 ? (
        <p role="status" className="rounded-md border border-border bg-muted px-3 py-2 text-sm">
          Totals leave out {firm.failed.join(", ")}, which could not be loaded.
        </p>
      ) : null}
      <section aria-label="Bill counts" className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="Needs review" value={String(total("needs_review"))} detail={`${rupees(reviewAmount)} in total`} />
        <Metric label="New" value={String(total("new"))} detail="Still being read" />
        <Metric label="Approved" value={String(total("approved"))} detail="Approved by a reviewer" />
        <Metric label="Exceptions" value={String(total("exception"))} detail="Stopped and need a person" />
      </section>
      {firm.clients.length === 0 ? (
        <section className="rounded-xl border border-border bg-card px-4 py-8 text-center text-sm text-muted-foreground">
          No clients are assigned to this firm.
        </section>
      ) : (
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1.4fr)_minmax(260px,0.8fr)]">
          <section className="rounded-xl border border-border bg-card">
            <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">Waiting for review</h2>
            {waiting.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-muted-foreground">Nothing is waiting for review.</p>
            ) : (
              <ul>
                {waiting.map((bill) => {
                  const href = invoiceHref(bill.clientId, bill.document_id);
                  return (
                    <li key={`${bill.clientId}-${bill.document_id}`} className="border-b border-border last:border-0">
                      <a
                        href={href}
                        onClick={(event) => open(event, href)}
                        className="flex items-start justify-between gap-3 px-3 py-2 text-[13px] hover:bg-muted/70"
                      >
                        <span>
                          <span className="font-medium">{bill.invoice_no || bill.filename || "Untitled"}</span>
                          <span className="text-muted-foreground"> · {bill.vendor_name || "Unresolved vendor"}</span>
                          <span className="mt-0.5 block text-muted-foreground">{bill.clientName}</span>
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
          <section className="rounded-xl border border-border bg-card">
            <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">Companies</h2>
            <ul>
              {firm.clients.map((client) => {
                const summary = byId.get(client.id);
                const links = SHORTCUTS.map((item) => ({ ...item, count: countOf(summary?.counts, item.queue) })).filter((link) => link.count > 0);
                const home = queueHref(client.id, "needs_review");
                return (
                  <li key={client.id} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-border px-3 py-2 text-[13px] last:border-0">
                    <a href={home} onClick={(event) => open(event, home)} className="font-medium">
                      {client.name}
                    </a>
                    {!summary ? (
                      <span className="text-muted-foreground">Could not load</span>
                    ) : links.length === 0 ? (
                      <span className="text-muted-foreground">Nothing waiting</span>
                    ) : (
                      <span className="flex flex-wrap gap-x-3">
                        {links.map((link) => {
                          const href = queueHref(client.id, link.queue);
                          return (
                            <a key={link.queue} href={href} onClick={(event) => open(event, href)} className="text-muted-foreground hover:text-foreground">
                              {link.label}{" "}<span className="font-medium tabular-nums text-foreground">{link.count}</span>
                            </a>
                          );
                        })}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
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
