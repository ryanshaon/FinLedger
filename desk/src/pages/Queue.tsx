import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { api, errorMessage } from "@/api";
import { Button } from "@/components/ui/button";
import { filterRows, matchesBill } from "@/deskSearch";
import { PageHeader } from "@/pages/Placeholder";
import { BillTable } from "@/pages/Clients";
import { EmptyState, LoadError, SearchField, TableSkeleton } from "@/pages/states";
import { QUEUES, queueHref, type QueueKey } from "@/router";
import type { LiveBill } from "@/live";
import type { PostingPanel } from "@/types";

const DEFAULT_MAX_UPLOAD = 25 * 1024 * 1024;
const UPLOAD_ROLES = ["ap_clerk", "firm_admin"];
const ACCEPT = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";
const EXTENSIONS = /\.(pdf|jpe?g|png)$/i;

function PostingSwitch({ clientId }: { clientId: string }) {
  const [panel, setPanel] = useState<PostingPanel | null>(null);
  const [guid, setGuid] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    setPanel(null);
    api.posting(clientId)
      .then((next) => { if (live) setPanel(next); })
      .catch(() => { if (live) setPanel(null); });
    return () => { live = false; };
  }, [clientId]);

  async function change(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
      setPanel(await api.posting(clientId));
    } catch (reason) {
      setError(errorMessage(reason, "The desk could not change posting."));
    } finally {
      setBusy(false);
    }
  }

  if (!panel) return null;
  return (
    <section aria-label="Tally posting" className="space-y-2 rounded-xl border border-border bg-card p-4">
      <p role="status" className="text-sm font-medium">
        {panel.enabled ? "Posting is ON for this test company." : "Posting is off."}
      </p>
      <p className="text-xs text-muted-foreground">
        Company: {panel.company_name ?? "not paired"}
        {panel.company_guid ? ` · ${panel.company_guid}` : ""}
        {" · "}Agent last seen: {panel.agent_last_seen_at ?? "never"}
      </p>
      {error ? <p role="alert" className="text-sm">{error}</p> : null}
      {panel.can_change && panel.enabled ? (
        <Button variant="outline" disabled={busy} onClick={() => void change(() => api.disablePosting(clientId))}>Disable posting</Button>
      ) : null}
      {panel.can_change && !panel.enabled ? (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => { event.preventDefault(); void change(() => api.enablePosting(clientId, guid.trim(), name)); }}
        >
          <label className="text-sm font-medium">
            Company GUID
            <input className="mt-1 block w-80 rounded-md border border-border bg-background px-2 py-1 text-sm" value={guid} onChange={(event) => setGuid(event.target.value)} />
          </label>
          <label className="text-sm font-medium">
            Type the company name
            <input className="mt-1 block w-72 rounded-md border border-border bg-background px-2 py-1 text-sm" value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <Button type="submit" variant="outline" disabled={busy || !guid.trim() || !name}>Enable posting</Button>
        </form>
      ) : null}
    </section>
  );
}

