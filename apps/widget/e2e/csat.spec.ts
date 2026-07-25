import { expect, test } from "@playwright/test";

const HOST = "/examples/e2e.html";
const FRAME = 'iframe[data-helpdeck="frame"]';

test("closing an answered conversation asks for a rating, once", async ({ page }) => {
  await page.goto(HOST);
  await page.locator('button[data-helpdeck="launcher"]').click();

  const app = page.frameLocator(FRAME);
  await app.getByTestId("widget-input").fill("How often should I descale?");
  await app.getByTestId("widget-input").press("Enter");
  await expect(app.getByTestId("msg-assistant").first()).toBeVisible();
  await expect(app.getByTestId("widget-input")).toBeEnabled({ timeout: 20_000 });

  // No prompt while the conversation is live.
  await expect(app.getByTestId("csat-prompt")).toHaveCount(0);

  // Closing asks how it went instead of just disappearing.
  await app.getByRole("button", { name: "Close chat" }).click();
  await expect(app.getByTestId("csat-prompt")).toBeVisible();

  await app.getByTestId("csat-4").click();
  await expect(app.getByTestId("csat-thanks")).toBeVisible();
  await expect(app.getByTestId("csat-prompt")).toHaveCount(0);

  // Asked once: after a reload the panel closes without asking again.
  await page.reload();
  await page.locator('button[data-helpdeck="launcher"]').click();
  const reopened = page.frameLocator(FRAME);
  await expect(reopened.getByTestId("msg-assistant").first()).toBeVisible();
  await reopened.getByRole("button", { name: "Close chat" }).click();
  await expect(page.locator(FRAME)).toBeHidden();
});

test("a rating can be skipped without sending a score", async ({ page }) => {
  await page.goto(HOST);
  await page.locator('button[data-helpdeck="launcher"]').click();

  const app = page.frameLocator(FRAME);
  await app.getByTestId("widget-input").fill("How often should I descale?");
  await app.getByTestId("widget-input").press("Enter");
  await expect(app.getByTestId("widget-input")).toBeEnabled({ timeout: 20_000 });

  await app.getByRole("button", { name: "Close chat" }).click();
  await app.getByTestId("csat-skip").click();

  // No thank-you (nothing was submitted) and no repeat ask.
  await expect(app.getByTestId("csat-thanks")).toHaveCount(0);
  await expect(app.getByTestId("csat-prompt")).toHaveCount(0);
  await app.getByRole("button", { name: "Close chat" }).click();
  await expect(page.locator(FRAME)).toBeHidden();
});
