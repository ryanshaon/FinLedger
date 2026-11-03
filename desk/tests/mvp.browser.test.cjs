const assert = require("node:assert/strict");
const { before, after, test } = require("node:test");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright-core");

const CLIENT_A = "11111111-1111-4111-8111-111111111111";
const CLIENT_B = "22222222-2222-4222-8222-222222222222";
const DOCUMENT = "33333333-3333-4333-8333-333333333333";
const CLIENTS = [{ id: CLIENT_A, name: "Alpha Steel" }, { id: CLIENT_B, name: "Beta Components" }];
let browser, server, origin;

before(async () => {
  const root = path.resolve(__dirname, "../dist");
  assert.ok(fs.existsSync(path.join(root, "index.html")), "Build the desk before browser tests");
  server = http.createServer((req, res) => {
    const pathname = new URL(req.url, "http://localhost").pathname;
    const file = pathname.startsWith("/assets/") ? path.resolve(root, `.${pathname}`) : path.join(root, "index.html");
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) {
      res.writeHead(404).end();
      return;
    }
    const types = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".woff2": "font/woff2" };
    res.writeHead(200, { "Content-Type": types[path.extname(file)] || "application/octet-stream" });
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true });
});

after(async () => {
  if (browser) await browser.close();
  if (server) await new Promise(resolve => server.close(resolve));
});

function invoice(overrides = {}) {
  return {
    document_id: DOCUMENT, filename: "invoice.pdf", status: "in_review", state_label: "Needs review",
    revision: 1, source_url: "", can_edit: true, can_approve: true, approval_note: "",
    invoice: { invoice_no: "INV-42", invoice_date: "2026-10-01", vendor: { name: "Synthetic vendor", gstin: "27AAACS1234A1Z2" },
      taxable: "10000", cgst: "900", sgst: "900", igst: "0", cess: "0", round_off: "0", total: "11800" },
    draft: { party_ledger: "Supplier creditors", lines: [{ ledger: "Purchases", amount: 11800, is_debit: true }] },
    risk: { band: "high", score: 60, marks: [
      { check: "gstin_checksum", ok: true, note: "Valid" },
      { check: "duplicate", ok: false, note: "Review this duplicate" },
      { check: "msme_45_day", note: "Awaiting supplier classification" },
    ] },
    ...overrides,
  };
}

