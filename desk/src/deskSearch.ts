export function searchTerms(query: string): string[] {
  return query.toLowerCase().split(/\s+/).filter(Boolean);
}

function includesAll(text: string, query: string): boolean {
  const haystack = text.toLowerCase();
  return searchTerms(query).every((term) => haystack.includes(term));
}

export function matchesClient(client: { name: string; gstin?: string | null }, query: string): boolean {
  return includesAll(`${client.name} ${client.gstin ?? ""}`, query);
}

export function matchesBill(
  bill: { invoice_no: string | null; filename: string | null; vendor_name: string | null },
  query: string,
): boolean {
  return includesAll(`${bill.invoice_no ?? ""} ${bill.filename ?? ""} ${bill.vendor_name ?? ""}`, query);
}

export function filterRows<T>(rows: readonly T[], query: string, matches: (row: T, query: string) => boolean): T[] {
  if (searchTerms(query).length === 0) return [...rows];
  return rows.filter((row) => matches(row, query));
}

export function countOf(counts: Record<string, number> | undefined, ...keys: string[]): number {
  return keys.reduce((sum, key) => sum + (Number(counts?.[key]) || 0), 0);
}
