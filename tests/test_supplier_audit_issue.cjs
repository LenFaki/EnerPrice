"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const sync = require("../.github/scripts/supplier-audit-issue.cjs");
const body = sync.MARKER + "\nAudit result";
const managed = (overrides = {}) => ({ number: 7, title: sync.TITLE, body: sync.MARKER + "\nOld", state: "open", labels: [], ...overrides });
function fixture(issues = [], maintenance = true, failures = {}) {
  const calls = [];
  const api = {};
  for (const name of ["listForRepo", "getLabel", "createLabel", "create", "update"]) {
    api[name] = async args => {
      calls.push({ name, args });
      if (failures[name]) throw Object.assign(new Error("mock API failure"), { status: failures[name] });
      return {};
    };
  }
  return {
    calls,
    args: {
      github: { rest: { issues: api }, paginate: async (method, args) => {
        assert.equal(method, api.listForRepo);
        assert.equal(args.state, "all");
        assert.equal(args.per_page, 100);
        if (failures.paginate) throw new Error("mock read failure");
        return issues;
      } },
      context: { repo: { owner: "LenFaki", repo: "home-assistant-nl-day-ahead-prices" } },
      core: { info: () => {}, warning: message => calls.push({ name: "warning", message }) },
      report: { structurally_valid: true, maintenance_required: maintenance }, body,
    },
  };
}
test("first finding creates one issue with fixed title, marker and label", async () => {
  const f = fixture(); await sync(f.args);
  const created = f.calls.filter(c => c.name === "create");
  assert.equal(created.length, 1);
  assert.deepEqual(created[0].args, { owner: "LenFaki", repo: "home-assistant-nl-day-ahead-prices", title: sync.TITLE, body, labels: [sync.LABEL] });
});
test("healthy first run does nothing", async () => {
  const f = fixture([], false); await sync(f.args); assert.deepEqual(f.calls, []);
});
for (const [name, state, maintenance, expected] of [
  ["update open", "open", true, "open"],
  ["close healthy", "open", false, "closed"],
  ["reopen later", "closed", true, "open"],
]) test(name, async () => {
  const f = fixture([managed({ state })], maintenance); await sync(f.args);
  assert.deepEqual(f.calls.map(c => c.name), ["update"]);
  assert.equal(f.calls[0].args.state, expected);
  assert.equal(f.calls[0].args.body, body);
  assert.equal(f.calls[0].args.issue_number, 7);
});
test("identical report is idempotent", async () => {
  const f = fixture([managed({ body })]); await sync(f.args); assert.deepEqual(f.calls, []);
});
test("unrelated issues and pull requests are never modified", async () => {
  const f = fixture([managed({ body: "No marker" }), managed({ title: "Unrelated" }), managed({ pull_request: {} })], false);
  await sync(f.args); assert.deepEqual(f.calls, []);
});
test("label and embedded marker identify a renamed managed issue", async () => {
  const f = fixture([managed({ title: "Renamed", labels: [{ name: sync.LABEL }], body: "Intro\n" + body })]);
  await sync(f.args); assert.equal(f.calls[0].name, "update");
});
test("duplicates only update oldest and warn, without mass closing", async () => {
  const f = fixture([managed({ number: 9 }), managed({ number: 2 })], false);
  await sync(f.args);
  assert.deepEqual(f.calls.map(c => c.name), ["warning", "update"]);
  assert.equal(f.calls[1].args.issue_number, 2);
});
for (const [name, failures, labels] of [
  ["create missing label", { getLabel: 404 }, [sync.LABEL]],
  ["label creation race", { getLabel: 404, createLabel: 422 }, [sync.LABEL]],
  ["label permission fallback", { getLabel: 403 }, []],
  ["label creation permission fallback", { getLabel: 404, createLabel: 403 }, []],
]) test(name, async () => {
  const f = fixture([], true, failures); await sync(f.args);
  assert.deepEqual(f.calls.find(c => c.name === "create").args.labels, labels);
});
test("read failure never creates duplicate issue", async () => {
  const f = fixture([], true, { paginate: 500 });
  await assert.rejects(sync(f.args)); assert.deepEqual(f.calls, []);
});
test("unexpected label failure aborts", async () => {
  const f = fixture([], true, { getLabel: 500 });
  await assert.rejects(sync(f.args)); assert.equal(f.calls.some(c => c.name === "create"), false);
});
for (const field of ["structurally_valid", "maintenance_required", "body"]) test("reject invalid " + field, async () => {
  const f = fixture();
  if (field === "body") f.args.body = "Missing marker";
  else f.args.report[field] = null;
  await assert.rejects(sync(f.args)); assert.deepEqual(f.calls, []);
});
