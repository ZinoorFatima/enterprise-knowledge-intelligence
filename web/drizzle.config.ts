import { defineConfig } from "drizzle-kit";
import { config } from "dotenv";

// The repo-root .env is shared with the Python service, so both halves of the
// system read one connection string.
config({ path: "../.env" });

export default defineConfig({
  schema: "./lib/db/schema.ts",
  out: "./drizzle",
  dialect: "postgresql",
  dbCredentials: { url: process.env.DATABASE_URL! },
  // Drizzle owns `auth` and must never touch `rag`, which Alembic owns.
  schemaFilter: ["auth"],
  verbose: true,
  strict: true,
});
