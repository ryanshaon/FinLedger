import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "@/api";
import type { QueueKey } from "@/router";
import type { ClientRow, QueueItem } from "@/types";

export interface LiveBill extends QueueItem {
  clientId: string;
  clientName: string;
  queue: QueueKey;
}

export interface ClientSummary {
  client: ClientRow;
  counts: Record<string, number>;
  reviewBills: LiveBill[];
}

export interface FirmSummary {
  clients: ClientRow[];
  summaries: ClientSummary[];
  failed: string[];
}

// One request per company: every queue response already carries the counts for all queues.
async function loadSummary(client: ClientRow): Promise<ClientSummary> {
  const page = await api.queue(client.id, "needs_review");
  const clientName = page.client.name || client.name;
  return {
    client,
    counts: page.counts ?? {},
    reviewBills: page.items.map((item) => ({ ...item, clientId: client.id, clientName, queue: "needs_review" as const })),
  };
}

export async function loadFirmSummary(): Promise<FirmSummary> {
  const { clients } = await api.clients();
  const results = await Promise.allSettled(clients.map(loadSummary));
  const summaries: ClientSummary[] = [];
  const failed: string[] = [];
  let firstReason: unknown = null;
  results.forEach((result, index) => {
    if (result.status === "fulfilled") {
      summaries.push(result.value);
    } else {
      failed.push(clients[index].name);
      firstReason ??= result.reason;
    }
  });
  if (clients.length > 0 && summaries.length === 0) throw firstReason;
  return { clients, summaries, failed };
}

export function useFirmSummary(failure: string) {
  const [firm, setFirm] = useState<FirmSummary | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    loadFirmSummary()
      .then((next) => {
        if (!live) return;
        setFirm(next);
        setLoadedAt(new Date());
      })
      .catch((reason: unknown) => {
        if (live) setError(errorMessage(reason, failure));
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => { live = false; };
  }, [tick, failure]);

  const reload = useCallback(() => setTick((value) => value + 1), []);
  return { firm, error, loading, loadedAt, reload };
}
