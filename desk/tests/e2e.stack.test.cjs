// The Phase 1 journeys through the real screens, API, workers and database, with the mock provider and a SYNTHETIC Tally.
// Started by `npm run test:stack`. Needs a built desk, PostgreSQL tools on PATH, and FINLEDGER_STACK_PYTHON (or
// person3_control_ui/.venv). Set FINLEDGER_TEST_BROWSER to use an installed browser instead of Playwright's Chromium.
const assert = require("node:assert/strict");
const { before, after, test } = require("node:test");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");
const { chromium } = require("playwright-core");

const ROOT = path.resolve(__dirname, "../..");
const VENV = process.platform === "win32" ? "person3_control_ui/.venv/Scripts/python.exe" : "person3_control_ui/.venv/bin/python";
const PYTHON = process.env.FINLEDGER_STACK_PYTHON || path.join(ROOT, VENV);
let stack, browser, harness;
let serial = 0;

before(async () => {
  harness = spawn(PYTHON, ["-I", path.join(ROOT, "scripts", "phase1_stack.py")], { cwd: ROOT, stdio: ["ignore", "pipe", "inherit"] });
  stack = await new Promise((resolve, reject) => {
    harness.once("exit", code => reject(new Error(`the stack exited with ${code}`)));
    readline.createInterface({ input: harness.stdout }).once("line", line => resolve(JSON.parse(line)));
  });
  browser = await chromium.launch({ headless: true, executablePath: process.env.FINLEDGER_TEST_BROWSER || undefined });
}, { timeout: 180000 });

after(async () => {
  if (browser) await browser.close();
  if (stack) await fetch(`${stack.harness}/stop`).catch(() => {});
  await new Promise(resolve => setTimeout(resolve, 2000));
  if (harness) harness.kill();
});

async function control(pathname) {
  const response = await fetch(`${stack.harness}${pathname}`);
  assert.equal(response.status, 200);
  return response.json();
}

async function signIn(t, who) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
  t.after(() => context.close());
  const page = await context.newPage();
  await page.goto(`${stack.desk}/clients`);
  await page.locator('input[name="email"]').fill(stack.users[who][0]);
  await page.locator('input[name="password"]').fill(stack.users[who][1]);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByText("Sign out", { exact: true }).first().waitFor();
  return page;
}

async function queuePage(page, queue) {
  await page.goto(`${stack.desk}/clients/${stack.client_id}?queue=${queue}`);
}

