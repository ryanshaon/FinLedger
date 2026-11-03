import { useEffect, useState } from "react";
import { errorMessage } from "@/api";
import { Badge, statusTone } from "@/components/ui/badge";
import { formatDay, rupees } from "@/format";
import { loadClientBills, loadFirm, type LiveBill } from "@/live";
import { PageHeader } from "@/pages/Placeholder";
import { bodyCell, EmptyState, headCell, TableScroll, TableSkeleton } from "@/pages/states";
import { invoiceHref } from "@/router";
import type { ClientRow } from "@/types";

export function ClientsPage({ onNavigate }: { onNavigate: (url: string) => void }) {
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
        if (live) setError(errorMessage(reason, "The desk could not load clients."));
      });
    return () => { live = false; };
  }, []);

  return (
    <div className="space-y-4">
      <PageHeader title="Clients" description="Companies on the firm’s books. Open one to see its bills." />
      {error ? <p role="alert" className="rounded-xl border border-border bg-card px-4 py-6 text-sm">{error}</p> : null}
      {!clients || !bills ? <TableSkeleton /> : clients.length === 0 ? (
        <div className="rounded-xl border border-border bg-card">
          <EmptyState sentence="No clients are assigned to this firm." action="Back to the dashboard" onAction={() => onNavigate("/")} />
        </div>
      ) : (
        <TableScroll className="min-w-0">
          <thead>
            <tr>
              <th className={headCell}>Client</th>
              <th className={headCell}>GSTIN</th>
              <th className={`${headCell} text-right`}>Open bills</th>
              <th className={`${headCell} text-right`}>Needs review</th>
            </tr>
          </thead>
          <tbody>
            {clients.map((client) => {
              const rows = bills.filter((bill) => bill.clientId === client.id);
              const review = rows.filter((bill) => bill.queue === "needs_review" || bill.queue === "new");
              const href = `/clients/${client.id}`;
              return (
                <tr key={client.id} className="border-b border-border last:border-0 hover:bg-muted/70">
                  <td className={bodyCell}>
                    <a href={href} onClick={(event) => { event.preventDefault(); onNavigate(href); }} className="font-medium">
                      {client.name}
                    </a>
                  </td>
                  <td className={`${bodyCell} tabular-nums text-muted-foreground`}>{client.gstin || "—"}</td>
                  <td className={`${bodyCell} text-right tabular-nums`}>{rows.length}</td>
                  <td className={`${bodyCell} text-right tabular-nums`}>{review.length}</td>
                </tr>
              );
            })}
          </tbody>
        </TableScroll>
      )}
    </div>
  );
}

export function ClientDetail({ clientId, onNavigate }: { clientId: string; onNavigate: (url: string) => void }) {
  const [client, setClient] = useState<ClientRow | null>(null);
  const [rows, setRows] = useState<LiveBill[] | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    loadFirm()
      .then(async (firm) => {
        const found = firm.clients.find((item) => item.id === clientId);
        if (!found) {
          if (live) setMissing(true);
          return;
        }
        const bills = await loadClientBills(found);
        if (!live) return;
        setClient(found);
        setRows([...bills].sort((a, b) => (b.invoice_date || "").localeCompare(a.invoice_date || "")));
      })
      .catch((reason: unknown) => {
        if (live) setError(errorMessage(reason, "The desk could not load this client."));
      });
    return () => { live = false; };
  }, [clientId]);

  if (missing) {
    return (
      <div className="space-y-4">
        <PageHeader title="Client" description="This company is not on the firm’s list." />
        <div className="rounded-xl border border-border bg-card">
          <EmptyState sentence="This client is not on the desk." action="Back to clients" onAction={() => onNavigate("/clients")} />
        </div>
      </div>
    );
  }

  const open = rows || [];
  return (
    <div className="space-y-4">
      <PageHeader
        title={client?.name || "Client"}
        description={client
          ? `${client.gstin || "No GSTIN on file"}. ${open.length} open ${open.length === 1 ? "bill" : "bills"}.`
          : "Bills and status for this client."}
      />
      {error ? <p role="alert" className="rounded-xl border border-border bg-card px-4 py-6 text-sm">{error}</p> : null}
      {!rows ? <TableSkeleton /> : rows.length === 0 ? (
        <div className="rounded-xl border border-border bg-card">
          <EmptyState sentence="No bills are on file for this client." action="Back to clients" onAction={() => onNavigate("/clients")} />
        </div>
      ) : (
        <BillTable rows={rows} onNavigate={onNavigate} />
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
              <td className={bodyCell}><Badge tone={statusTone(bill.state_label)}>{bill.state_label}</Badge></td>
            </tr>
          );
        })}
      </tbody>
    </TableScroll>
  );
}
