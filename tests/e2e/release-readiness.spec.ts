import { expect, test } from "playwright/test";

test("fixture release moves from needs decision to ready", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Use demo repository" }).click();
  await page.getByLabel("Milestone").selectOption("7");
  await page
    .getByLabel("Release candidate")
    .selectOption("release/2026-08-10");
  await page.getByRole("button", { name: "Run analysis" }).click();

  await expect(
    page.locator('header[data-status="NEEDS_DECISION"]'),
  ).toContainText("NEEDS DECISION");
  await expect(page.getByRole("link", { name: "Open evidence" })).toHaveAttribute(
    "href",
    /^https:\/\/github\.com\/floppy522\/ai-release-intelligence-demo\/runs\/7001$/,
  );
  await page.getByRole("button", { name: "Accept risk" }).click();
  await page
    .getByLabel("Reason")
    .fill("Known flaky advisory test; blocking suite is green.");
  await page.getByLabel(/confirm this human decision/i).check();
  await page.getByRole("button", { name: "Record decision" }).click();

  await expect(page.locator('header[data-status="READY"]')).toContainText(
    "READY",
  );
  await expect(page.getByRole("link", { name: "Open evidence" })).toHaveCount(0);
  await expect(
    page
      .getByRole("region", { name: "What requires attention" })
      .getByText("No findings require attention for this snapshot.", {
        exact: true,
      }),
  ).toBeVisible();

  await page.goto("/");
  await page.getByRole("button", { name: "Use demo repository" }).click();
  await page.getByRole("button", { name: "Run analysis" }).click();
  await expect(
    page.locator('header[data-status="NEEDS_DECISION"]'),
  ).toContainText("NEEDS DECISION");
});
