export const metadata = {
  title: "Privacy",
  description: "What is stored, and what leaves the deployment.",
};

export default function PrivacyPage() {
  return (
    <div className="mx-auto max-w-[76ch] px-6 py-20">
      <p className="text-overline text-muted-foreground">Legal</p>
      <h1 className="text-display-2 mt-3 text-foreground-strong">Privacy</h1>
      <p className="text-ui-sm mt-3 text-subtle-foreground">
        Template language for a self-hosted deployment. Have counsel review it
        before relying on it.
      </p>

      <div className="mt-10 space-y-8">
        <section>
          <h2 className="text-title text-foreground-strong">
            What leaves your deployment
          </h2>
          <p className="text-body mt-2 text-muted-foreground">
            Parsing, OCR, embedding and reranking all run locally. No document
            text is sent to an embedding provider. The only outbound content is
            the assembled context for a question, sent to your configured
            language model &mdash; and with{" "}
            <code className="font-mono">LLM_MODE=offline</code> even that does
            not happen.
          </p>
        </section>

        <section>
          <h2 className="text-title text-foreground-strong">What is stored</h2>
          <ul className="text-body mt-2 space-y-1.5 text-muted-foreground">
            <li>
              Account email, a hashed password (argon2id) and organisation
              membership.
            </li>
            <li>
              Uploaded documents, their extracted text, page records and chunks.
            </li>
            <li>
              Questions, the passages retrieved for each, answers and their
              citations &mdash; retained so any answer can be reconstructed and
              audited later.
            </li>
          </ul>
        </section>

        <section>
          <h2 className="text-title text-foreground-strong">Deletion</h2>
          <p className="text-body mt-2 text-muted-foreground">
            Deleting a document cascades to its pages, chunks and citations in a
            single transaction. Deletion is a foreign-key cascade you can verify
            in SQL, not a background job you have to take on trust.
          </p>
        </section>

        <section>
          <h2 className="text-title text-foreground-strong">Access</h2>
          <p className="text-body mt-2 text-muted-foreground">
            Every query is scoped to an organisation in SQL, not only in
            application code. Membership is re-checked against the database on
            each request rather than trusted from a session token, so removing
            someone takes effect immediately rather than when their session
            expires.
          </p>
        </section>
      </div>
    </div>
  );
}
