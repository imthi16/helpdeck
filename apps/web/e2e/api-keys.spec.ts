import { expect, test } from "@playwright/test";

import { signUp } from "./helpers";

test("widget snippet shows a real key, and a secret key is revealed once", async ({ page }) => {
  await signUp(page, "keys");
  await page.goto("/dashboard/settings");
  await page.getByRole("tab", { name: "API keys" }).click();

  // The embed snippet carries the org's live public key (no placeholder).
  const snippet = await page.getByTestId("embed-snippet").textContent();
  expect(snippet).toContain('data-public-key="pk_');
  expect(snippet).not.toContain("pk_your_key");

  // One key to start with: the widget key, never used yet.
  const rows = page.getByTestId("key-row");
  await expect(rows).toHaveCount(1);
  await expect(rows.first().getByTestId("key-last-used")).toHaveText("never");

  // Creating a secret key reveals it exactly once.
  await page.getByTestId("key-name").fill("CI pipeline");
  await page.getByTestId("key-create").click();
  const secret = await page.getByTestId("fresh-secret").textContent();
  expect(secret?.trim()).toMatch(/^sk_/);
  await expect(rows).toHaveCount(2);

  // It is gone from the page after a reload — only its prefix remains.
  await page.reload();
  await page.getByRole("tab", { name: "API keys" }).click();
  await expect(page.getByTestId("fresh-secret")).toHaveCount(0);
  const secretRow = page.getByTestId("key-row").filter({ hasText: "CI pipeline" });
  await expect(secretRow).toContainText(secret!.trim().slice(0, 11));

  // Revoking marks it revoked and disables further revocation.
  await secretRow.getByTestId("key-revoke").click();
  await page.getByTestId("confirm-revoke").click();
  await expect(secretRow).toContainText("revoked");
  await expect(secretRow.getByTestId("key-revoke")).toBeDisabled();
});

test("revoking the widget key rotates it", async ({ page }) => {
  await signUp(page, "rotate");
  await page.goto("/dashboard/settings");
  await page.getByRole("tab", { name: "API keys" }).click();

  const before = await page.getByTestId("embed-snippet").textContent();

  await page.getByTestId("key-row").first().getByTestId("key-revoke").click();
  await page.getByTestId("confirm-revoke").click();

  // A fresh widget key is issued automatically, so the snippet stays usable.
  await expect(page.getByTestId("embed-snippet")).not.toHaveText(before!);
  expect(await page.getByTestId("embed-snippet").textContent()).toContain('data-public-key="pk_');
  await expect(page.getByTestId("key-row")).toHaveCount(2);
});
