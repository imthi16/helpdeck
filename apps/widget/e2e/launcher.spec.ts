import { expect, test } from "@playwright/test";

const DEMO = "/examples/demo.html";

test("launcher bubble toggles the chat iframe", async ({ page }) => {
  await page.goto(DEMO);

  const launcher = page.locator('button[data-helpdeck="launcher"]');
  await expect(launcher).toBeVisible();

  const frame = page.locator('iframe[data-helpdeck="frame"]');
  // Iframe exists but is hidden until opened.
  await expect(frame).toBeHidden();

  await launcher.click();
  await expect(frame).toBeVisible();

  // The iframe app renders its own UI (default welcome), isolated from the host.
  const app = page.frameLocator('iframe[data-helpdeck="frame"]');
  await expect(app.getByTestId("widget-welcome")).toBeVisible();

  // Close from inside the iframe (postMessage to parent).
  await app.getByRole("button", { name: "Close chat" }).click();
  await expect(frame).toBeHidden();
});

test("host page styles do not leak into the widget iframe", async ({ page }) => {
  await page.goto(DEMO);
  await page.locator('button[data-helpdeck="launcher"]').click();

  const app = page.frameLocator('iframe[data-helpdeck="frame"]');
  const title = app.getByTestId("widget-title");
  await expect(title).toBeVisible();

  // The host forces Comic Sans on everything; the iframe must use its own font.
  const fontFamily = await title.evaluate((el) => getComputedStyle(el).fontFamily);
  expect(fontFamily.toLowerCase()).not.toContain("comic sans");
});

test("launcher position and color come from data attributes", async ({ page }) => {
  await page.goto(DEMO);
  const launcher = page.locator('button[data-helpdeck="launcher"]');
  const styles = await launcher.evaluate((el) => {
    const s = getComputedStyle(el);
    return { right: s.right, background: s.backgroundColor };
  });
  // data-position="bottom-right" -> anchored to the right edge.
  expect(styles.right).toBe("20px");
  // data-color="#0d9488" -> rgb(13, 148, 136).
  expect(styles.background).toBe("rgb(13, 148, 136)");
});
