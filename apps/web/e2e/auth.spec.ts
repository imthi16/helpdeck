import { expect, test } from "@playwright/test";

function uniqueEmail(): string {
  return `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

test("signup lands on dashboard and session survives refresh", async ({ page }) => {
  const email = uniqueEmail();

  await page.goto("/signup");
  await page.getByLabel("Your name").fill("E2E User");
  await page.getByLabel("Organization name").fill("E2E Coffee Co");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("supersecret1");
  await page.getByRole("button", { name: "Create account" }).click();

  // Lands on the dashboard.
  await expect(page).toHaveURL(/\/dashboard$/);
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
  const email = uniqueEmail();

  await page.goto("/signup");
  await page.getByLabel("Your name").fill("Logout User");
  await page.getByLabel("Organization name").fill("Logout Co");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("supersecret1");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login/);

  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login/);
});
