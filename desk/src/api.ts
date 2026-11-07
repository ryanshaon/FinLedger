import type { ClientRow, InvoiceResponse, PostingPanel, QueueResponse, ReconciliationResponse, Session, UploadResponse } from "./types";

let csrfToken = "";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

function remember(data: unknown) {
  if (!data || typeof data !== "object" || !("csrf_token" in data)) return;
  const token = (data as { csrf_token?: unknown }).csrf_token;
  if (typeof token === "string" && token) csrfToken = token;
}

function detailMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return fallback;
}

export function isMfaRequired(error: unknown): boolean {
  if (!(error instanceof ApiError) || !error.body || typeof error.body !== "object") return false;
  const detail = (error.body as { detail?: unknown }).detail;
  return Boolean(detail && typeof detail === "object" && (detail as { mfa_required?: unknown }).mfa_required);
}

export function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.message) return error.message;
  return fallback;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "The desk could not reach the control server.", null);
  }
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text) as unknown;
    } catch {
      data = null;
    }
  }
  remember(data);
  if (!response.ok) {
    throw new ApiError(response.status, detailMessage(data, "The desk could not complete that request."), data);
  }
  return data as T;
}

function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify({ ...body, csrf_token: csrfToken }) });
}

function isUploadBody(body: unknown): body is UploadResponse {
  return Boolean(body) && typeof body === "object" && Array.isArray((body as { rejected?: unknown }).rejected);
}

async function upload(clientId: string, file: File): Promise<UploadResponse> {
  try {
    return await request<UploadResponse>(`/api/clients/${encodeURIComponent(clientId)}/documents`, {
      method: "POST",
      headers: {
        "Content-Type": file.type || "application/octet-stream",
        "X-Filename": encodeURIComponent(file.name),
        "X-CSRF-Token": csrfToken,
      },
      body: file,
    });
  } catch (error) {
    // A 422 can still carry the per-file reasons; a generic 422 detail is rethrown.
    if (error instanceof ApiError && error.status === 422 && isUploadBody(error.body)) {
      return { received: error.body.received ?? [], rejected: error.body.rejected };
    }
    throw error;
  }
}

export const api = {
  upload,
  csrf: () => request<{ csrf_token: string }>("/api/csrf"),
  session: () => request<Session>("/api/session"),
  login: (email: string, password: string) => post<{ ok: boolean }>("/api/login", { email, password }),
  logout: () => post<{ ok: boolean }>("/api/logout", {}),
  clients: () => request<{ clients: ClientRow[] }>("/api/clients"),
  queue: (clientId: string, queue: string) =>
    request<QueueResponse>(`/api/clients/${encodeURIComponent(clientId)}/queue?queue=${encodeURIComponent(queue)}`),
  invoice: (clientId: string, documentId: string) =>
    request<InvoiceResponse>(`/api/clients/${encodeURIComponent(clientId)}/invoices/${encodeURIComponent(documentId)}`),
  reconciliation: (clientId: string) =>
    request<ReconciliationResponse>(`/api/clients/${encodeURIComponent(clientId)}/reconciliation`),
  saveChange: (clientId: string, documentId: string, revision: number, value: string) =>
    post(`/api/clients/${encodeURIComponent(clientId)}/invoices/${encodeURIComponent(documentId)}/edit`, {
      revision, field: "party_ledger", value,
    }),
  approve: (clientId: string, documentId: string, revision: number, postToBooks: boolean) =>
    post<{ status: string; posting_blocked?: boolean; posting_reason?: string | null }>(
      `/api/clients/${encodeURIComponent(clientId)}/invoices/${encodeURIComponent(documentId)}/approve`,
      { revision, post: postToBooks },
    ),
  sendBack: (clientId: string, documentId: string, revision: number) =>
    post(`/api/clients/${encodeURIComponent(clientId)}/invoices/${encodeURIComponent(documentId)}/reject`, {
      revision, resubmit: true,
    }),
  posting: (clientId: string) => request<PostingPanel>(`/api/clients/${encodeURIComponent(clientId)}/posting`),
  enablePosting: (clientId: string, companyGuid: string, companyName: string) =>
    post<{ enabled: boolean }>(`/api/clients/${encodeURIComponent(clientId)}/posting/enable`, {
      company_guid: companyGuid, confirm_company_name: companyName,
    }),
  disablePosting: (clientId: string) =>
    post<{ enabled: boolean }>(`/api/clients/${encodeURIComponent(clientId)}/posting/disable`, {}),
  reconcile: (clientId: string, documentId: string, decision: "posted" | "absent", erpId: string, note: string) =>
    post(`/api/clients/${encodeURIComponent(clientId)}/invoices/${encodeURIComponent(documentId)}/reconcile`, {
      decision, erp_id: erpId, note,
    }),
  returnToReview: (clientId: string, documentId: string, revision: number, note: string) =>
    post(`/api/clients/${encodeURIComponent(clientId)}/invoices/${encodeURIComponent(documentId)}/return-to-review`, {
      revision, note,
    }),
  mfa: () => request<{ factor_id: string; secret: string; csrf_token: string }>("/api/mfa"),
  enroll: () => post<{ factor_id: string; secret: string }>("/api/mfa/enroll", {}),
  verify: (factorId: string, code: string) => post<{ ok: boolean }>("/api/mfa/verify", { factor_id: factorId, code }),
  staff: () => request<{ clients: ClientRow[]; csrf_token: string }>("/api/staff"),
  invite: (body: { email: string; name: string; firm_admin: boolean; memberships: { client_id: string; role: string }[] }) =>
    post<{ notice: string }>("/api/staff/invite", body),
  acceptInvite: (tokenHash: string, password: string, passwordConfirm: string) =>
    post<{ ok: boolean }>("/api/auth/accept", { token_hash: tokenHash, password, password_confirm: passwordConfirm }),
};

export function needsMfa(session: { aal: string; mfa_required?: boolean }): boolean {
  return session.mfa_required !== false && session.aal !== "aal2";
}
