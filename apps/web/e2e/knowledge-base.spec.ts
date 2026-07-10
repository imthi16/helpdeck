import path from "node:path";

import { expect, test } from "@playwright/test";

import { signUp } from "./helpers";

const PDF_FIXTURE = path.resolve(__dirname, "../../api/tests/fixtures/sample.pdf");

test("upload a PDF, wait for ready, then delete it", async ({ page }) => {
  await signUp(page, "kb");
  await page.goto("/dashboard/knowledge-base");

  await page.getByTestId("file-input").setInputFiles(PDF_FIXTURE);

  // A row appears for the uploaded document.
  const row = page.getByTestId("doc-row").filter({ hasText: "sample" });
  await expect(row).toBeVisible();

  // Status auto-refreshes to "ready" once the worker ingests it.
  await expect(row.getByTestId("doc-status")).toHaveText("ready", { timeout: 20_000 });

  // Chunk count is positive.
  const chunks = await row.getByTestId("doc-chunks").textContent();
  expect(Number(chunks)).toBeGreaterThan(0);

  // Delete via the confirm dialog.
  await row.getByTestId("doc-delete").click();
  await page.getByTestId("confirm-delete").click();

  await expect(page.getByTestId("doc-row").filter({ hasText: "sample" })).toHaveCount(0);
});

test("add a raw text document", async ({ page }) => {
  await signUp(page, "kb");
  await page.goto("/dashboard/knowledge-base");

  await page.getByRole("tab", { name: "Paste text" }).click();
  await page.getByLabel("Title").fill("Refund Policy");
  await page.getByLabel("Content").fill("# Refunds\n\nWe refund within 30 days of delivery.");
  await page.getByRole("button", { name: "Add text" }).click();

  const row = page.getByTestId("doc-row").filter({ hasText: "Refund Policy" });
  await expect(row).toBeVisible();
  await expect(row.getByTestId("doc-status")).toHaveText("ready", { timeout: 20_000 });
});
