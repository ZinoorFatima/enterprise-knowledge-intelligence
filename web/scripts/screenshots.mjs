// Regenerates the README screenshots against a running stack.
//
//   npm run screenshots
//
// Requires the web app (3000), the API (8010) and Postgres to be up, and a
// seeded corpus. Captures are scripted rather than taken by hand so they can be
// regenerated after a UI change instead of silently going stale.
//
// Note on correctness: pages are captured with a real, visible browser context.
// A hidden page does not fire requestAnimationFrame, which is exactly what
// stalls pdf.js renders -- see the PDF viewer notes in the README.
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const OUT = join(here, "..", "..", "docs", "screenshots");
const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const EMAIL = process.env.SHOT_EMAIL ?? "zinoor.test@example.com";
const PASSWORD = process.env.SHOT_PASSWORD ?? "correct-horse-battery";

mkdirSync(OUT, { recursive: true });

const shot = async (page, name, opts = {}) => {
  await page.screenshot({ path: join(OUT, `${name}.png`), ...opts });
  console.log(`  captured ${name}.png`);
};

async function main() {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2, // crisp on high-DPI displays and in GitHub's viewer
    colorScheme: "light",
  });
  // Hide the Next.js dev-mode indicator; it is build scaffolding, not product.
  await ctx.addInitScript(() => {
    const css = document.createElement("style");
    css.textContent =
      "nextjs-portal,[data-nextjs-dev-overlay],#__next-dev-overlay,[data-nextjs-toast]{display:none!important}";
    document.documentElement.appendChild(css);
  });

  const page = await ctx.newPage();

  console.log("Marketing pages");
  for (const [route, name] of [
    ["/", "landing"],
    ["/how-it-works", "how-it-works"],
    ["/evaluation", "evaluation"],
  ]) {
    await page.goto(`${BASE}${route}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(700); // let the pipeline diagram finish drawing
    await shot(page, name, { fullPage: route !== "/" });
  }

  console.log("Signing in");
  await page.goto(`${BASE}/sign-in`, { waitUntil: "networkidle" });
  await page.fill('input[name="email"]', EMAIL);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(library|ask)/, { timeout: 30_000 });

  console.log("App pages");
  await page.goto(`${BASE}/library`, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  await shot(page, "library");

  await page.goto(`${BASE}/ask`, { waitUntil: "networkidle" });
  await page.fill('input[placeholder*="liability"]', "which state court settles disagreements");
  await page.click("button:has-text('Ask')");
  // The answer streams; wait for citations, then for the PDF canvas to paint.
  await page.waitForSelector("text=Sources:", { timeout: 120_000 });
  await page.waitForFunction(
    () => {
      const c = document.querySelector("canvas");
      return c && c.width > 100;
    },
    { timeout: 120_000 },
  );
  await page.waitForTimeout(1500);
  await shot(page, "ask-citations");

  // Retrieval inspector: the two-lane table with rerank deltas.
  const retrievalTab = page.locator("button", { hasText: /^retrieval$/i }).first();
  if (await retrievalTab.count()) {
    await retrievalTab.click();
    await page.waitForTimeout(600);
    await shot(page, "retrieval-inspector");
  }

  const verificationTab = page.locator("button", { hasText: /^verification$/i }).first();
  if (await verificationTab.count()) {
    await verificationTab.click();
    await page.waitForTimeout(600);
    await shot(page, "verification");
  }

  console.log("Quality dashboard");
  await page.goto(`${BASE}/eval`, { waitUntil: "networkidle" });
  await page.click("button:has-text('Run evaluation')");
  await page.waitForSelector("text=Answer quality", { timeout: 600_000 });
  await page.waitForTimeout(800);
  await shot(page, "quality-dashboard", { fullPage: true });

  await browser.close();
  console.log(`\nWrote screenshots to docs/screenshots/`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
