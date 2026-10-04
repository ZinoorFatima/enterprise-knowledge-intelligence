"""Seed a dev corpus through the REAL ingestion pipeline.

    python scripts/seed_dev.py

Deliberately not raw INSERTs: going through ingest_pdf() means the seeded rows
have genuine page spines, genuine chunk offsets and genuine tsvectors, so the
query-plan tests and the retrieval smoke tests are measuring the same thing
production would produce.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402
import pymupdf  # noqa: E402

from app.config import settings  # noqa: E402
from app.ingest.pipeline import HashEmbedder, ingest_pdf  # noqa: E402

# Each clause is a distinct topic. Deliberately phrased so that the obvious
# question about it shares few or no keywords with it -- that is what makes the
# vector lane's contribution measurable rather than assumed.
DOCS = [
    (
        "Acme Master Services Agreement",
        ["contracts"],
        [
            "Aggregate liability of either party under this agreement shall not exceed the total "
            "fees paid during the twelve months immediately preceding the event giving rise to "
            "the claim. This ceiling applies in aggregate across all claims, whether framed in "
            "contract, tort, or otherwise.",
            "Either party may terminate this agreement for convenience upon sixty days prior "
            "written notice delivered to the other party at the address stated in the preamble. "
            "No reason need be given and no termination fee is payable.",
            "Termination for cause requires thirty days written notice specifying the breach, "
            "together with a reasonable opportunity to cure. If the breach is cured within that "
            "window the notice lapses automatically.",
            "Indemnification obligations, together with the confidentiality covenants in Section "
            "9, survive expiry or termination of this agreement indefinitely and bind each "
            "party's successors and assigns.",
            "Clause 12.2 governs limitation of liability. Nothing in this agreement limits "
            "liability for fraud, wilful misconduct, or death or personal injury caused by "
            "negligence.",
            "Intellectual property created by the supplier in the course of performing the "
            "services vests in the customer upon payment. Pre-existing materials remain the "
            "property of the party that owned them before the effective date.",
            "The supplier shall maintain professional indemnity insurance of not less than five "
            "million dollars for the duration of the term and for six years thereafter.",
            "Any dispute shall first be escalated to the executive sponsors of each party before "
            "either party commences proceedings. Escalation must complete within twenty business "
            "days.",
        ],
    ),
    (
        "Globex Vendor Agreement",
        ["contracts", "vendor"],
        [
            "Indemnity under this vendor agreement is capped at the total contract value "
            "calculated over the entire term, rather than on an annual basis.",
            "This agreement and any non-contractual obligations arising out of it are governed "
            "by the laws of the State of Delaware, and the parties submit to the exclusive "
            "jurisdiction of the Delaware Court of Chancery.",
            "Payment terms are net thirty days from receipt of a valid and undisputed invoice. "
            "Late amounts accrue interest at one percent per month.",
            "Upon termination the vendor shall return or irreversibly destroy all confidential "
            "information and certify that destruction in writing within fourteen days.",
            "The vendor may not subcontract any part of the services without the customer's "
            "prior written consent, which shall not be unreasonably withheld.",
            "Service credits are the customer's sole remedy for failure to meet the availability "
            "target of 99.9 percent measured monthly.",
        ],
    ),
    (
        "Information Security Policy",
        ["policy"],
        [
            "All production data must be encrypted at rest using AES-256 or stronger, and in "
            "transit using TLS 1.2 or above. Keys are rotated annually.",
            "Access reviews are performed quarterly by the security team. Any account inactive "
            "for ninety days is disabled automatically.",
            "Suspected incidents must be reported to the security team within seventy-two hours "
            "of discovery, and affected customers notified without undue delay.",
            "Multi-factor authentication is mandatory for all administrative access to "
            "production systems, with hardware tokens required for privileged roles.",
            "Backups are taken nightly, retained for thirty-five days, and restore procedures "
            "are tested at least twice per year.",
            "Laptops issued to staff must have full-disk encryption enabled and screen lock "
            "after five minutes of inactivity.",
        ],
    ),
]


def build_pdf(path: Path, title: str, clauses: list[str], pages: int | None = None) -> Path:
    """One substantive clause per page, with only light connective filler.

    This matters more than it looks. An earlier version repeated the same
    boilerplate paragraph three times per page, so each distinctive clause was
    one sentence inside ~400 tokens of identical text. The result: 42 chunks
    produced only 27 distinct embeddings and a total cosine spread of 0.10 --
    every chunk was effectively equidistant from every query, and vector search
    could not discriminate at all. The embedder was fine; the corpus was not.

    A corpus used to exercise retrieval must have a signal-to-boilerplate ratio
    a retriever could plausibly work with.
    """
    pages = pages or len(clauses)
    doc = pymupdf.open()
    for i, clause in enumerate(clauses[:pages], start=1):
        page = doc.new_page()
        page.insert_text((72, 60), "CONFIDENTIAL - INTERNAL USE ONLY", fontsize=9)
        body = f"Section {i}. {clause}"
        page.insert_textbox(pymupdf.Rect(72, 90, 523, 720), body, fontsize=11)
        page.insert_text((72, 760), f"{title} - Page {i} of {pages}", fontsize=8)
    doc.save(str(path))
    doc.close()
    return path


async def main() -> int:
    conn = await asyncpg.connect(settings.asyncpg_dsn, timeout=10)
    try:
        org = await conn.fetchval("SELECT id FROM auth.organizations LIMIT 1")
        owner = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
        if not org or not owner:
            print("No user/org found. Sign up in the web app first (http://localhost:3000/sign-up).")
            return 1

        out_dir = Path(__file__).resolve().parents[2] / "storage" / "seed"
        out_dir.mkdir(parents=True, exist_ok=True)

        # --bulk N adds N filler documents so the corpus is large enough that the
        # planner prefers index scans; below ~500 chunks a sequential scan is
        # genuinely cheaper and the plan assertions would be meaningless.
        bulk = 0
        if "--bulk" in sys.argv:
            bulk = int(sys.argv[sys.argv.index("--bulk") + 1])

        docs = list(DOCS)
        for n in range(bulk):
            docs.append(
                (
                    f"Filler Agreement {n:03d}",
                    ["filler"],
                    [
                        f"Schedule {n}.{k} sets out the delivery milestones and "
                        f"acceptance criteria for workstream {k}."
                        for k in range(1, 9)
                    ],
                )
            )

        total_chunks = 0
        for title, tags, clauses in docs:
            pdf = build_pdf(out_dir / f"{title.replace(' ', '_')}.pdf", title, clauses)
            res = await ingest_pdf(
                conn=conn,
                path=pdf,
                org_id=str(org),
                owner_id=str(owner),
                title=title,
                tags=tags,
                embedder=HashEmbedder(settings.embed_dim),
            )
            total_chunks += res.chunk_count
            state = "deduplicated" if res.deduplicated else f"{res.chunk_count} chunks"
            print(f"  {title:<36} {res.page_count:>3} pages  {state}")

        await conn.execute("ANALYZE rag.chunks")
        n = await conn.fetchval("SELECT count(*) FROM rag.chunks")
        print(f"\nCorpus now holds {n} chunks ({total_chunks} added this run).")
        if n < 500:
            print(
                "Note: the query-plan tests need >= 500 chunks for the planner to "
                "prefer indexes over a sequential scan; they will skip below that."
            )
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
