"""PDF extraction and the page offset spine.

The single most important output of this module is not the text -- it is the
mapping from character offsets to page numbers. Every chunk later records a
character span in the same coordinate space, so chunk -> page is an exact
bisect rather than a guess. Without that, citations cannot be made clickable,
and the product's central claim is unsupported.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import pymupdf


@dataclass
class ExtractedPage:
    page_number: int  # 1-indexed, matching what a human reads off the page
    text: str
    char_start: int
    char_end: int
    extraction_method: str
    is_scanned: bool
    scan_reason: str = ""
    ocr_confidence: float | None = None
    width: float = 0.0
    height: float = 0.0
    rotation: int = 0
    line_boxes: list[dict] = field(default_factory=list)


@dataclass
class ExtractedDocument:
    full_text: str
    pages: list[ExtractedPage]
    page_count: int
    scanned_pages: int
    ocr_mean_confidence: float | None = None

    def page_spine(self) -> tuple[list[int], list[int]]:
        """(ascending char_starts, matching 1-indexed page numbers) for bisect."""
        return [p.char_start for p in self.pages], [p.page_number for p in self.pages]


# ───────────────────────────────────────────────── scanned detection

_WS = re.compile(r"[ \t ]+")
_NL = re.compile(r"\n{3,}")
# A hyphen at a line break that splits a word: "obliga-\ntion" -> "obligation".
_DEHYPHEN = re.compile(r"(\w)-\n(\w)")


def page_needs_ocr(page: pymupdf.Page) -> tuple[bool, str]:
    """Decide whether a page's text layer is usable.

    Two failure modes this deliberately separates:

      * A genuine scan with no text layer -- obvious, caught by char count.
      * A PDF with a BROKEN text layer (bad CID font maps) that returns
        text-shaped garbage. PyMuPDF hands that over happily, and if it is not
        caught it gets embedded and indexed, poisoning the keyword lane with
        nonsense. The alpha-ratio and mean-word-length checks catch it.

    Note the case this deliberately does NOT flag: an already-OCR'd scan with an
    invisible text layer returns real text and is correctly left alone, which
    saves re-OCR on the most common kind of enterprise scan.
    """
    text = page.get_text("text")
    stripped = text.strip()
    n_chars = len(stripped)
    n_words = len(page.get_text("words"))

    area = max(page.rect.width * page.rect.height, 1.0)
    img_area = 0.0
    try:
        for info in page.get_image_info():
            bbox = info.get("bbox")
            if bbox:
                r = pymupdf.Rect(bbox)
                img_area += abs(r.get_area())
    except Exception:
        img_area = 0.0
    img_ratio = min(img_area / area, 1.0)

    if n_chars < 50:
        return True, "no_text_layer"
    if n_words < 20 and img_ratio > 0.45:
        return True, "image_dominant"
    if (n_chars / area) < 0.0006 and img_ratio > 0.60:
        return True, "sparse_text_over_image"

    alpha = sum(1 for ch in stripped if ch.isalpha())
    if alpha / max(n_chars, 1) < 0.55:
        return True, "garbage_text_layer"

    words = stripped.split()
    if words and (sum(len(w) for w in words) / len(words)) > 18:
        # No spaces recovered from the font encoding.
        return True, "unsegmented_text"

    return False, "text_layer_ok"


# ───────────────────────────────────────────────── normalization


def normalize(text: str) -> str:
    """Normalize WITHOUT changing length semantics more than necessary.

    Every transform here runs before offsets are recorded, so page spans and
    chunk spans are computed against the normalized text and stay consistent.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _DEHYPHEN.sub(r"\1\2", text)
    text = _WS.sub(" ", text)
    text = _NL.sub("\n\n", text)
    return text.strip()


def strip_running_headers(pages: Sequence[str], threshold: float = 0.6) -> list[str]:
    """Remove lines that repeat across most pages.

    "CONFIDENTIAL - DO NOT DISTRIBUTE" on 400 pages becomes 400 chunks' worth of
    identical tokens, which then dominates the keyword lane for any query
    containing "confidential". Digits are masked so "Page 12 of 400" and
    "Page 13 of 400" count as the same line.
    """
    if len(pages) < 4:
        return list(pages)

    def key(line: str) -> str:
        return re.sub(r"\d+", "#", line.strip().lower())

    first_counts: dict[str, int] = {}
    last_counts: dict[str, int] = {}
    for p in pages:
        lines = [ln for ln in p.splitlines() if ln.strip()]
        if not lines:
            continue
        first_counts[key(lines[0])] = first_counts.get(key(lines[0]), 0) + 1
        last_counts[key(lines[-1])] = last_counts.get(key(lines[-1]), 0) + 1

    cutoff = len(pages) * threshold
    drop_first = {k for k, v in first_counts.items() if v >= cutoff and k}
    drop_last = {k for k, v in last_counts.items() if v >= cutoff and k}

    out = []
    for p in pages:
        lines = p.splitlines()
        non_empty = [i for i, ln in enumerate(lines) if ln.strip()]
        if non_empty:
            if key(lines[non_empty[0]]) in drop_first:
                lines[non_empty[0]] = ""
            if key(lines[non_empty[-1]]) in drop_last:
                lines[non_empty[-1]] = ""
        out.append("\n".join(lines))
    return out


