import { expect, test } from "@playwright/test";

import { signUp } from "./helpers";

test("signup + onboarding lands on dashboard and session survives refresh", async ({ page }) => {
  const email = await signUp(page, "auth");

  await expect(page.getByTestId("dashboard-heading")).toBeVisible();
  await expect(page.getByTestId("user-email")).toHaveText(email);

  // A full page reload keeps the session (cookie-based).
  await page.reload();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByTestId("user-email")).toHaveText(email);
});

test("protected dashboard redirects to login when unauthenticated", async ({ page }) => {
  await page.context().clearCookies();
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login/);
});

test("logout returns to login and blocks the dashboard", async ({ page }) => {
  await signUp(page, "auth");

  await page.getByTestId("user-menu").click();
  await page.getByRole("menuitem", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login/);

  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login/);
});
