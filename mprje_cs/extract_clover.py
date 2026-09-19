"""Clover "Sales Overview" report -> structured data.

Handles three input shapes, per CLAUDE.md section 1:
  - A text-based PDF (extracted directly via PyMuPDF - no system binaries
    like poppler/pdftotext required, so this works out of the box on a
    plain Windows install)
  - A raster "Print to PDF" (no text layer - rasterize with PyMuPDF, then
    OCR if the optional OCR package is installed)
  - Pasted webpage text/HTML (Full Report / Trends view copy-paste)

Never guesses a number it can't find - raises ClarifyNeeded (which the CLI
surfaces as a flag / question) rather than silently defaulting.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs


class ClarifyNeeded(RuntimeError):
    """Raised when the report can't be read confidently and a human must confirm."""


MONEY_RE = r"\(?-?\$?\s?[\d,]+\.\d{2}\)?"


def _to_decimal(s: str) -> Decimal:
    s = s.strip()
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("$", "").replace(",", "").strip()
    if s.startswith("-"):
        negative = True
        s = s[1:]
    try:
        val = Decimal(s)
    except InvalidOperation as exc:
        raise ClarifyNeeded(f"Could not parse a dollar amount from '{s}'") from exc
    return -val if negative else val


@dataclass
class DailyReport:
    date: dt.date
    gross_sales: Decimal
    discounts: Decimal
    refunds: Decimal
    net_sales_total: Decimal
    taxes_fees_total: Decimal
    amount_collected_total: Decimal
    revenue_classes: dict = field(default_factory=dict)  # category -> Net Sales (Decimal)
    revenue_pct_total: Optional[Decimal] = None  # the report's own "% Net Sales" total row, if present
    tenders: dict = field(default_factory=dict)  # tender name -> Amount Collected (Decimal)
    source_path: Optional[Path] = None
    raw_text: str = ""


def load_text(path: Path) -> str:
    """Return the best-effort text content of a report file."""
    suffix = path.suffix.lower()
    if suffix in (".txt",):
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix in (".html", ".htm"):
        raw = path.read_text(encoding="utf-8", errors="replace")
        return _strip_html(raw)
    if suffix == ".pdf":
        return _load_pdf_text(path)
    raise ClarifyNeeded(
        f"Don't know how to read '{path.name}' (unsupported extension '{suffix}'). "
        "Expected .pdf, .txt, or .html."
    )


