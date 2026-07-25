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
});