async function fixture(t, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  t.after(() => context.close());
  const page = await context.newPage();
  const state = { detail: invoice(options.invoice), roles: options.roles || ["ap_clerk", "approver"] };
  const json = (route, body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/**", async route => {
    const req = route.request(), url = new URL(req.url());
    if (options.handle && await options.handle({ route, req, url, state, json })) return;
    if (url.pathname === "/api/session") return json(route, { user_id: "staff", firm_id: "firm", firm_admin: false, aal: "aal2", csrf_token: "synthetic-csrf" });
    if (url.pathname === "/api/clients") return json(route, { clients: CLIENTS });
    const client = CLIENTS.find(c => url.pathname.includes(c.id)) || CLIENTS[0];
    if (url.pathname.endsWith("/queue")) return json(route, {
      client, queue: url.searchParams.get("queue") || "needs_review", counts: {}, items: [],
      roles: state.roles, csrf_token: "synthetic-csrf", max_upload_bytes: 25 * 1024 * 1024,
    });
    if (url.pathname.includes("/invoices/")) return json(route, {
      client, roles: state.roles, csrf_token: "synthetic-csrf", invoice: state.detail,
    });
    return json(route, { detail: "Unexpected test request" }, 404);
  });
  return { page, state };
}

test("staff upload sends the original bytes and CSRF to the selected company", async t => {
  let sent;
  const { page } = await fixture(t, { handle: async ({ route, req, url, json }) => {
    if (!url.pathname.endsWith("/documents")) return false;
    sent = { url, headers: req.headers(), data: req.postDataBuffer() };
    await json(route, { received: [{ document_id: DOCUMENT, filename: "bill #1.pdf", content_hash: "synthetic" }], rejected: [] }, 201);
    return true;
  } });
  await page.goto(`${origin}/clients/${CLIENT_A}`);
  await page.getByLabel("Upload a bill", { exact: true }).setInputFiles({ name: "bill #1.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\noriginal") });
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await page.waitForURL(`**/clients/${CLIENT_A}?queue=new`);
  assert.equal(sent.url.pathname, `/api/clients/${CLIENT_A}/documents`);
  assert.equal(sent.headers["x-csrf-token"], "synthetic-csrf");
  assert.equal(sent.headers["x-filename"], encodeURIComponent("bill #1.pdf"));
  assert.equal(sent.data.toString(), "%PDF-1.4\noriginal");
});

test("payer sees neither upload controls nor excluded primary navigation", async t => {
  const { page } = await fixture(t, { roles: ["payer"] });
  await page.goto(`${origin}/clients/${CLIENT_A}`);
  await page.getByRole("heading", { name: "Alpha Steel", exact: true }).waitFor();
  assert.equal(await page.getByLabel("Upload a bill", { exact: true }).count(), 0);
  assert.equal(await page.getByRole("link", { name: "Transactions", exact: true }).count(), 0);
  assert.equal(await page.getByRole("link", { name: "Posted", exact: true }).count(), 0);
});

test("oversized and unsupported files are rejected before a network upload", async t => {
  let calls = 0;
  const { page } = await fixture(t, { handle: async ({ route, url, json }) => {
    if (!url.pathname.endsWith("/documents")) return false;
    calls++;
    await json(route, { received: [], rejected: [] }, 422);
    return true;
  } });
  await page.goto(`${origin}/clients/${CLIENT_A}`);
  const input = page.getByLabel("Upload a bill", { exact: true });
  await input.setInputFiles({ name: "statement.csv", mimeType: "text/csv", buffer: Buffer.from("not a bill") });
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "Choose a PDF" }).waitFor();
  await input.setInputFiles({ name: "large.pdf", mimeType: "application/pdf", buffer: Buffer.alloc(25 * 1024 * 1024 + 1) });
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "larger than 25 MB" }).waitFor();
  assert.equal(calls, 0);
});

test("a late upload cannot navigate away from a newly selected company", async t => {
  let release;
  const waiting = new Promise(resolve => { release = resolve; });
  const { page } = await fixture(t, { handle: async ({ route, url, json }) => {
    if (!url.pathname.endsWith("/documents")) return false;
    await waiting;
    await json(route, { received: [{ document_id: DOCUMENT, filename: "old.pdf", content_hash: "synthetic" }], rejected: [] }, 201);
    return true;
  } });
  await page.goto(`${origin}/clients/${CLIENT_A}`);
  await page.getByLabel("Upload a bill", { exact: true }).setInputFiles({ name: "old.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") });
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await page.getByText("Uploading the bill. Keep this page open.").waitFor();
  await page.getByRole("button", { name: "Alpha Steel", exact: true }).click();
  await page.getByRole("menuitem", { name: "Beta Components", exact: true }).click();
  await page.getByRole("heading", { name: "Beta Components", exact: true }).waitFor();
  const completed = page.waitForResponse(r => r.url().endsWith("/documents"));
  release();
  await completed;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(new URL(page.url()).pathname, `/clients/${CLIENT_B}`);
  assert.equal(new URL(page.url()).search, "");
  assert.equal(await page.getByText("bill received", { exact: false }).count(), 0);
});

test("invoice shows tax and all check states while own-edit approval is unavailable", async t => {
  const { page } = await fixture(t, { invoice: { can_approve: false, approval_note: "Another reviewer must approve a bill you made or edited." } });
  await page.goto(`${origin}/clients/${CLIENT_A}/invoices/${DOCUMENT}`);
  await page.getByText("CGST", { exact: true }).waitFor();
  await page.getByText("Gstin checksum: Passed", { exact: false }).waitFor();
  await page.getByText("Duplicate: Needs attention", { exact: false }).waitFor();
  await page.getByText("Msme 45 day: Not checked", { exact: false }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Approve", exact: true }).count(), 0);
  await page.getByText("Another reviewer must approve", { exact: false }).waitFor();
  assert.equal(await page.getByRole("heading", { name: "Three-way match", exact: true }).count(), 0);
  assert.equal(await page.getByText("TDS", { exact: true }).count(), 0);
});

test("desk approval sends post false and refreshes the approved result", async t => {
  let saved;
  const { page } = await fixture(t, { handle: async ({ route, req, url, state, json }) => {
    if (!url.pathname.endsWith("/approve")) return false;
    saved = req.postDataJSON();
    state.detail.status = "approved";
    state.detail.state_label = "Approved";
    state.detail.can_approve = false;
    await json(route, { status: "approved", revision: 1 });
    return true;
  } });
  await page.goto(`${origin}/clients/${CLIENT_A}/invoices/${DOCUMENT}`);
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Approved." }).waitFor();
  assert.equal(saved.post, false);
  assert.equal(saved.csrf_token, "synthetic-csrf");
});

test("revision conflicts refresh the actual ledger and revision", async t => {
  const { page } = await fixture(t, { handle: async ({ route, url, state, json }) => {
    if (!url.pathname.endsWith("/edit")) return false;
    state.detail.revision = 2;
    state.detail.draft.party_ledger = "Saved by another reviewer";
    await json(route, { detail: "Draft changed; refresh before saving" }, 409);
    return true;
  } });
  await page.goto(`${origin}/clients/${CLIENT_A}/invoices/${DOCUMENT}`);
  await page.getByLabel("Edit party ledger").fill("My stale change");
  await page.getByRole("button", { name: "Save edit", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "latest version" }).waitFor();
  await page.getByText("Revision 2", { exact: false }).waitFor();
  assert.equal(await page.getByLabel("Edit party ledger").inputValue(), "Saved by another reviewer");
});

test("unprocessed bills expose no edit or approval controls", async t => {
  const { page } = await fixture(t, { invoice: { revision: 0, status: "received", invoice: {}, draft: {}, can_edit: false, can_approve: false } });
  await page.goto(`${origin}/clients/${CLIENT_A}/invoices/${DOCUMENT}`);
  await page.getByText("not ready for review yet", { exact: false }).waitFor();
  assert.equal(await page.getByLabel("Edit party ledger").count(), 0);
  assert.equal(await page.getByRole("button", { name: "Approve", exact: true }).count(), 0);
});
