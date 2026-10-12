"use strict";

const MARKER = "<!-- enerprice-supplier-tariff-audit -->";
const TITLE = "EnerPrice supplier tariff audit";
const LABEL = "supplier-tariff-audit";

// Only GitHub API orchestration lives here; Python owns report generation.
module.exports = async function syncIssue({ github, context, core, report, body }) {
  if (report.structurally_valid !== true || typeof report.maintenance_required !== "boolean") {
    throw new Error("Refusing to publish an invalid audit report");
  }
  if (!body.startsWith(MARKER + "\n")) {
    throw new Error("Generated report is missing its managed-issue marker");
  }
  const repo = { owner: context.repo.owner, repo: context.repo.repo };
  const issues = await github.paginate(github.rest.issues.listForRepo, {
    ...repo, state: "all", per_page: 100,
  });
  const managed = issues.filter(issue =>
    !issue.pull_request && typeof issue.body === "string" &&
    issue.body.includes(MARKER) &&
    (issue.title === TITLE || (issue.labels || []).some(label =>
      (typeof label === "string" ? label : label.name) === LABEL))
  ).sort((a, b) => a.number - b.number);
  if (managed.length > 1) {
    core.warning("Multiple managed audit issues found; updating only the oldest. Review duplicates manually.");
  }
  const existing = managed[0];
  if (!existing && !report.maintenance_required) {
    core.info("No maintenance findings and no managed issue: nothing to create.");
    return;
  }
  if (existing) {
    const state = report.maintenance_required ? "open" : "closed";
    if (existing.body !== body || existing.state !== state) {
      await github.rest.issues.update({ ...repo, issue_number: existing.number, body, state });
    }
    core.info("Managed audit issue updated or already up to date.");
    return;
  }
  let labels = [];
  try {
    try {
      await github.rest.issues.getLabel({ ...repo, name: LABEL });
    } catch (error) {
      if (error.status !== 404) throw error;
      try {
        await github.rest.issues.createLabel({
          ...repo, name: LABEL, color: "0E8A16", description: "Human verification of supplier registry tariffs",
        });
      } catch (error) {
        if (error.status !== 422) throw error; // Another caller may have created it.
      }
    }
    labels = [LABEL];
  } catch (error) {
    if (error.status !== 403) throw error;
    core.warning("Audit label unavailable; using the stable title and marker without a label.");
  }
  await github.rest.issues.create({ ...repo, title: TITLE, body, labels });
  core.info("Created the central managed supplier audit issue.");
};

module.exports.MARKER = MARKER;
module.exports.TITLE = TITLE;
module.exports.LABEL = LABEL;
