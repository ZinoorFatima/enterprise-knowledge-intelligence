// Copies the pdf.js runtime assets into public/ so they can be served statically.
//
// Three things are needed, not just the worker:
//
//   build/pdf.worker.min.mjs -> the worker itself
//   standard_fonts/          -> metrics for the standard-14 fonts (Helvetica,
//                               Times, Courier). A PDF that references these
//                               without embedding them -- which most generators
//                               produce -- cannot be rendered without them, and
//                               pdf.js HANGS rather than erroring when the
//                               fetch fails.
//   cmaps/                   -> character maps for CID/CJK encodings.
//
// Generated from node_modules rather than vendored in git, so they can never
// drift from the pdfjs-dist version the app bundles. A mismatched worker also
// fails by hanging, which is extremely hard to diagnose.
import { cpSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const pkg = join(root, "node_modules", "pdfjs-dist");
const publicDir = join(root, "public");

if (!existsSync(pkg)) {
  console.warn("[copy-pdf-assets] pdfjs-dist not installed yet; skipping.");
  process.exit(0);
}

mkdirSync(publicDir, { recursive: true });

const jobs = [
  { from: join(pkg, "build", "pdf.worker.min.mjs"), to: join(publicDir, "pdf.worker.min.mjs") },
  { from: join(pkg, "standard_fonts"), to: join(publicDir, "standard_fonts"), dir: true },
  { from: join(pkg, "cmaps"), to: join(publicDir, "cmaps"), dir: true },
];

for (const { from, to, dir } of jobs) {
  if (!existsSync(from)) {
    console.warn(`[copy-pdf-assets] missing ${from}; skipping.`);
    continue;
  }
  cpSync(from, to, { recursive: !!dir });
  const n = dir ? `${readdirSync(to).length} files` : "ok";
  console.log(`[copy-pdf-assets] public/${to.slice(publicDir.length + 1)} (${n})`);
}
