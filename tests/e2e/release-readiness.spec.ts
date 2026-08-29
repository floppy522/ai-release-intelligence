import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test, type Page } from "playwright/test";

const capturePortfolioScreenshots =
  process.env.CAPTURE_PORTFOLIO_SCREENSHOTS === "1";
const portfolioScreenshotDirectory = path.join("test-results", "portfolio");

async function capturePortfolioScreenshot(page: Page, filename: string) {
  if (!capturePortfolioScreenshots) {
    return;
  }

  await page.screenshot({
    path: path.join(portfolioScreenshotDirectory, filename),
  });
}

test("fixture release moves from needs decision to ready", async ({ page }) => {
  if (capturePortfolioScreenshots) {
    await page.setViewportSize({ width: 1440, height: 1024 });
    await mkdir(portfolioScreenshotDirectory, { recursive: true });
  }

  await page.goto("/");
  await page.getByRole("button", { name: "Use demo repository" }).click();
  await page.getByLabel("Milestone").selectOption("7");
  await page
    .getByLabel("Release candidate")
    .selectOption("release/2026-08-10");
  await capturePortfolioScreenshot(page, "01-release-setup.png");
  await page.getByRole("button", { name: "Run analysis" }).click();

  await expect(
    page.locator('header[data-status="NEEDS_DECISION"]'),
  ).toContainText("NEEDS DECISION");
  const evidenceLink = page.getByRole("link", { name: "Open evidence" });
  await expect(evidenceLink).toBeVisible();
  await expect(evidenceLink).toHaveAttribute(
    "href",
    /^https:\/\/github\.com\/floppy522\/ai-release-intelligence-demo\/runs\/7001$/,
  );
  await capturePortfolioScreenshot(page, "02-needs-decision.png");
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
  await capturePortfolioScreenshot(page, "03-ready-after-decision.png");

  await page.goto("/");
  await page.getByRole("button", { name: "Use demo repository" }).click();
  await page.getByRole("button", { name: "Run analysis" }).click();
  await expect(
    page.locator('header[data-status="NEEDS_DECISION"]'),
  ).toContainText("NEEDS DECISION");
});
