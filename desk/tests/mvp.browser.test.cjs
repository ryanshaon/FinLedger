const assert = require("node:assert/strict");
const { before, after, test } = require("node:test");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright-core");

const CLIENT_A = "11111111-1111-4111-8111-111111111111";
const CLIENT_B = "22222222-2222-4222-8222-222222222222";
const DOCUMENT = "33333333-3333-4333-8333-333333333333";
const CLIENTS = [
  { id: CLIENT_A, name: "Alpha Steel", gstin: "27AAACA1111A1Z1" },
  { id: CLIENT_B, name: "Beta Components", gstin: "29BBBCB2222B1Z2" },
];
let browser, server, origin;

before(async () => {
  const root = path.resolve(__dirname, "../dist");
  assert.ok(fs.existsSync(path.join(root, "index.html")), "Build the desk before browser tests");
  server = http.createServer((req, res) => {
    const pathname = new URL(req.url, "http://localhost").pathname;
    const file = pathname.startsWith("/assets/") || ["/finledger-mark.png", "/finledger-logo.png"].includes(pathname)
      ? path.resolve(root, `.${pathname}`) : path.join(root, "index.html");
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) {
      res.writeHead(404).end();
      return;
    }
    const types = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".woff2": "font/woff2", ".png": "image/png" };
    res.writeHead(200, { "Content-Type": types[path.extname(file)] || "application/octet-stream" });
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, executablePath: process.env.FINLEDGER_TEST_BROWSER || undefined });
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
  const state = { detail: invoice(options.invoice), roles: options.roles || ["ap_clerk", "approver"], queues: options.queues || {} };
  const json = (route, body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/**", async route => {
    const req = route.request(), url = new URL(req.url());
    if (options.handle && await options.handle({ route, req, url, state, json })) return;
    if (url.pathname === "/api/session") return json(route, { user_id: "staff", firm_id: "firm", firm_admin: false, aal: "aal2", csrf_token: "synthetic-csrf" });
    if (url.pathname === "/api/clients") return json(route, { clients: CLIENTS });
    const client = CLIENTS.find(c => url.pathname.includes(c.id)) || CLIENTS[0];
    if (url.pathname.endsWith("/queue")) {
      const name = url.searchParams.get("queue") || "needs_review";
      const fixtureQueue = state.queues[client.id] || {};
      return json(route, {
        client, queue: name, counts: fixtureQueue.counts || {}, items: (fixtureQueue.items || {})[name] || [],
        roles: state.roles, csrf_token: "synthetic-csrf", max_upload_bytes: 25 * 1024 * 1024,
      });
    }
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

const uuid = n => `44444444-4444-4444-8444-${String(n).padStart(12, "0")}`;
const bill = (n, invoiceNo, vendor, total, date) => ({
  document_id: uuid(n), invoice_no: invoiceNo, filename: `${invoiceNo.toLowerCase()}.pdf`, vendor_name: vendor,
  invoice_date: date, total, band: "low", score: 10, state_label: "Needs review",
});

test("partial company loads keep available counts and can be refreshed", async t => {
  let failing = true;
  const { page } = await fixture(t, {
    queues: { [CLIENT_B]: { counts: { needs_review: 2 } } },
    handle: async ({ route, url, json }) => {
      if (!failing || !url.pathname.includes(CLIENT_A) || !url.pathname.endsWith("/queue")) return false;
      await json(route, { detail: "Temporary company failure" }, 503);
      return true;
    },
  });
  await page.goto(`${origin}/clients`);
  await page.getByRole("status").filter({ hasText: "Counts could not be loaded for Alpha Steel" }).waitFor();
  const rowA = page.getByRole("row", { name: /Alpha Steel/ });
  assert.equal((await rowA.getByRole("cell").allTextContents())[2], "—");
  assert.equal((await page.getByRole("row", { name: /Beta Components/ }).getByRole("cell").allTextContents())[2], "2");
  const brand = page.getByRole("link", { name: "FinLedger", exact: true }).locator("img");
  await brand.evaluate(image => image.decode());
  assert.ok(await brand.evaluate(image => image.naturalWidth > 0));
  failing = false;
  await page.getByRole("button", { name: "Refresh clients", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Counts could not be loaded" }).waitFor({ state: "hidden" });
  assert.equal((await rowA.getByRole("cell").allTextContents())[2], "0");
});

test("header search finds only assigned companies by name or GSTIN, opens them and clears", async t => {
  const { page } = await fixture(t);
  await page.goto(`${origin}/`);
  const search = page.getByLabel("Search companies", { exact: true });
  const results = page.getByRole("list", { name: "Matching companies" });
  await search.fill("  beta ");
  await results.getByRole("link", { name: /Beta Components/ }).waitFor();
  assert.equal(await results.getByRole("link").count(), 1);
  await search.press("Enter");
  await page.waitForURL(`**/clients/${CLIENT_B}`);
  await page.waitForFunction(el => el.value === "", await search.elementHandle());
  assert.equal(await search.inputValue(), "");
  await search.fill("27aaaca");
  await results.getByRole("link", { name: /Alpha Steel/ }).click();
  await page.waitForURL(`**/clients/${CLIENT_A}`);
  await page.waitForFunction(el => el.value === "", await search.elementHandle());
  assert.equal(await search.inputValue(), "");
  await search.fill("no such company");
  await page.getByText("No assigned company matches", { exact: false }).first().waitFor();
  await search.press("Enter");
  assert.equal(new URL(page.url()).pathname, `/clients/${CLIENT_A}`);
  await search.press("Escape");
  await page.waitForFunction(el => el.value === "", await search.elementHandle());
  assert.equal(await search.inputValue(), "");
});

test("clients page shows queue counts from one request per company and filters by name or GSTIN", async t => {
  const queueCalls = [];
  const { page } = await fixture(t, {
    queues: {
      [CLIENT_A]: { counts: { new: 1, needs_review: 2, approved: 0, exception: 1 } },
      [CLIENT_B]: { counts: { needs_review: 1 } },
    },
    handle: async ({ url }) => {
      if (url.pathname.endsWith("/queue")) queueCalls.push(url.searchParams.get("queue"));
      return false;
    },
  });
  await page.goto(`${origin}/clients`);
  const rowA = page.getByRole("row", { name: /Alpha Steel/ });
  await rowA.waitFor();
  assert.deepEqual(await rowA.getByRole("cell").allTextContents(), ["Alpha Steel", "27AAACA1111A1Z1", "2", "1", "1", "4"]);
  assert.deepEqual(await page.getByRole("row", { name: /Beta Components/ }).getByRole("cell").allTextContents(), ["Beta Components", "29BBBCB2222B1Z2", "1", "0", "0", "1"]);
  assert.deepEqual(queueCalls, ["needs_review", "needs_review"]);
  const filter = page.getByLabel("Filter companies by name or GSTIN", { exact: true });
  await filter.fill("29bbb");
  await page.getByText("Showing 1 of 2 companies").waitFor();
  assert.equal(await page.getByRole("row", { name: /Alpha Steel/ }).count(), 0);
  await filter.fill("nothing");
  await page.getByText("No company matches", { exact: false }).waitFor();
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await page.getByText("2 companies", { exact: true }).waitFor();
  assert.equal(await filter.inputValue(), "");
  assert.equal(await page.getByRole("row", { name: /Alpha Steel/ }).count(), 1);
});

test("queue tabs show counts and bills can be searched, cleared and reset by tab", async t => {
  const { page } = await fixture(t, { queues: { [CLIENT_A]: {
    counts: { new: 2, needs_review: 3, approved: 1, exception: 0 },
    items: { needs_review: [
      bill(1, "INV-001", "Shree Ganesh Metals", "11800", "2026-10-03"),
      bill(2, "INV-002", "Omkar Packaging", "5400", "2026-10-02"),
      bill(3, "INV-003", "Shree Ganesh Metals", "2300", "2026-10-01"),
    ] },
  } } });
  await page.goto(`${origin}/clients/${CLIENT_A}?queue=needs_review`);
  await page.getByRole("link", { name: "New: 2", exact: true }).waitFor();
  await page.getByRole("link", { name: "Needs review: 3", exact: true }).waitFor();
  await page.getByRole("link", { name: "Exception: 0", exact: true }).waitFor();
  const rows = page.locator("tbody tr");
  await rows.first().waitFor();
  assert.equal(await rows.count(), 3);
  const search = page.getByLabel("Search bills by vendor, invoice number or file name", { exact: true });
  await search.fill("ganesh");
  await page.getByText("Showing 2 of 3 bills").waitFor();
  assert.equal(await rows.count(), 2);
  await search.fill("inv-002");
  await page.getByText("Showing 1 of 3 bills").waitFor();
  await search.fill("zzz");
  await page.getByText("No bill in needs review matches", { exact: false }).waitFor();
  assert.equal(await rows.count(), 0);
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await page.getByText("3 bills", { exact: true }).waitFor();
  assert.equal(await rows.count(), 3);
  await search.fill("omkar");
  await page.getByRole("link", { name: "Approved: 1", exact: true }).click();
  await page.waitForURL(`**/clients/${CLIENT_A}?queue=approved`);
  await page.getByText("No bills are in approved.").waitFor();
  assert.equal(await page.getByLabel("Search bills by vendor, invoice number or file name", { exact: true }).count(), 0);
});

test("dashboard retries a failed load, counts honestly, refreshes and links to company queues", async t => {
  const calls = [];
  let failing = true;
  const { page } = await fixture(t, {
    queues: {
      [CLIENT_A]: { counts: { new: 1, needs_review: 2, approved: 3, exception: 1 }, items: { needs_review: [
        bill(11, "INV-101", "Shree Ganesh Metals", "1000", "2026-10-05"), bill(12, "INV-102", "Omkar Packaging", "2500", "2026-10-04"),
      ] } },
      [CLIENT_B]: { counts: { needs_review: 1 }, items: { needs_review: [bill(13, "INV-201", "Kaveri Textiles", "500", "2026-10-06")] } },
    },
    handle: async ({ route, url, json }) => {
      if (!url.pathname.endsWith("/queue")) return false;
      calls.push(url.searchParams.get("queue"));
      if (!failing) return false;
      await json(route, { detail: "Server unavailable" }, 500);
      return true;
    },
  });
  await page.goto(`${origin}/`);
  await page.getByRole("alert").filter({ hasText: "Server unavailable" }).waitFor();
  failing = false;
  calls.length = 0;
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  const review = page.locator("article").filter({ hasText: "Needs review" });
  await review.waitFor();
  assert.deepEqual(calls, ["needs_review", "needs_review"]);
  assert.match(await review.textContent(), /Needs review\s*3.*4,000\.00 in total/);
  assert.match(await page.locator("article").filter({ hasText: "New" }).textContent(), /New\s*1/);
  assert.match(await page.locator("article").filter({ hasText: "Approved" }).textContent(), /Approved\s*3/);
  assert.match(await page.locator("article").filter({ hasText: "Exceptions" }).textContent(), /Exceptions\s*1/);
  const waiting = page.locator("section").filter({ has: page.getByRole("heading", { name: "Waiting for review", exact: true }) });
  assert.match(await waiting.getByRole("link").first().textContent(), /INV-201/);
  await Promise.all([
    page.waitForResponse(r => r.url().includes("/queue")),
    page.getByRole("button", { name: "Refresh", exact: true }).click(),
  ]);
  await page.getByText(/Updated/).waitFor();
  await page.getByRole("link", { name: "Exceptions 1", exact: true }).click();
  await page.waitForURL(`**/clients/${CLIENT_A}?queue=exception`);
});

test("keyboard users can skip to the content and narrow screens do not scroll sideways", async t => {
  const { page } = await fixture(t, { queues: { [CLIENT_A]: { counts: { needs_review: 1 }, items: { needs_review: [bill(21, "INV-301", "Shree Ganesh Metals", "900", "2026-10-07")] } } } });
  await page.goto(`${origin}/clients/${CLIENT_A}`);
  await page.getByRole("heading", { name: "Alpha Steel", exact: true }).waitFor();
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to main content", exact: true });
  assert.equal(await skip.evaluate(el => el === document.activeElement), true);
  await page.keyboard.press("Enter");
  assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.id), "main");
  await page.setViewportSize({ width: 390, height: 800 });
  for (const path of ["/", "/clients", `/clients/${CLIENT_A}`]) {
    await page.goto(`${origin}${path}`);
    await page.getByRole("heading", { level: 1 }).first().waitFor();
    await page.waitForLoadState("networkidle");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, `${path} scrolls sideways`);
  }
});
