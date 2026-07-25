import { expect, test } from "@playwright/test";

import { signUp, uniqueEmail } from "./helpers";

test("invite a viewer, accept the link, and land in a read-only dashboard", async ({
  page,
  browser,
}) => {
  await signUp(page, "members");
  await page.goto("/dashboard/settings");
  await page.getByRole("tab", { name: "Members" }).click();

  // The owner is listed and marked as themselves.
  await expect(page.getByTestId("member-row")).toHaveCount(1);

  const inviteeEmail = uniqueEmail("invitee");
  await page.getByTestId("invite-email").fill(inviteeEmail);
  await page.getByTestId("invite-role").selectOption("viewer");
  await page.getByTestId("invite-submit").click();

  // The link is shown once, and the invite appears as pending.
  const link = await page.getByTestId("invite-link").textContent();
  expect(link).toContain("/invite/");
  await expect(page.getByTestId("invite-row").filter({ hasText: inviteeEmail })).toBeVisible();

  // Redeem it as a brand-new person, in a separate browser context.
  const inviteeContext = await browser.newContext();
  const inviteePage = await inviteeContext.newPage();
  try {
    await inviteePage.goto(link!.trim());
    await expect(inviteePage.getByText(inviteeEmail)).toBeVisible();
    await inviteePage.getByLabel("Choose a password").fill("supersecret1");
    await inviteePage.getByTestId("accept-invite").click();
    await expect(inviteePage).toHaveURL(/\/dashboard$/);

    // A viewer sees the knowledge base but cannot add to it.
    await inviteePage.goto("/dashboard/knowledge-base");
    await expect(
      inviteePage.getByText("Only owners and admins can add or remove sources."),
    ).toBeVisible();
    await inviteePage.getByRole("tab", { name: "Paste text" }).click();
    await expect(inviteePage.getByRole("button", { name: "Add text" })).toBeDisabled();

    // And the members controls are disabled for them.
    await inviteePage.goto("/dashboard/settings");
    await inviteePage.getByRole("tab", { name: "Members" }).click();
    await expect(inviteePage.getByTestId("invite-submit")).toBeDisabled();
    await expect(inviteePage.getByTestId("member-remove").first()).toBeDisabled();
  } finally {
    await inviteeContext.close();
  }

  // Back on the owner's page: the invite is redeemed and the member is listed.
  await page.reload();
  await page.getByRole("tab", { name: "Members" }).click();
  await expect(page.getByTestId("member-row")).toHaveCount(2);
  await expect(page.getByTestId("invite-row")).toHaveCount(0);

  // The owner can promote them, then remove them.
  const row = page.getByTestId("member-row").filter({ hasText: inviteeEmail });
  await row.getByTestId("member-role").selectOption("agent");
  await expect(row.getByTestId("member-role")).toHaveValue("agent");

  await row.getByTestId("member-remove").click();
  await page.getByTestId("confirm-remove").click();
  await expect(page.getByTestId("member-row")).toHaveCount(1);
});

test("the last owner cannot demote themselves", async ({ page }) => {
  await signUp(page, "lastowner");
  await page.goto("/dashboard/settings");
  await page.getByRole("tab", { name: "Members" }).click();

  const row = page.getByTestId("member-row").first();
  await row.getByTestId("member-role").selectOption("admin");

  await expect(page.getByTestId("members-error")).toContainText("at least one owner");
  // The role is reverted by the refresh that follows the failed change.
  await expect(row.getByTestId("member-role")).toHaveValue("owner");
});
