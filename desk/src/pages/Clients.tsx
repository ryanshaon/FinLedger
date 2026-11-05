import { useState } from "react";
import { Badge, statusTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { countOf, filterRows, matchesClient } from "@/deskSearch";
import { formatDay, rupees } from "@/format";
import { useFirmSummary, type LiveBill } from "@/live";
import { PageHeader } from "@/pages/Placeholder";
import { bodyCell, EmptyState, headCell, LoadError, SearchField, TableScroll, TableSkeleton } from "@/pages/states";
import { invoiceHref } from "@/router";

export function ClientsPage({ onNavigate }: { onNavigate: (url: string) => void }) {
  const { firm, error, loading, reload } = useFirmSummary("The desk could not load clients.");
  const [query, setQuery] = useState("");
  const shown = firm ? filterRows(firm.clients, query, matchesClient) : [];
  const byId = new Map((firm?.summaries ?? []).map((summary) => [summary.client.id, summary] as const));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <PageHeader title="Clients" description="Companies on the firm’s books. Open one to see its bills." />
        <Button variant="outline" disabled={loading} onClick={reload}>{loading && firm ? "Refreshing…" : "Refresh clients"}</Button>
      </div>
      {error ? <LoadError message={error} busy={loading} onRetry={reload} /> : null}
      {firm && firm.failed.length > 0 ? (
        <p role="status" className="rounded-md border border-border bg-muted px-3 py-2 text-sm">
          Counts could not be loaded for {firm.failed.join(", ")}. Try again to refresh them.
        </p>
      ) : null}
      {!firm ? (error ? null : <TableSkeleton />) : firm.clients.length === 0 ? (
        <div className="rounded-xl border border-border bg-card">
          <EmptyState sentence="No clients are assigned to this firm." action="Back to the dashboard" onAction={() => onNavigate("/")} />
        </div>
      ) : (
        <div className="space-y-3">
          <SearchField label="Filter companies by name or GSTIN" value={query} onChange={setQuery} />
          <p role="status" className="text-xs text-muted-foreground">
            {query.trim()
              ? `Showing ${shown.length} of ${firm.clients.length} companies`
              : `${firm.clients.length} ${firm.clients.length === 1 ? "company" : "companies"}`}
          </p>
          {shown.length === 0 ? (
            <div className="rounded-xl border border-border bg-card">
              <EmptyState sentence={`No company matches “${query.trim()}”.`} action="Clear search" onAction={() => setQuery("")} />
            </div>
          ) : (
            <TableScroll className="min-w-0">
              <thead>
                <tr>
                  <th className={headCell}>Client</th>
                  <th className={headCell}>GSTIN</th>
                  <th className={`${headCell} text-right`}>Needs review</th>
                  <th className={`${headCell} text-right`}>New</th>
                  <th className={`${headCell} text-right`}>Exceptions</th>
                  <th className={`${headCell} text-right`}>Open bills</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((client) => {
                  const summary = byId.get(client.id);
                  const count = (...keys: string[]) => (summary ? String(countOf(summary.counts, ...keys)) : "—");
                  const href = `/clients/${client.id}`;
                  return (
                    <tr key={client.id} className="border-b border-border last:border-0 hover:bg-muted/70">
                      <td className={bodyCell}>
                        <a href={href} onClick={(event) => { event.preventDefault(); onNavigate(href); }} className="font-medium">
                          {client.name}
                        </a>
                      </td>
                      <td className={`${bodyCell} tabular-nums text-muted-foreground`}>{client.gstin || "—"}</td>
                      <td className={`${bodyCell} text-right tabular-nums`}>{count("needs_review")}</td>
                      <td className={`${bodyCell} text-right tabular-nums`}>{count("new")}</td>
                      <td className={`${bodyCell} text-right tabular-nums`}>{count("exception")}</td>
                      <td className={`${bodyCell} text-right tabular-nums`}>{count("new", "needs_review", "approved", "exception")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </TableScroll>
          )}
        </div>
      )}
    </div>
  );
}

export function BillTable({ rows, onNavigate }: { rows: LiveBill[]; onNavigate: (url: string) => void }) {
  return (
    <TableScroll>
      <thead>
        <tr>
          <th className={headCell}>Invoice</th>
          <th className={headCell}>Vendor</th>
          <th className={headCell}>Date</th>
          <th className={`${headCell} text-right`}>Amount</th>
          <th className={headCell}>Status</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((bill) => {
          const href = invoiceHref(bill.clientId, bill.document_id);
          return (
            <tr key={bill.document_id} className="border-b border-border last:border-0 hover:bg-muted/70">
              <td className={bodyCell}>
                <a href={href} onClick={(event) => { event.preventDefault(); onNavigate(href); }} className="font-medium">
                  {bill.invoice_no || bill.filename || "Untitled"}
                </a>
              </td>
              <td className={bodyCell}>{bill.vendor_name || "Unresolved vendor"}</td>
              <td className={`${bodyCell} tabular-nums text-muted-foreground`}>{formatDay(bill.invoice_date)}</td>
              <td className={`${bodyCell} text-right tabular-nums`}>{rupees(bill.total)}</td>
              <td className={bodyCell}><Badge tone={statusTone(bill.state_label)}>{bill.state_label}</Badge>{bill.status_reason ? <span className="mt-1 block text-xs text-muted-foreground">{bill.status_reason.startsWith("outcome_uncertain") ? "Uncertain: do not re-enter this bill." : bill.status_reason}</span> : null}</td>
            </tr>
          );
        })}
      </tbody>
    </TableScroll>
  );
}
