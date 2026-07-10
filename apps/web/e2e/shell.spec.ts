import { expect, test } from "@playwright/test";

import { signUp } from "./helpers";

test("sidebar navigates between sections on desktop", async ({ page }) => {
  await signUp(page, "shell");
  await expect(page.getByTestId("org-name")).toHaveText("E2E Coffee Co");

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
  await signUp(page, "shell");

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