# ───────────────────────────────────────────────── extraction


PAGE_SEPARATOR = "\n\n"


def extract_pdf(
    path: str | Path,
    *,
    ocr_engine=None,
    strip_headers: bool = True,
    max_pages: int = 2000,
) -> ExtractedDocument:
    """Extract text with exact page provenance.

    `ocr_engine`, when supplied, must expose
    `run(page) -> (text, confidence, line_boxes)`. It is injected rather than
    imported so the pipeline is testable without the OCR dependency installed.
    """
    doc = pymupdf.open(str(path))
    try:
        if doc.is_encrypted and not doc.authenticate(""):
            raise ValueError("PDF is password protected")
        if doc.page_count > max_pages:
            raise ValueError(f"PDF has {doc.page_count} pages; limit is {max_pages}")

        raw_texts: list[str] = []
        meta: list[dict] = []

        for page in doc:
            needs_ocr, reason = page_needs_ocr(page)
            method = "pymupdf"
            confidence = None
            boxes: list[dict] = []
            text = page.get_text("text")

            if needs_ocr and ocr_engine is not None:
                try:
                    text, confidence, boxes = ocr_engine.run(page)
                    method = f"ocr_{ocr_engine.name}"
                except Exception:
                    # A failed OCR must not lose the page; keep whatever the text
                    # layer gave us and record that it is unreliable.
                    reason = f"{reason}+ocr_failed"

            raw_texts.append(text)
            meta.append(
                {
                    "needs_ocr": needs_ocr,
                    "reason": reason,
                    "method": method,
                    "confidence": confidence,
                    "boxes": boxes,
                    "width": page.rect.width,
                    "height": page.rect.height,
                    "rotation": page.rotation,
                }
            )

        if strip_headers:
            raw_texts = strip_running_headers(raw_texts)

        # Build full_text and record each page's span in the SAME coordinates.
        pages: list[ExtractedPage] = []
        parts: list[str] = []
        cursor = 0
        for i, (raw, m) in enumerate(zip(raw_texts, meta), start=1):
            norm = normalize(raw)
            start = cursor
            parts.append(norm)
            cursor += len(norm)
            end = cursor
            if i < len(raw_texts):
                parts.append(PAGE_SEPARATOR)
                cursor += len(PAGE_SEPARATOR)
            pages.append(
                ExtractedPage(
                    page_number=i,
                    text=norm,
                    char_start=start,
                    char_end=end,
                    extraction_method=m["method"],
                    is_scanned=bool(m["needs_ocr"]),
                    scan_reason=m["reason"],
                    ocr_confidence=m["confidence"],
                    width=m["width"],
                    height=m["height"],
                    rotation=m["rotation"],
                    line_boxes=m["boxes"],
                )
            )

        confidences = [p.ocr_confidence for p in pages if p.ocr_confidence is not None]
        return ExtractedDocument(
            full_text="".join(parts),
            pages=pages,
            page_count=len(pages),
            scanned_pages=sum(1 for p in pages if p.is_scanned),
            ocr_mean_confidence=(sum(confidences) / len(confidences)) if confidences else None,
        )
    finally:
        doc.close()


def split_sentences(full_text: str, pages: Sequence[ExtractedPage]) -> list:
    """Sentence-split with offsets into full_text.

    Splitting per page and offsetting avoids sentences that straddle a page
    break being given a span that crosses the separator.
    """
    from app.ingest.chunker import Sentence

    try:
        import pysbd

        seg = pysbd.Segmenter(language="en", clean=False, char_span=True)
        use_pysbd = True
    except Exception:
        seg = None
        use_pysbd = False

    out: list[Sentence] = []
    for page in pages:
        if not page.text.strip():
            continue
        if use_pysbd:
            try:
                for s in seg.segment(page.text):
                    txt = s.sent.strip()
                    if txt:
                        out.append(
                            Sentence(txt, page.char_start + s.start, page.char_start + s.start + len(s.sent.rstrip()))
                        )
                continue
            except Exception:
                pass
        # Fallback: regex split, still with exact offsets.
        pos = 0
        for m in re.finditer(r"[^.!?\n]+[.!?]+|\S[^\n]*", page.text):
            txt = m.group().strip()
            if txt:
                out.append(Sentence(txt, page.char_start + m.start(), page.char_start + m.end()))
            pos = m.end()
        _ = pos
    return out
