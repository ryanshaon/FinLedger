import { useEffect, useState } from "react";
import { api, errorMessage } from "@/api";
import { Badge, statusTone } from "@/components/ui/badge";
import { rupees } from "@/format";
import { PageHeader } from "@/pages/Placeholder";
import { EmptyState, TableSkeleton, bodyCell, headCell, TableScroll } from "@/pages/states";
import { invoiceHref } from "@/router";
import type { ClientRow, ReconciliationResponse } from "@/types";

export function TransactionsPage({ onNavigate }: { onNavigate: (url: string) => void }) {
  const [clients, setClients] = useState<ClientRow[] | null>(null);
  const [clientId, setClientId] = useState("");
  const [pages, setPages] = useState<ReconciliationResponse[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    api.clients()
      .then((firm) => {
        if (!live) return;
        setClients(firm.clients);
        const harbour = firm.clients.find((client) => /harbour/i.test(client.name));
        setClientId((current) => current || harbour?.id || firm.clients[0]?.id || "all");
      })
      .catch((reason: unknown) => {
        if (live) setError(errorMessage(reason, "The desk could not load clients."));
      });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    if (!clients || !clientId) return;
    let live = true;
    setPages(null);
    setError("");
    const selected = clientId === "all" ? clients : clients.filter((client) => client.id === clientId);
    Promise.all(selected.map((client) => api.reconciliation(client.id)))
      .then((next) => { if (live) setPages(next); })
      .catch((reason: unknown) => {
        if (live) setError(errorMessage(reason, "The desk could not load bank lines."));
      });
    return () => { live = false; };
  }, [clients, clientId]);

  const lines = (pages || []).flatMap((page) => page.lines.map((line) => ({ ...line, clientName: page.client.name, clientId: page.client.id })));
  const bills = (pages || []).flatMap((page) => page.bills.map((bill) => ({ ...bill, clientId: page.client.id })));
  const selected = clients?.find((client) => client.id === clientId);
  return (
    <div className="space-y-3">
      <PageHeader title="Reconciliation" description="Bank lines against open purchase bills. A payment matches when the invoice number and the amount both agree." />
      {error ? <p role="alert" className="rounded-xl border border-border bg-card px-4 py-6 text-sm">{error}</p> : null}
      {!error && (!clients || (clientId && !pages)) ? <TableSkeleton /> : clients ? (
        <>
          <label className="text-xs text-muted-foreground">
            Client
            <select
              aria-label="Client"
              value={clientId}
              onChange={(event) => setClientId(event.target.value)}
              className="ml-2 h-8 rounded-md border border-input bg-card px-2 text-[13px] text-foreground"
            >
              <option value="all">All clients</option>
              {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
            </select>
          </label>
          {lines.length === 0 ? (
            <div className="rounded-xl border border-border bg-card">
              <EmptyState
                sentence={selected ? `No bank lines are on file for ${selected.name}.` : "No bank lines are on file."}
                action="Back to the dashboard"
                onAction={() => onNavigate("/")}
              />
            </div>
          ) : (
            <TableScroll>
              <thead>
                <tr>
                  <th className={headCell}>Date</th>
                  <th className={headCell}>Client</th>
                  <th className={headCell}>Narration</th>
                  <th className={headCell}>Reference</th>
                  <th className={headCell}>Debit</th>
                  <th className={headCell}>Credit</th>
                  <th className={headCell}>Bill</th>
                </tr>
              </thead>
              <tbody>
                {lines.map((line, index) => (
                  <tr key={`${line.clientId}-${line.date}-${line.narration}-${index}`} className="border-b border-border last:border-0">
                    <td className={bodyCell}>{line.date || "—"}</td>
                    <td className={bodyCell}>{line.clientName}</td>
                    <td className={bodyCell}>{line.narration || "—"}</td>
                    <td className={bodyCell}>{line.reference || "—"}</td>
                    <td className={bodyCell}>{rupees(line.debit)}</td>
                    <td className={bodyCell}>{rupees(line.credit)}</td>
                    <td className={bodyCell}>
                      <Badge tone={statusTone(line.status === "matched" ? "Matched" : "Unmatched")}>
                        {line.status === "matched" ? "Matched" : "Unmatched"}
                      </Badge>
                      {line.status === "matched" && line.document_id ? (
                        <button
                          type="button"
                          className="ml-2 text-left underline"
                          onClick={() => onNavigate(invoiceHref(line.clientId, line.document_id || ""))}
                        >
                          {line.invoice_no}
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </TableScroll>
          )}
          {bills.length > 0 ? (
            <section className="space-y-2" aria-label="Open bills">
              <h2 className="text-sm font-semibold">Open bills</h2>
              <TableScroll>
                <thead>
                  <tr>
                    <th className={headCell}>Invoice</th>
                    <th className={headCell}>Vendor</th>
                    <th className={headCell}>Amount</th>
                    <th className={headCell}>Match</th>
                  </tr>
                </thead>
                <tbody>
                  {bills.map((bill) => (
                    <tr key={bill.document_id} className="border-b border-border last:border-0">
                      <td className={bodyCell}>
                        <button type="button" className="underline" onClick={() => onNavigate(invoiceHref(bill.clientId, bill.document_id))}>
                          {bill.invoice_no}
                        </button>
                      </td>
                      <td className={bodyCell}>{bill.vendor_name || "—"}</td>
                      <td className={bodyCell}>{rupees(bill.amount)}</td>
                      <td className={bodyCell}>
                        <Badge tone={statusTone(bill.status === "matched" ? "Matched" : "Unmatched")}>
                          {bill.status === "matched" ? "Matched" : "Unmatched"}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </TableScroll>
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