export function QueuePage({
  clientId,
  queue,
  onNavigate,
}: {
  clientId: string;
  queue: QueueKey;
  onNavigate: (url: string) => void;
}) {
  const current = QUEUES.find((item) => item.key === queue) ?? QUEUES[1];
  const [name, setName] = useState("");
  const [rows, setRows] = useState<LiveBill[] | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [canUpload, setCanUpload] = useState(false);
  const [maxBytes, setMaxBytes] = useState(DEFAULT_MAX_UPLOAD);
  const [refresh, setRefresh] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [pending, setPending] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadNotice, setUploadNotice] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const activeClient = useRef<string | null>(null);
  const inputId = useId();
  const hintId = useId();
  const shown = rows ? filterRows(rows, query, matchesBill) : [];
  const emptyAction = queue === "needs_review"
    ? { label: "Back to clients", url: "/clients" }
    : { label: "Go to needs review", url: queueHref(clientId, "needs_review") };

  useEffect(() => {
    activeClient.current = clientId;
    return () => { activeClient.current = null; };
  }, [clientId]);

  useEffect(() => { setQuery(""); }, [queue]);

  useEffect(() => {
    let live = true;
    setRows(null);
    setError("");
    api.queue(clientId, queue)
      .then((page) => {
        if (!live) return;
        setName(page.client.name);
        setCounts(page.counts ?? {});
        setCanUpload((page.roles ?? []).some((role) => UPLOAD_ROLES.includes(role)));
        setMaxBytes(page.max_upload_bytes ?? DEFAULT_MAX_UPLOAD);
        setRows(page.items.map((item) => ({
          ...item,
          clientId,
          clientName: page.client.name,
          queue,
        })));
      })
      .catch((reason: unknown) => {
        if (live) setError(errorMessage(reason, "The desk could not load this queue."));
      });
    return () => { live = false; };
  }, [clientId, queue, refresh]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || pending) return;
    const limit = Math.floor(maxBytes / (1024 * 1024));
    if (!EXTENSIONS.test(file.name)) {
      setUploadError("Choose a PDF, JPG or PNG file.");
      return;
    }
    if (file.size === 0 || file.size > maxBytes) {
      setUploadError(file.size === 0 ? "That file is empty." : `That file is larger than ${limit} MB.`);
      return;
    }
    const target = clientId;
    setPending(true);
    setUploadError("");
    setUploadNotice("");
    try {
      const result = await api.upload(target, file);
      if (activeClient.current !== target) return;
      const reasons = result.rejected.map((item) => `${item.filename}: ${item.reason}`).join("; ");
      if (result.received.length === 0) {
        setUploadError(reasons || "The bill was not accepted.");
        return;
      }
      const count = result.received.length;
      setUploadNotice(`${count} ${count === 1 ? "bill" : "bills"} received. It appears under New while it is read.`);
      setUploadError(reasons);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      if (queue === "new") setRefresh((value) => value + 1);
      else onNavigate(queueHref(target, "new"));
    } catch (reason) {
      if (activeClient.current === target) setUploadError(errorMessage(reason, "The desk could not upload that bill."));
    } finally {
      if (activeClient.current === target) setPending(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <PageHeader title={name || current.label} description={current.context} />
        <Button variant="outline" disabled={pending} onClick={() => setRefresh(value => value + 1)}>Refresh queue</Button>
      </div>
      <PostingSwitch clientId={clientId} />
      {canUpload ? (
        <form
          aria-label="Invoice upload"
          onSubmit={(event) => { void submit(event); }}
          className="flex flex-wrap items-end gap-3 rounded-xl border border-border bg-card p-4"
        >
          <div className="min-w-0 flex-1">
            <label htmlFor={inputId} className="block text-sm font-medium">Upload a bill</label>
            <input
              id={inputId}
              ref={inputRef}
              type="file"
              accept={ACCEPT}
              disabled={pending}
              aria-describedby={hintId}
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                setUploadError("");
                setUploadNotice("");
              }}
              className="mt-1.5 block w-full text-sm file:mr-3 file:rounded-md file:border file:border-border file:bg-background file:px-3 file:py-1.5 file:text-sm"
            />
            <p id={hintId} className="mt-1 text-xs text-muted-foreground">
              PDF, JPG or PNG, up to {Math.floor(maxBytes / (1024 * 1024))} MB.
            </p>
          </div>
          <Button type="submit" disabled={!file || pending}>{pending ? "Uploading…" : "Upload"}</Button>
        </form>
      ) : null}
      {pending ? <p role="status" className="text-sm text-muted-foreground">Uploading the bill. Keep this page open.</p> : null}
      {uploadNotice ? <p role="status" className="rounded-md border border-border bg-card px-3 py-2 text-sm">{uploadNotice}</p> : null}
      {uploadError ? <p role="alert" className="rounded-md border border-border bg-muted px-3 py-2 text-sm">{uploadError}</p> : null}
      <div className="flex flex-wrap gap-2">
        {QUEUES.map((item) => {
          const total = counts[item.key];
          return (
            <a
              key={item.key}
              href={queueHref(clientId, item.key)}
              aria-current={item.key === queue ? "page" : undefined}
              aria-label={typeof total === "number" ? `${item.label}: ${total}` : undefined}
              onClick={(event) => { event.preventDefault(); onNavigate(queueHref(clientId, item.key)); }}
              className={`rounded-full border px-3 py-1 text-xs ${item.key === queue ? "border-foreground bg-foreground text-background" : "border-border text-muted-foreground hover:bg-muted"}`}
            >
              {item.label}
              {typeof total === "number" ? <span className="ml-1.5 tabular-nums opacity-80">{total}</span> : null}
            </a>
          );
        })}
      </div>
      {error ? (
        <LoadError message={error} onRetry={() => setRefresh((value) => value + 1)} />
      ) : !rows ? (
        <TableSkeleton />
      ) : rows.length === 0 ? (
        <div className="rounded-xl border border-border bg-card">
          <EmptyState sentence={`No bills are in ${current.label.toLowerCase()}.`} action={emptyAction.label} onAction={() => onNavigate(emptyAction.url)} />
        </div>
      ) : (
        <div className="space-y-3">
          <SearchField label="Search bills by vendor, invoice number or file name" value={query} onChange={setQuery} />
          <p role="status" className="text-xs text-muted-foreground">
            {query.trim()
              ? `Showing ${shown.length} of ${rows.length} bills`
              : `${rows.length} ${rows.length === 1 ? "bill" : "bills"}`}
          </p>
          {shown.length === 0 ? (
            <div className="rounded-xl border border-border bg-card">
              <EmptyState sentence={`No bill in ${current.label.toLowerCase()} matches “${query.trim()}”.`} action="Clear search" onAction={() => setQuery("")} />
            </div>
          ) : (
            <BillTable rows={shown} onNavigate={onNavigate} />
          )}
        </div>
      )}
    </div>
  );
}
