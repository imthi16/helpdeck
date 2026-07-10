import { expect, test } from "@playwright/test";

test("loads with async script and exposes only window.HelpDeck", async ({ page }) => {
  await page.goto("/examples/async.html");

  // Launcher still boots under async loading.
  const launcher = page.locator('button[data-helpdeck="launcher"]');
  await expect(launcher).toBeVisible();

  const api = await page.evaluate(() => {
    const hd = (window as unknown as { HelpDeck?: Record<string, unknown> }).HelpDeck;
    return hd ? Object.keys(hd).sort() : null;
  });
  expect(api).toEqual(["close", "open", "toggle"]);

  // No internal identifiers leaked onto window.
  const leaked = await page.evaluate(() =>
    ["boot", "readConfig", "config", "launcher", "iframe", "assign"].filter(
      (name) => name in window,
    ),
  );
  expect(leaked).toEqual([]);
});

test("iframe app is not fetched until the widget is first opened", async ({ page }) => {
  await page.goto("/examples/demo.html");

  const frame = page.locator('iframe[data-helpdeck="frame"]');
  // Before opening, the iframe has no app URL loaded (lazy).
  const srcBefore = await frame.getAttribute("src");
  expect(srcBefore).toBeFalsy();

  await page.locator('button[data-helpdeck="launcher"]').click();
  const srcAfter = await frame.getAttribute("src");
  expect(srcAfter).toContain("app/index.html");

  // window.HelpDeck programmatic API works.
  await page.evaluate(() => (window as unknown as { HelpDeck: { close: () => void } }).HelpDeck.close());
  await expect(frame).toBeHidden();
});
