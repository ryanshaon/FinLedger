export interface Session {
  user_id: string;
  firm_id: string;
  firm_admin: boolean;
  aal: string;
  mfa_required?: boolean;
  csrf_token: string;
}

export interface ClientRow {
  id: string;
  name: string;
  gstin?: string;
}

export interface QueueItem {
  document_id: string;
  invoice_no: string | null;
  filename: string | null;
  vendor_name: string | null;
  invoice_date?: string | null;
  total: string | number | null;
  band: string | null;
  score: number | string | null;
  state_label: string;
}

export interface QueueResponse {
  client: ClientRow;
  queue: string;
  counts: Record<string, number>;
  items: QueueItem[];
  roles?: string[];
  csrf_token?: string;
  max_upload_bytes?: number;
}

export interface Mark {
  field?: string;
  check?: string;
  ok?: boolean;
  note?: string;
}

export interface InvoiceDetail {
  document_id: string;
  filename: string | null;
  status: string;
  state_label: string;
  revision: number;
  source_url?: string;
  ai_paragraph?: string;
  can_edit?: boolean;
  can_approve?: boolean;
  approval_note?: string;
  invoice: {
    invoice_no?: string | null;
    invoice_date?: string | null;
    buyer_gstin?: string | null;
    po_number?: string | null;
    total?: string | number | null;
    taxable?: string | number | null;
    cgst?: string | number | null;
    sgst?: string | number | null;
    igst?: string | number | null;
    cess?: string | number | null;
    round_off?: string | number | null;
    vendor?: { name?: string | null; gstin?: string | null };
    confidences?: Record<string, number>;
  };
  risk?: { score?: number | string | null; band?: string | null; marks?: Mark[] };
  draft?: {
    party_ledger?: string | null;
    lines?: { ledger?: string | null; amount?: string | number | null; is_debit?: boolean | null }[];
    gst?: { treatment?: string | null };
    tds?: { applicable?: boolean };
    bill_wise?: { ref?: string | null };
    reasons?: string[];
  };
}

import type { PostingBlock, ThreeWayMatch } from "./threeWay";

export type { PostingBlock, ThreeWayMatch };

export interface InvoiceResponse {
  client: ClientRow;
  roles: string[];
  csrf_token: string;
  invoice: InvoiceDetail;
  match?: ThreeWayMatch;
  posting?: PostingBlock;
}

export interface BankLine {
  date: string;
  narration: string;
  reference: string;
  debit: string | null;
  credit: string | null;
  status: "matched" | "unmatched";
  invoice_no: string | null;
  document_id: string | null;
}

export interface OpenBillMatch {
  document_id: string;
  invoice_no: string;
  amount: string | null;
  vendor_name: string;
  workflow_status: string;
  status: "matched" | "unmatched";
}

export interface ReconciliationResponse {
  client: ClientRow;
  lines: BankLine[];
  bills: OpenBillMatch[];
}

export interface UploadResponse {
  received: { document_id: string; filename: string; content_hash: string }[];
  rejected: { filename: string; reason: string }[];
}

export interface PageContext {
  alive: () => boolean;
  go: (url: string) => void;
  rerender: () => void;
}