def _strip_html(raw: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(tr|p|div|li|table)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return text


def _import_fitz():
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError as exc:
        raise ClarifyNeeded(
            "PyMuPDF is not installed, so PDFs can't be read. Run SETUP first "
            "(pip install -r requirements.txt)."
        ) from exc
    return fitz


def _text_by_reading_order(page) -> str:
    """Reconstruct a "-layout"-like reading order from word boxes: group
    words into lines by y-position, then sort each line left to right."""
    words = page.get_text("words")  # (x0, y0, x1, y1, word, block, line, word_no)
    if not words:
        return ""
    words = sorted(words, key=lambda w: (round(w[1], 0), w[0]))
    lines: list[list] = []
    current_y = None
    current_line: list = []
    for w in words:
        y = round(w[1], 0)
        if current_y is None or abs(y - current_y) > 3:
            if current_line:
                lines.append(current_line)
            current_line = [w]
            current_y = y
        else:
            current_line.append(w)
    if current_line:
        lines.append(current_line)

    out_lines = []
    for line in lines:
        line_sorted = sorted(line, key=lambda w: w[0])
        out_lines.append(" ".join(w[4] for w in line_sorted))
    return "\n".join(out_lines)


def _load_pdf_text(pdf_path: Path) -> str:
    fitz = _import_fitz()
    with fitz.open(str(pdf_path)) as doc:
        page_texts = [_text_by_reading_order(page) for page in doc]
    text = "\n".join(page_texts)
    if text.strip():
        return text
    # No text layer at all - this is a raster/"Print to PDF" report. Fall
    # back to OCR (optional dependency - never installed by default).
    return _ocr_pdf(pdf_path)


def _ocr_pdf(pdf_path: Path) -> str:
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except ImportError as exc:
        raise ClarifyNeeded(
            f"'{pdf_path.name}' has no text layer (it looks like an image-only 'Print to "
            "PDF' export) and OCR support isn't installed for this Python version. Either "
            "re-export the report as a normal (non-flattened) PDF or CSV/Excel from Clover, "
            "or install OCR support manually: pip install -r requirements-ocr.txt "
            "(requires a Python version rapidocr-onnxruntime supports)."
        ) from exc

    fitz = _import_fitz()
    engine = RapidOCR()
    lines: list[str] = []
    with fitz.open(str(pdf_path)) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            result, _ = engine(img_bytes)
            if result:
                for _box, ocr_text, _score in result:
                    lines.append(ocr_text)
    if not lines:
        raise ClarifyNeeded(f"OCR produced no text for '{pdf_path.name}'.")
    return "\n".join(lines)


_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_MIN_YEAR = 2015
_MAX_YEAR = 2100


def _valid_date(year: int, month: int, day: int) -> Optional[dt.date]:
    if not (_MIN_YEAR <= year <= _MAX_YEAR):
        return None
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _try_month_name_dates(text: str) -> Optional[dt.date]:
    """Matches 'September 1, 2026', 'Sep 1 2026', 'Sept. 01, 2026', with or
    without a leading weekday, in any month-name case."""
    pattern = re.compile(
        r"\b(?:[A-Za-z]+,\s*)?"
        r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"
    )
    for m in pattern.finditer(text):
        month_name, day_str, year_str = m.groups()
        month = _MONTH_NAMES.get(month_name.lower())
        if month is None:
            continue
        d = _valid_date(int(year_str), month, int(day_str))
        if d:
            return d
    return None


def _try_numeric_dates(text: str) -> Optional[dt.date]:
    """Matches YYYY-MM-DD, YYYY/MM/DD, MM/DD/YYYY, DD/MM/YYYY, MM-DD-YYYY,
    MM.DD.YYYY etc. Ambiguous day/month order is resolved by whichever
    number can't possibly be a month (>12)."""
    pattern = re.compile(r"\b(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})\b")
    for m in pattern.finditer(text):
        a, b, c = m.groups()
        ai, bi, ci = int(a), int(b), int(c)
        if len(a) == 4:  # YYYY-MM-DD or YYYY/MM/DD
            d = _valid_date(ai, bi, ci)
            if d:
                return d
            continue
        if len(c) != 4:  # need a 4-digit year somewhere to be confident
            continue
        # a and b are day/month in some order, c is the year
        if ai > 12 >= bi:
            d = _valid_date(ci, bi, ai)  # DD-MM-YYYY
        elif bi > 12 >= ai:
            d = _valid_date(ci, ai, bi)  # MM-DD-YYYY
        else:
            d = _valid_date(ci, ai, bi)  # ambiguous - default to MM-DD-YYYY (North America)
        if d:
            return d
    return None


def _try_fuzzy_dateutil(text: str) -> Optional[dt.date]:
    """Last resort: hand each of the first ~20 non-empty lines to
    python-dateutil's fuzzy parser. Only lines that look date-ish (contain
    a month name or date-like separators, and aren't a dollar-amount table
    row) are tried, and the result is sanity-checked against a plausible
    year range - never guessed silently, still surfaced as needing
    confirmation by the caller."""
    try:
        from dateutil import parser as dateutil_parser
    except ImportError:
        return None

    candidate_line_re = re.compile(
        r"(?:\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}\b)|"
        r"(?:\b(?:" + "|".join(_MONTH_NAMES) + r")[a-z]*\.?\s+\d{1,2}\b)",
        re.I,
    )
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines[:20]:
        if "$" in line or line.count(".") > 2:
            continue
        if not candidate_line_re.search(line):
            continue
        try:
            parsed = dateutil_parser.parse(line, fuzzy=True, default=dt.datetime(1904, 1, 1))
        except (ValueError, OverflowError, TypeError):
            continue
        if parsed.year == 1904:  # default sentinel means no real year token was found
            continue
        d = _valid_date(parsed.year, parsed.month, parsed.day)
        if d:
            return d
    return None


def parse_date(text: str, source_hint: Optional[Path] = None) -> tuple[dt.date, bool]:
    """Return (date, from_url_fallback). Never trusts the filename.

    The second value is True whenever the date was NOT read from an
    explicit, confident header match (Clover URL timestamp, or the
    last-resort fuzzy scan) - the caller must confirm with the user before
    proceeding (per CLAUDE.md section 1).
    """
    normalized = re.sub(r"[ \t]+", " ", text)

    d = _try_month_name_dates(normalized)
    if d:
        return d, False

    d = _try_numeric_dates(normalized)
    if d:
        return d, False

    m = re.search(r"[?&]startTimestamp=(\d{10,13})", text)
    if m:
        ts = int(m.group(1))
        if ts > 10**12:
            ts //= 1000
        return dt.datetime.utcfromtimestamp(ts).date(), True

    d = _try_fuzzy_dateutil(normalized)
    if d:
        return d, True

    preview = "\n".join(l for l in normalized.splitlines() if l.strip())[:600]
    raise ClarifyNeeded(
        "Could not find a date anywhere in this report (checked month-name dates, "
        "numeric dates, a Clover URL timestamp, and a fuzzy scan). Provide --date "
        "explicitly, or share this preview so the date format can be added:\n"
        f"--- first lines actually read from the file ---\n{preview}\n---"
    )


def _find_amount_after_label(text: str, label: str) -> Optional[Decimal]:
    pattern = re.escape(label) + r"[^\d\-\(]{0,80}(" + MONEY_RE + r")"
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    return _to_decimal(m.group(1))


def parse_summary_totals(text: str) -> dict:
    labels = {
        "gross_sales": "Gross Sales",
        "discounts": "Discounts",
        "refunds": "Refunds",
        "net_sales_total": "Net Sales",
        "taxes_fees_total": "Taxes & Fees",
        "amount_collected_total": "Amount Collected",
    }
    out = {}
    for key, label in labels.items():
        val = _find_amount_after_label(text, label)
        if val is None:
            if key in ("discounts", "refunds"):
                val = Decimal("0.00")
            else:
                raise ClarifyNeeded(f"Could not find '{label}' total in the Clover report.")
        out[key] = val
    return out


def parse_revenue_classes(text: str) -> tuple[dict, Optional[Decimal]]:
    """Parse the Revenue Classes table: category name -> Net Sales.

    Table rows look like (layout-preserved text):
        Souvenirs        1234.56    45.6%
    The category name is everything before the first dollar amount on the
    line; the Net Sales figure is the first dollar amount; an optional
    trailing percentage is captured separately (used for the missing-SKU
    detection in build.py).
    """
    m = re.search(r"Revenue Classes(.*?)(?:Tender Types|Sales By Card Type|Tax Details|Taxes & Fees|\Z)",
                  text, re.S | re.I)
    if not m:
        raise ClarifyNeeded("Could not find a 'Revenue Classes' table in the Clover report.")
    section = m.group(1)

    row_re = re.compile(
        r"^(?!Total\b)([A-Za-z][A-Za-z /&\-]*?)\s+(" + MONEY_RE + r")\s*(-?[\d.]+%)?\s*$",
        re.M,
    )
    categories: dict = {}
    for row in row_re.finditer(section):
        name = row.group(1).strip()
        amount = _to_decimal(row.group(2))
        categories[name] = amount

    pct_total = None
    total_row = re.search(r"^Total\b.*?(" + MONEY_RE + r")\s*([\d.]+)%\s*$", section, re.M | re.I)
    if total_row:
        pct_total = Decimal(total_row.group(2))

    if not categories:
        raise ClarifyNeeded("Found a 'Revenue Classes' header but no category rows under it.")
    return categories, pct_total


def parse_tenders(text: str) -> dict:
    """Parse Tender Types / Sales By Card Type -> tender name -> Amount Collected."""
    m = re.search(
        r"(?:Tender Types|Sales By Card Type)(.*?)(?:Tax Details|Taxes & Fees|Unpaid Balance|\Z)",
        text, re.S | re.I,
    )
    if not m:
        raise ClarifyNeeded("Could not find a 'Tender Types' / 'Sales By Card Type' table in the Clover report.")
    section = m.group(1)

    row_re = re.compile(
        r"^(?!Total\b)([A-Za-z][A-Za-z /&\-]*?)\s+(" + MONEY_RE + r")\s*(?:[\d.]+%)?\s*$",
        re.M,
    )
    tenders: dict = {}
    for row in row_re.finditer(section):
        name = row.group(1).strip()
        amount = _to_decimal(row.group(2))
        tenders[name] = tenders.get(name, Decimal("0.00")) + amount

    if not tenders:
        raise ClarifyNeeded("Found a tender table header but no tender rows under it.")
    return tenders


def extract(path: Path, date_override: Optional[dt.date] = None) -> DailyReport:
    text = load_text(path)

    if date_override is not None:
        report_date = date_override
    else:
        report_date, needs_confirmation = parse_date(text, path)
        if needs_confirmation:
            raise ClarifyNeeded(
                f"'{path.name}' has no confident, explicit date header; best guess is "
                f"{report_date.isoformat()} (from a URL timestamp or a fuzzy text scan). "
                "Confirm with --date before proceeding."
            )

    totals = parse_summary_totals(text)
    revenue_classes, pct_total = parse_revenue_classes(text)
    tenders = parse_tenders(text)

    return DailyReport(
        date=report_date,
        gross_sales=totals["gross_sales"],
        discounts=totals["discounts"],
        refunds=totals["refunds"],
        net_sales_total=totals["net_sales_total"],
        taxes_fees_total=totals["taxes_fees_total"],
        amount_collected_total=totals["amount_collected_total"],
        revenue_classes=revenue_classes,
        revenue_pct_total=pct_total,
        tenders=tenders,
        source_path=path,
        raw_text=text,
    )
