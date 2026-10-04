"""Regenerate the golden dataset against the documents actually in the corpus.

    python scripts/build_golden.py

Ground-truth labels are (document_id, page) pairs, and document ids are assigned
at ingest. A hand-written dataset with placeholder ids silently scores zero on
every retrieval metric -- which looks like a broken retriever rather than a
broken label set. This resolves the ids from the database so the labels mean
something.

Page labels, not chunk ids: chunk ids die the moment the chunker changes, which
is exactly the experiment the eval exists to measure.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import asyncpg  # noqa: E402

from app.config import settings  # noqa: E402

# (question, document title, page holding the answer, ground truth, difficulty)
ITEMS = [
    (
        "q-001",
        "What is the aggregate liability cap?",
        "Acme Master Services Agreement",
        1,
        "Aggregate liability is capped at the total fees paid in the preceding twelve months.",
        "single_hop",
    ),
    (
        "q-002",
        "How much notice is needed to terminate for convenience?",
        "Acme Master Services Agreement",
        2,
        "Sixty days prior written notice.",
        "single_hop",
    ),
    (
        "q-003",
        "Which state law governs the vendor agreement?",
        "Globex Vendor Agreement",
        2,
        "Delaware, with exclusive jurisdiction in the Delaware Court of Chancery.",
        "single_hop",
    ),
    (
        "q-004",
        "What are the payment terms for a valid invoice?",
        "Globex Vendor Agreement",
        3,
        "Net thirty days, with one percent monthly interest on late amounts.",
        "single_hop",
    ),
    (
        "q-005",
        "What encryption is required for production data at rest?",
        "Information Security Policy",
        1,
        "AES-256 or stronger at rest, TLS 1.2 or above in transit.",
        "single_hop",
    ),
    (
        "q-006",
        "How quickly must a suspected security incident be reported?",
        "Information Security Policy",
        3,
        "Within seventy-two hours of discovery.",
        "single_hop",
    ),
    # Unanswerable. Without items like this, a system that answers everything
    # confidently scores well on every other metric and you ship it.
    (
        "q-007",
        "What is the chief executive's home address?",
        None,
        None,
        None,
        "no_answer",
    ),
    (
        "q-008",
        "How many employees does the vendor have in Singapore?",
        None,
        None,
        None,
        "no_answer",
    ),
]


async def main() -> int:
    conn = await asyncpg.connect(settings.asyncpg_dsn, timeout=15)
    try:
        rows = await conn.fetch(
            "SELECT id::text AS id, title, page_count FROM rag.documents "
            "WHERE NOT (tags && ARRAY['filler'])"
        )
        by_title = {r["title"]: r for r in rows}
        if not by_title:
            print("No non-filler documents found. Run scripts/seed_dev.py first.")
            return 1

        out_path = Path(__file__).resolve().parents[2] / "eval" / "golden" / "smoke.jsonl"
        lines, skipped = [], []

        for item_id, question, title, page, truth, difficulty in ITEMS:
            if title is None:
                lines.append(
                    {
                        "id": item_id,
                        "question": question,
                        "ground_truth": None,
                        "relevant_pages": [],
                        "difficulty": difficulty,
                        "expected_behavior": "refuse",
                        "reviewed": True,
                        "notes": "not present in the corpus; refusal is the correct output",
                    }
                )
                continue

            doc = by_title.get(title)
            if doc is None:
                skipped.append(f"{item_id}: no document titled {title!r}")
                continue
            if page > (doc["page_count"] or 0):
                skipped.append(f"{item_id}: {title!r} has no page {page}")
                continue

            lines.append(
                {
                    "id": item_id,
                    "question": question,
                    "ground_truth": truth,
                    "relevant_pages": [{"document_id": doc["id"], "page": page}],
                    "difficulty": difficulty,
                    "expected_behavior": "answer",
                    "reviewed": True,
                    "notes": "",
                }
            )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8"
        )

        answerable = sum(1 for x in lines if x["expected_behavior"] == "answer")
        refuse = len(lines) - answerable
        print(f"Wrote {len(lines)} items to {out_path.name}")
        print(f"  answerable: {answerable}   expected-refusal: {refuse}")
        for s in skipped:
            print(f"  SKIPPED {s}")
        if refuse / max(len(lines), 1) < 0.10:
            print("  WARNING: fewer than 10% unanswerable items; over-answering will go unmeasured.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
