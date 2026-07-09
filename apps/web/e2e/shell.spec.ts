import { expect, test, type Page } from "@playwright/test";

async function signUp(page: Page): Promise<void> {
  const email = `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("Shell User");
  await page.getByLabel("Organization name").fill("Shell Coffee Co");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("supersecret1");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

test("sidebar navigates between sections on desktop", async ({ page }) => {
  await signUp(page);
  await expect(page.getByTestId("org-name")).toHaveText("Shell Coffee Co");

  for (const [label, heading] of [
    ["Knowledge Base", "Knowledge Base"],
    ["Playground", "Playground"],
    ["Conversations", "Conversations"],
    ["Analytics", "Analytics"],
    ["Settings", "Settings"],
  ]) {
    await page.getByRole("link", { name: label, exact: true }).click();
    await expect(page.getByTestId("page-heading")).toHaveText(heading);
  }
});

test("mobile layout uses a drawer for navigation at 375px", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await signUp(page);

  // Desktop sidebar links are not visible at this width.
  const desktopLink = page.getByRole("link", { name: "Playground", exact: true });
  await expect(desktopLink).toBeHidden();

  // Open the drawer and navigate.
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("link", { name: "Conversations", exact: true }).click();
  await expect(page.getByTestId("page-heading")).toHaveText("Conversations");

  // No horizontal overflow at mobile width.
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth + 1,
  );
  expect(overflow).toBe(true);
});
