export type QueueKey = "new" | "needs_review" | "approved" | "exception";

export const QUEUES: { key: QueueKey; label: string; context: string }[] = [
  { key: "new", label: "New", context: "Bills that have arrived and are still being prepared." },
  { key: "needs_review", label: "Needs review", context: "Bills a person needs to read before approving them." },
  { key: "approved", label: "Approved", context: "Bills a reviewer has approved." },
  { key: "exception", label: "Exception", context: "Bills that stopped and need a person." },
];

const QUEUE_KEYS = new Set<string>(QUEUES.map((queue) => queue.key));

export type Route =
  | { name: "login" }
  | { name: "mfa" }
  | { name: "home" }
  | { name: "clients" }
  | { name: "client"; clientId: string }
  | { name: "transactions" }
  | { name: "queue"; clientId: string; queue: QueueKey }
  | { name: "invoice"; clientId: string; documentId: string }
  | { name: "staff" }
  | { name: "accept" }
  | { name: "missing" };

const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

function normalize(path: string): string {
  if (path.length > 1 && path.endsWith("/")) return path.slice(0, -1);
  return path || "/";
}

export function parseRoute(path: string, search: string): Route {
  const clean = normalize(path);
  if (clean === "/login") return { name: "login" };
  if (clean === "/mfa") return { name: "mfa" };
  if (clean === "/") return { name: "home" };
  if (clean === "/clients") return { name: "clients" };
  if (clean === "/transactions") return { name: "transactions" };
  if (clean === "/staff") return { name: "staff" };
  if (clean === "/auth/accept") return { name: "accept" };
  let match = clean.match(new RegExp(`^/clients/(${UUID})/invoices/(${UUID})$`));
  if (match) return { name: "invoice", clientId: match[1], documentId: match[2] };
  match = clean.match(new RegExp(`^/clients/(${UUID})$`));
  if (match) {
    const raw = new URLSearchParams(search).get("queue");
    if (raw && QUEUE_KEYS.has(raw)) return { name: "queue", clientId: match[1], queue: raw as QueueKey };
    return { name: "client", clientId: match[1] };
  }
  return { name: "missing" };
}

export function clientIdOf(route: Route): string | null {
  if (route.name === "queue" || route.name === "invoice" || route.name === "client") return route.clientId;
  return null;
}

export function rememberQueue(clientId: string, queue: QueueKey) {
  try {
    sessionStorage.setItem("finledger-queue", JSON.stringify({ clientId, queue }));
  } catch {
    /* private mode */
  }
}

export function queueHref(clientId: string, queue: QueueKey): string {
  return `/clients/${clientId}?queue=${queue}`;
}

export function invoiceHref(clientId: string, documentId: string): string {
  return `/clients/${clientId}/invoices/${documentId}`;
}
