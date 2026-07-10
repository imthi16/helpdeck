import { expect, test } from "@playwright/test";

const HOST = "/examples/e2e.html";
const FRAME = 'iframe[data-helpdeck="frame"]';

test("full widget conversation: cited answer, source popover, thumbs, human handoff", async ({
  page,
}) => {
  await page.goto(HOST);
  await page.locator('button[data-helpdeck="launcher"]').click();

  const app = page.frameLocator(FRAME);
  await expect(app.getByTestId("widget-welcome")).toBeVisible();

  // Ask a seeded question -> streamed, cited answer.
  await app.getByTestId("widget-input").fill("How often should I descale?");
  await app.getByTestId("widget-input").press("Enter");

  const assistant = app.getByTestId("msg-assistant").first();
  await expect(assistant).toBeVisible();
  const chip = assistant.getByTestId("citation-chip").first();
  await expect(chip).toBeVisible({ timeout: 20_000 });

  // Clicking a citation opens the source popover.
  await chip.click();
  await expect(app.getByTestId("source-popover")).toBeVisible();

  // Thumbs up is recorded (button becomes active).
  await app.getByTestId("thumbs-up").first().click();
  await expect(app.getByTestId("thumbs-up").first()).toHaveClass(/voted/);
});

test("talk to a human escalates and shows a handoff message", async ({ page }) => {
  await page.goto(HOST);
  await page.locator('button[data-helpdeck="launcher"]').click();

  const app = page.frameLocator(FRAME);
  await expect(app.getByTestId("widget-welcome")).toBeVisible();
  await app.getByTestId("talk-to-human").click();

  await expect(app.getByTestId("handoff")).toBeVisible({ timeout: 20_000 });
});

test("conversation persists across a page reload", async ({ page }) => {
  await page.goto(HOST);
  await page.locator('button[data-helpdeck="launcher"]').click();

  const app = page.frameLocator(FRAME);
  await app.getByTestId("widget-input").fill("What is the free shipping threshold?");
  await app.getByTestId("widget-input").press("Enter");
  await expect(app.getByTestId("msg-assistant").first()).toBeVisible();
  await expect(app.getByTestId("citation-chip").first()).toBeVisible({ timeout: 20_000 });

  // Reload the whole host page; the widget restores the transcript from storage.
  await page.reload();
  await page.locator('button[data-helpdeck="launcher"]').click();
  const reopened = page.frameLocator(FRAME);
  await expect(reopened.getByTestId("msg-user").first()).toContainText(
    "free shipping threshold",
  );
  await expect(reopened.getByTestId("msg-assistant").first()).toBeVisible();
});
