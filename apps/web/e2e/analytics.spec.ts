import { expect, test } from "@playwright/test";

import { signUpAndSeedText } from "./helpers";

const API_URL = "http://localhost:8020";

test("analytics reflects real traffic, not placeholders", async ({ page }) => {
  // The same doc/question shape the playground spec uses, so the offline model
  // stub can actually ground an answer here.
  await signUpAndSeedText(
    page,
    "Descaling Guide",
    "# Descaling\n\nDescale the machine every three months with normal use. " +
      "Use only food-safe descaling solution and never use vinegar.",
    "analytics",
  );

  // A fresh org starts empty.
  await page.goto("/dashboard/analytics");
  await expect(page.getByTestId("stat-conversations")).toHaveText("0");
  await expect(page.getByTestId("stat-csat")).toHaveText("—");

  // One answerable question and one that must escalate.
  const orgId = await page.evaluate(async (api) => {
    const me = await (await fetch(`${api}/auth/me`, { credentials: "include" })).json();
    const org = me.memberships[0].org_id;
    for (const message of ["How often should I descale?", "What is the CEO's shoe size?"]) {
      await fetch(`${api}/api/v1/chat`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org_id: org, message, bypass_cache: true }),
      }).then((r) => r.text());
    }
    return org as string;
  }, API_URL);
  expect(orgId).toBeTruthy();

  await page.reload();

  // Two conversations, one of them escalated -> 50% deflection, 50% escalation.
  await expect(page.getByTestId("stat-conversations")).toHaveText("2");
  await expect(page.getByTestId("stat-deflection")).toHaveText("50%");
  await expect(page.getByTestId("stat-escalation")).toHaveText("50%");

  // The escalated question shows up as an unanswered gap.
  await expect(page.getByTestId("unanswered-row")).toContainText("shoe size");

  // The chart renders, and its table view carries the same numbers.
  await expect(page.getByTestId("conversations-chart")).toBeVisible();
  await page.getByRole("button", { name: "Show data" }).click();
  await expect(page.getByRole("cell", { name: "2", exact: true })).toBeVisible();

  // Range filters re-query.
  await page.getByTestId("range-7").click();
  await expect(page.getByText("Last 7 days")).toBeVisible();

  // Quality card (6.6). A fresh org has never been sampled by the nightly job, so
  // it must say so rather than render 0% — which would read as "every answer is a
  // hallucination" on an org whose answers were never scored at all.
  await expect(page.getByTestId("quality-card")).toBeVisible();
  await expect(page.getByTestId("quality-card")).toContainText("Not measured yet");
  await expect(page.getByTestId("quality-score")).toHaveCount(0);
});

test("the quality card shows the latest nightly score", async ({ page }) => {
  await signUpAndSeedText(
    page,
    "Descaling Guide",
    "# Descaling\n\nDescale the machine every three months with normal use.",
    "quality",
  );

  // Stub the endpoint rather than wait for a real nightly run: this asserts the
  // card renders what the API returns, which is the part of 6.6 that lives here.
  // The service's own correctness against eval_runs is covered by pytest.
  await page.route("**/api/v1/analytics/quality*", async (route) => {
    await route.fulfill({
      json: {
        latest_faithfulness: 0.923,
        latest_sample_size: 27,
        latest_at: "2026-07-29T03:15:00Z",
        baseline_faithfulness: 0.951,
        delta: -0.028,
        alerted: true,
        trend: [
          { day: "2026-07-27", faithfulness: 0.95, sample_size: 20 },
          { day: "2026-07-28", faithfulness: 0.95, sample_size: 24 },
          { day: "2026-07-29", faithfulness: 0.923, sample_size: 27 },
        ],
      },
    });
  });

  await page.goto("/dashboard/analytics");

  await expect(page.getByTestId("quality-score")).toHaveText("92.3%");
  // The denominator travels with the score: 92.3% from 27 answers is a different
  // claim from 92.3% from two.
  await expect(page.getByTestId("quality-card")).toContainText("27 answers scored");
  await expect(page.getByTestId("quality-delta")).toContainText("2.8 pts");
  await expect(page.getByTestId("quality-alert")).toBeVisible();
});
