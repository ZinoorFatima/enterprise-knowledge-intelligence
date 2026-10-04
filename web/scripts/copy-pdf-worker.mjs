// Copies the pdf.js worker into public/ so it can be served as a static asset.
//
// The worker must match the pdfjs-dist version the app bundles. Vendoring a
// copy in git invites silent drift on the next dependency bump, and a
// mismatched worker fails by hanging rather than erroring -- so it is generated
// from node_modules on install, dev and build instead.
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const src = join(root, "node_modules", "pdfjs-dist", "build", "pdf.worker.min.mjs");
const destDir = join(root, "public");
const dest = join(destDir, "pdf.worker.min.mjs");

if (!existsSync(src)) {
  console.warn("[copy-pdf-worker] pdfjs-dist not installed yet; skipping.");
  process.exit(0);
}
mkdirSync(destDir, { recursive: true });
copyFileSync(src, dest);
console.log("[copy-pdf-worker] public/pdf.worker.min.mjs updated");
