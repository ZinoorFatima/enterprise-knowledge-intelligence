-- Cross-schema ownership FKs.
--
-- Separate from 0001 because it must run AFTER the Drizzle auth migration has
-- created auth.organizations / auth.users. Keeping them apart lets the
-- ingestion pipeline be built and tested before the web app exists.

ALTER TABLE rag.documents
  DROP CONSTRAINT IF EXISTS documents_org_fk,
  ADD CONSTRAINT documents_org_fk
    FOREIGN KEY (org_id) REFERENCES auth.organizations(id) ON DELETE CASCADE;

ALTER TABLE rag.documents
  DROP CONSTRAINT IF EXISTS documents_owner_fk,
  ADD CONSTRAINT documents_owner_fk
    FOREIGN KEY (owner_id) REFERENCES auth.users(id) ON DELETE RESTRICT;

ALTER TABLE rag.threads
  DROP CONSTRAINT IF EXISTS threads_org_fk,
  ADD CONSTRAINT threads_org_fk
    FOREIGN KEY (org_id) REFERENCES auth.organizations(id) ON DELETE CASCADE;
