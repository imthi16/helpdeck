import { expect, test } from "@playwright/test";

import { signUpAndSeedText } from "./helpers";

test("an escalated chat appears in the inbox and can be resolved", async ({ page }) => {
  await signUpAndSeedText(page, "Descaling Guide", "# Descaling\n\nDescale every three months.", "conv");

  // Trigger an escalation with an out-of-KB question.
  await page.goto("/dashboard/playground");
  await page.getByTestId("chat-input").fill("What is your CEO's shoe size?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("assistant-message")).toBeVisible();
  // Wait for the turn to finish (input re-enabled).
  await expect(page.getByTestId("chat-input")).toBeEnabled({ timeout: 20_000 });

  // It shows up under Escalated.
  await page.goto("/dashboard/conversations");
  await page.getByRole("tab", { name: "Escalated" }).click();
  const row = page.getByTestId("conversation-row").first();
  await expect(row).toBeVisible({ timeout: 10_000 });

  // Open the transcript and resolve it.
  await row.click();
  await expect(page.getByTestId("transcript-status")).toHaveText("escalated");
  await page.getByTestId("resolve").click();
  await expect(page.getByTestId("transcript-status")).toHaveText("closed");

  // Internal reply is appended to the transcript.
  await page.getByTestId("reply-input").fill("Following up by email.");
  await page.getByRole("button", { name: "Reply" }).click();
  await expect(page.getByTestId("transcript")).toContainText("Following up by email.");
});

test("visitor feedback and a CSAT rating show up in the transcript", async ({ page }) => {
  await signUpAndSeedText(
    page,
    "Descaling Guide",
    "# Descaling\n\nDescale the machine every three months with normal use.",
    "feedback",
  );

  // A real answered turn to vote on.
  await page.goto("/dashboard/playground");
  await page.getByTestId("chat-input").fill("How often should I descale?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("chat-input")).toBeEnabled({ timeout: 20_000 });

  // Record a thumbs-down and a CSAT score the way the widget does — through the
  // public key, which the dashboard now exposes for real (5.3).
  await page.evaluate(async (api) => {
    const key = await (
      await fetch(`${api}/api/v1/keys/widget`, { credentials: "include" })
    ).json();
    const list = await (
      await fetch(`${api}/api/v1/conversations`, { credentials: "include" })
    ).json();
    const detail = await (
      await fetch(`${api}/api/v1/conversations/${list[0].id}`, { credentials: "include" })
    ).json();
    const assistant = detail.messages.find((m: { role: string }) => m.role === "assistant");

    await fetch(`${api}/api/v1/widget/feedback`, {
      method: "POST",
      headers: { "X-Public-Key": key.value, "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: assistant.id, rating: -1 }),
    });
    await fetch(`${api}/api/v1/widget/csat`, {
      method: "POST",
      headers: { "X-Public-Key": key.value, "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: list[0].id, score: 4 }),
    });
  }, "http://localhost:8020");

  await page.goto("/dashboard/conversations");
  await page.getByTestId("conversation-row").first().click();

  await expect(page.getByTestId("message-feedback")).toHaveText("👎");
  await expect(page.getByTestId("transcript-csat")).toHaveText("Rated 4/5");
});