// Upload through the form as the clerk, wait for the mock extraction, and return the bill's page address.
async function uploadBill(t, { taxed = false } = {}) {
  const number = `STACK-${Date.now()}-${++serial}`;
  const pdf = Buffer.from(await (await fetch(`${stack.harness}/pdf?number=${number}&taxed=${taxed ? 1 : 0}`)).arrayBuffer());
  const clerk = await signIn(t, "clerk");
  await queuePage(clerk, "needs_review");
  await clerk.locator('input[type="file"]').setInputFiles({ name: `${number}.pdf`, mimeType: "application/pdf", buffer: pdf });
  await clerk.getByRole("button", { name: "Upload", exact: true }).click();
  await clerk.getByRole("status").filter({ hasText: "received" }).first().waitFor();
  for (let tries = 0; tries < 90; tries += 1) {
    await queuePage(clerk, "needs_review");
    const link = clerk.getByRole("link", { name: number, exact: true });
    if (await link.count().catch(() => 0)) {
      const href = await link.getAttribute("href");
      return { number, url: `${stack.desk}${href}`, clerk };
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error(`${number} never reached review`);
}

async function ensurePosting(t, on) {
  const owner = await signIn(t, "owner");
  await queuePage(owner, "needs_review");
  const panel = owner.getByRole("region", { name: "Tally posting" });
  await panel.getByRole("status").waitFor();
  const isOn = (await panel.getByRole("status").innerText()).includes("ON");
  if (on && !isOn) {
    await panel.getByLabel("Company GUID").fill(stack.guid);
    await panel.getByLabel("Type the company name").fill(stack.company);
    await panel.getByRole("button", { name: "Enable posting", exact: true }).click();
    await panel.getByRole("status").filter({ hasText: "ON" }).waitFor();
  }
  if (!on && isOn) {
    await panel.getByRole("button", { name: "Disable posting", exact: true }).click();
    await panel.getByRole("status").filter({ hasText: "off" }).waitFor();
  }
  return owner;
}

test("only the owner sees the switch controls", { timeout: 120000 }, async t => {
  await ensurePosting(t, false);
  const approver = await signIn(t, "approver");
  await queuePage(approver, "needs_review");
  const panel = approver.getByRole("region", { name: "Tally posting" });
  await panel.getByRole("status").waitFor();
  assert.equal(await panel.getByRole("button", { name: "Enable posting" }).count(), 0);
});

test("posting off: the post button is disabled with the reason, and no job is possible", { timeout: 180000 }, async t => {
  await ensurePosting(t, false);
  const { url } = await uploadBill(t);
  const approver = await signIn(t, "approver");
  await approver.goto(url);
  const button = approver.getByRole("button", { name: "Approve and post to Tally", exact: true });
  await button.waitFor();
  assert.equal(await button.isDisabled(), true);
  await approver.getByText("Posting is off for this company.").waitFor();
});

test("exempt bill: maker-checker, approve and post, one Import, Posted with its Tally ID", { timeout: 240000 }, async t => {
  await ensurePosting(t, true);
  await control("/tally?mode=create");
  const { url, clerk } = await uploadBill(t);
  // the clerk edits the bill, so the clerk may not approve it
  await clerk.goto(url);
  await clerk.getByRole("button", { name: "Save edit", exact: true }).waitFor();
  const ledger = clerk.getByLabel("Edit party ledger");
  await ledger.fill("Shree Ganesh Metals ");
  await ledger.fill("Shree Ganesh Metals");
  await clerk.getByRole("button", { name: "Save edit", exact: true }).click();
  await clerk.getByRole("status").filter({ hasText: "Party ledger saved." }).waitFor();
  assert.equal(await clerk.getByRole("button", { name: "Approve", exact: true }).count(), 0);

  const approver = await signIn(t, "approver");
  await approver.goto(url);
  await approver.getByRole("button", { name: "Approve and post to Tally", exact: true }).click();
  await approver.getByText("Waiting for agent", { exact: true }).waitFor();

  const wrong = await control("/run-once?answer=not-the-phrase");
  assert.equal(wrong.reason, "not_confirmed");
  assert.equal((await control("/imports")).imports, 0);
  await new Promise(resolve => setTimeout(resolve, 61000)); // a released job waits 60 seconds before it can be claimed

  const posted = await control("/run-once");
  assert.equal(posted.ok, true, JSON.stringify(posted));
  assert.equal(posted.outcome, "posted");
  assert.equal((await control("/imports")).imports, 1);
  await approver.reload();
  await approver.getByText(`Posted. Tally ID ${posted.erp_id}`, { exact: true }).waitFor();
  assert.equal((await control("/run-once")).reason, "no_job");
  assert.equal((await control("/imports")).imports, 1);
});

test("taxed bill: approved with the reason shown, and no job exists", { timeout: 180000 }, async t => {
  await ensurePosting(t, true);
  await control("/tally?mode=create");
  const { url } = await uploadBill(t, { taxed: true });
  const approver = await signIn(t, "approver");
  await approver.goto(url);
  const button = approver.getByRole("button", { name: "Approve and post to Tally", exact: true });
  await button.waitFor();
  assert.equal(await button.isDisabled(), true);
  await approver.getByText("This bill has tax. Phase 1 can't post taxed bills.", { exact: false }).waitFor();
  await approver.getByRole("button", { name: "Approve", exact: true }).click();
  await approver.getByRole("status").filter({ hasText: "Approved." }).waitFor();
  assert.equal(await approver.getByRole("region", { name: "Tally posting" }).count(), 0);
  assert.equal((await control("/run-once")).reason, "no_job");
  assert.equal((await control("/imports")).imports, 0);
});

test("Tally rejection: shown in Exceptions, no retry, returns to review as a new revision", { timeout: 240000 }, async t => {
  await ensurePosting(t, true);
  await control("/tally?mode=reject");
  const { url, number } = await uploadBill(t);
  const approver = await signIn(t, "approver");
  await approver.goto(url);
  await approver.getByRole("button", { name: "Approve and post to Tally", exact: true }).click();
  await approver.getByText("Waiting for agent", { exact: true }).waitFor();
  const result = await control("/run-once");
  assert.equal(result.outcome, "failed", JSON.stringify(result));
  assert.equal((await control("/imports")).imports, 1);
  await queuePage(approver, "exception");
  await approver.getByRole("link", { name: number, exact: true }).waitFor();
  await approver.getByText("tally_rejected", { exact: false }).first().waitFor();
  await approver.goto(url);
  await approver.getByText("Tally rejected this bill", { exact: false }).waitFor();
  await approver.getByLabel("Note (at least 10 characters)").fill("Ledger fixed in Tally");
  await approver.getByRole("button", { name: "Return to review", exact: true }).click();
  await approver.getByText("Revision 2", { exact: false }).waitFor();
  assert.equal((await control("/imports")).imports, 1);
});

test("uncertain outcome: warning shown, no retry, only the owner can reconcile", { timeout: 240000 }, async t => {
  await ensurePosting(t, true);
  await control("/tally?mode=hangup_before_create");
  const { url, number } = await uploadBill(t);
  const approver = await signIn(t, "approver");
  await approver.goto(url);
  await approver.getByRole("button", { name: "Approve and post to Tally", exact: true }).click();
  await approver.getByText("Waiting for agent", { exact: true }).waitFor();
  const result = await control("/run-once");
  assert.equal(result.outcome, "uncertain", JSON.stringify(result));
  assert.equal((await control("/imports")).imports, 1);
  await approver.reload();
  await approver.getByText("Uncertain: do not re-enter this bill.", { exact: false }).waitFor();
  assert.equal(await approver.getByRole("button", { name: "Mark as not in Tally" }).count(), 0);
  assert.equal(await approver.getByRole("button", { name: "Return to review" }).count(), 0);
  await queuePage(approver, "exception");
  await approver.getByRole("link", { name: number, exact: true }).waitFor();

  const owner = await signIn(t, "owner");
  await owner.goto(url);
  await owner.getByLabel("Note (at least 10 characters)").fill("Not in Day Book for that date");
  await owner.getByRole("button", { name: "Mark as not in Tally", exact: true }).click();
  await owner.getByText("You recorded this bill as not in Tally.", { exact: false }).waitFor();
  assert.equal((await control("/run-once")).reason, "no_job");
  assert.equal((await control("/imports")).imports, 1);
});
