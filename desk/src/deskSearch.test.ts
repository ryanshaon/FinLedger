import assert from "node:assert/strict";
import test from "node:test";
import { countOf, filterRows, matchesBill, matchesClient, searchTerms } from "./deskSearch.ts";

const clients = [
  { name: "Alpha Steel", gstin: "27AAACA1111A1Z1" },
  { name: "Beta Components", gstin: "29BBBCB2222B1Z2" },
  { name: "Gamma Traders", gstin: "" },
];

test("company search matches name or GSTIN, ignoring case and spacing", () => {
  assert.deepEqual(filterRows(clients, "  BETA ", matchesClient).map((c) => c.name), ["Beta Components"]);
  assert.deepEqual(filterRows(clients, "27aaaca", matchesClient).map((c) => c.name), ["Alpha Steel"]);
  assert.deepEqual(filterRows(clients, "steel alpha", matchesClient).map((c) => c.name), ["Alpha Steel"]);
  assert.deepEqual(filterRows(clients, "nothing here", matchesClient), []);
});

test("a blank search keeps every row and returns a copy", () => {
  const all = filterRows(clients, "   ", matchesClient);
  assert.equal(all.length, 3);
  assert.notEqual(all, clients);
  assert.deepEqual(searchTerms("  a  b "), ["a", "b"]);
});

test("bill search covers invoice number, file name and vendor and tolerates missing fields", () => {
  const bills = [
    { invoice_no: "INV-001", filename: "scan-1.pdf", vendor_name: "Shree Ganesh Metals" },
    { invoice_no: null, filename: "upload.pdf", vendor_name: null },
  ];
  assert.equal(filterRows(bills, "ganesh", matchesBill).length, 1);
  assert.equal(filterRows(bills, "inv-001", matchesBill).length, 1);
  assert.equal(filterRows(bills, "upload", matchesBill).length, 1);
  assert.equal(filterRows(bills, "missing", matchesBill).length, 0);
});

test("queue counts add the named queues and ignore absent or invalid values", () => {
  assert.equal(countOf({ new: 2, needs_review: 3 }, "new", "needs_review"), 5);
  assert.equal(countOf({ new: 2 }, "approved"), 0);
  assert.equal(countOf(undefined, "new"), 0);
  assert.equal(countOf({ new: Number.NaN }, "new"), 0);
});
