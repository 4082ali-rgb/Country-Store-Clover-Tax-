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
from collections import Counter
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
    without a leading weekday, in any month-name case - and also Clover's
    own zero-space header rendering, e.g. 'Sep1,202612:00AM' (whitespace
    between month/day/year is optional, not required)."""
    pattern = re.compile(
        r"\b(?:[A-Za-z]+,\s*)?"
        r"([A-Za-z]{3,9})\.?\s*(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})"
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


def _flex_label_pattern(label: str) -> str:
    """Build a regex for `label` that tolerates missing/extra whitespace -
    Clover's PDF export sometimes concatenates header words with zero
    spaces ("Taxes&Fees", "GrossSales") depending on where they render."""
    return re.escape(label).replace(r"\ ", r"\s*")


def _find_amount_after_label(text: str, label: str) -> Optional[Decimal]:
    pattern = _flex_label_pattern(label) + r"[^\d\-\(]{0,80}(" + MONEY_RE + r")"
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


# The Clover "Sales Overview" PDF export renders each table cell on its own
# line (one value per line, not a single-line row) - a category record looks
# like:
#   Souvenirs
#   127
#   $1,552.20
#   -$1.80
#   $0.00
#   $1,550.40
#   40.91%
#   $174.03
#   $0.00
# Column *order* can drift, but "Net Sales" is always immediately followed
# by "% Net Sales" (it's literally computed from it) - so we anchor on the
# %-sign token rather than counting columns. Category/tender names are also
# sometimes truncated with a trailing ellipsis ("Non Alcohol..", "lce" for
# "Ice") by the report's column width, so matching is done against a known
# list of real Clover category/tender names, by exact match or by the
# report's (possibly truncated) text being a prefix of the real name.

KNOWN_REVENUE_CATEGORIES = [
    "Souvenirs", "Snacks", "Non Alcoholic Beverages", "Alcohol Beverages",
    "Grocery", "Tobacco", "Ice", "Seasonal Items", "Hard Goods",
    "Prepared Foods", "Firewood", "Unclassified",
]

# Known text-extraction glyph artifacts for specific labels (confirmed by
# inspecting a real export) - never a guess, just a literal alias.
_CATEGORY_ALIASES = {"lce": "Ice"}

KNOWN_CASH_AND_OTHER_TENDER_NAMES = ["Cash", "Gift Certificates", "Gift Certificate", "Room Charge"]
KNOWN_CARD_TYPE_NAMES = ["Interac", "Debit", "Visa", "MasterCard", "Mastercard", "Amex", "American Express"]
KNOWN_AGGREGATE_TENDER_NAMES = ["Credit Card", "Debit Card"]

MONEY_TOKEN_RE = re.compile(r"^\(?-?\$?[\d,]+\.\d{2}\)?$")
PERCENT_TOKEN_RE = re.compile(r"^-?\d+(\.\d+)?%$")


def _split_name_and_rest(line: str, candidates: list) -> tuple:
    """Return (matched_name, remaining_value_tokens) for a line, or
    (None, None) if it doesn't start with a known name. Handles both
    layouts seen in practice:
      - one value per line (the real Clover PDF export): the whole line
        IS the name, possibly truncated with an ellipsis ("Non Alcohol..",
        "lce" for "Ice") - values follow on their own subsequent lines.
      - a single-line tabular row (pasted text, older exports): the name
        and its values all share one line ("Cash   448.36").
    """
    stripped = line.strip()
    if not stripped:
        return None, None

    all_candidates = list(candidates) + ["Total"]

    cleaned_whole = stripped.rstrip(".").rstrip("…").strip()
    if cleaned_whole in _CATEGORY_ALIASES and _CATEGORY_ALIASES[cleaned_whole] in candidates:
        return _CATEGORY_ALIASES[cleaned_whole], []
    low_whole = cleaned_whole.lower()
    for c in all_candidates:
        if c.lower() == low_whole:
            return c, []
    if len(low_whole) >= 3:
        for c in all_candidates:
            if c.lower().startswith(low_whole):
                return c, []

    low = stripped.lower()
    best = None
    for c in all_candidates:
        cl = c.lower()
        if low.startswith(cl) and (len(low) == len(cl) or not low[len(cl)].isalnum()):
            if best is None or len(cl) > len(best.lower()):
                best = c
    if best:
        rest = stripped[len(best):].strip()
        return best, rest.split()

    # Single-line tabular row where the name itself is truncated
    # mid-word by column width, with the ellipsis embedded before the
    # numeric columns rather than at the very end of the line - e.g.
    # "Alcohol … 28 $362.10 ...", "Non Alco… 150 $557.59 ...",
    # "Prepared… 8 $50.75 ...", "Seasonal… 17 $245.87 ...". The whole-line
    # prefix check above can't match these because the line doesn't END
    # with the (truncated) name - numbers follow it on the same line.
    # Only attempted when an ellipsis is actually present, to avoid ever
    # touching an already-working, non-truncated single-line row.
    if "…" in stripped:
        tokens = stripped.split()
        idx = None
        for i, t in enumerate(tokens):
            if MONEY_TOKEN_RE.match(t) or PERCENT_TOKEN_RE.match(t) or re.match(r"^\d+$", t):
                idx = i
                break
        if idx:  # idx is None or 0 means no leading name tokens - skip
            name_tokens = tokens[:idx]
            if any("…" in t for t in name_tokens):
                name_clean = " ".join(name_tokens).replace("…", "").strip()
                if len(name_clean) >= 3:
                    low_clean = name_clean.lower()
                    best = None
                    for c in candidates:
                        if c.lower().startswith(low_clean):
                            if best is None or len(c) > len(best):
                                best = c
                    if best:
                        return best, tokens[idx:]

    return None, None


def _scan_records(lines: list, candidates: list) -> dict:
    """Group lines into {matched_name: [value tokens]} records, anchored on
    lines that (start with) a known name or the literal "Total" row."""
    records: dict = {}
    current_name = None
    current_values: list = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        matched, rest_tokens = _split_name_and_rest(stripped, candidates)
        if matched:
            if current_name is not None:
                records.setdefault(current_name, []).extend(current_values)
            current_name = matched
            current_values = list(rest_tokens)
        elif current_name is not None:
            # One-value-per-line layout: an unmatched line is a single
            # field (which may itself be a multi-word category name this
            # code doesn't recognize, e.g. "Propane Firepits" - keep it as
            # one token, don't split it into separate words). Single-line
            # tabular layout: an unmatched row packs a name and its values
            # onto one line together - only split when the line actually
            # contains recognizable numeric tokens.
            tokens = stripped.split()
            if any(MONEY_TOKEN_RE.match(t) or PERCENT_TOKEN_RE.match(t) for t in tokens):
                current_values.extend(tokens)
            else:
                current_values.append(stripped)
    if current_name is not None:
        records.setdefault(current_name, []).extend(current_values)
    return records


def _net_sales_from_values(values: list) -> Optional[Decimal]:
    for i, v in enumerate(values):
        if i > 0 and PERCENT_TOKEN_RE.match(v) and MONEY_TOKEN_RE.match(values[i - 1]):
            return _to_decimal(values[i - 1])
    return None


def _percent_value(values: list) -> Optional[Decimal]:
    for v in values:
        if PERCENT_TOKEN_RE.match(v):
            return Decimal(v.rstrip("%"))
    return None


def _last_money(values: list) -> Optional[Decimal]:
    for v in reversed(values):
        if MONEY_TOKEN_RE.match(v):
            return _to_decimal(v)
    return None


def _section(text: str, start_label: str, end_labels: list) -> Optional[str]:
    pattern = _flex_label_pattern(start_label) + r"(.*?)(?:" + \
        "|".join(_flex_label_pattern(e) for e in end_labels) + r"|\Z)"
    m = re.search(pattern, text, re.S | re.I)
    return m.group(1) if m else None


def parse_revenue_classes(text: str) -> tuple:
    """Parse the Revenue Classes table: category name -> Net Sales.

    Net Sales is identified as the dollar value immediately preceding the
    "% Net Sales" value in each category's record, regardless of how many
    other columns the report shows either side of it, or what order they
    come in - this has been observed to vary (GrossSales/Discounts/Refunds
    can come in a different order depending on the machine/PyMuPDF version
    reading the PDF), but Net Sales is always the figure %NetSales is
    computed from, so it's the one reliable anchor.

    A category name this code doesn't recognize has no line of its own to
    start a record with under anchor-based scanning - its name and values
    get appended onto whichever known category happened to come right
    before it (the same failure mode as the "LiquorTax(10%)" tax-matching
    bug, one layer up). Detected here as: that category's record ends up
    noticeably longer than a normal one. When that happens, the excess is
    peeled off in fixed-size groups (the modal/most-common record length)
    and surfaced as its own record - flagged as an unmapped placeholder by
    build.py - instead of silently corrupting the category it landed on.
    This only ever touches a record that's already longer than normal; a
    normal-length record is never rewritten, so it can't be corrupted by
    the recovery logic itself (unlike an earlier version of this function,
    which re-derived a single field count for the WHOLE table from the
    shortest matched record and rebuilt everything from that - fragile to
    any one record being short, which broke real reports).
    """
    section = _section(text, "Revenue Classes",
                        ["Sales By Card Type", "Tender Types", "Cash Deposits", "Cash Adjustments"])
    if section is None:
        raise ClarifyNeeded("Could not find a 'Revenue Classes' table in the Clover report.")

    lines = [l for l in section.splitlines() if l.strip()]
    anchored = _scan_records(lines, KNOWN_REVENUE_CATEGORIES)

    non_total_lengths = [len(v) for k, v in anchored.items() if k != "Total" and v]
    if not non_total_lengths:
        raise ClarifyNeeded(
            "Found a 'Revenue Classes' table but couldn't read any category's Net Sales figure. "
            "Preview of that section:\n" + "\n".join(lines[:40])
        )
    # A genuinely clean (non-bloated) record has exactly one %NetSales
    # token; a record that swallowed one or more unrecognized categories
    # has two or more (each category's own row carries its own %). Prefer
    # deriving the field count from clean records only - a plain length
    # mode/min can be fooled by a small sample (e.g. only 2 categories,
    # one bloated) into picking the bloated length as if it were normal.
    clean_lengths = [
        len(v) for k, v in anchored.items()
        if k != "Total" and v and sum(1 for tok in v if PERCENT_TOKEN_RE.match(tok)) == 1
    ]
    field_count = Counter(clean_lengths or non_total_lengths).most_common(1)[0][0]

    categories: dict = {}
    pct_total = None
    for name, values in anchored.items():
        if name == "Total":
            pct_total = _percent_value(values)
            continue

        own_values = values[:field_count] if len(values) >= field_count else values
        net = _net_sales_from_values(own_values)
        if net is not None:
            categories[name] = categories.get(name, Decimal("0.00")) + net

        # Anything past the first `field_count` values wasn't this
        # category's own data - peel it off in (name + field_count values)
        # groups, each belonging to a category this code doesn't recognize.
        leftover = values[field_count:]
        while len(leftover) >= field_count + 1:
            swallowed_name = leftover[0]
            swallowed_values = leftover[1:1 + field_count]
            leftover = leftover[1 + field_count:]
            swallowed_net = _net_sales_from_values(swallowed_values)
            if swallowed_net is not None:
                categories[swallowed_name] = categories.get(swallowed_name, Decimal("0.00")) + swallowed_net

    if not categories:
        raise ClarifyNeeded(
            "Found a 'Revenue Classes' table but couldn't read any category's Net Sales figure. "
            "Preview of that section:\n" + "\n".join(lines[:40])
        )
    return categories, pct_total


def parse_tenders(text: str) -> dict:
    """Combine Amount Collected across Tender Types (Cash / Gift
    Certificates / Room Charge) and Sales By Card Type (Interac / Visa /
    MasterCard / ...). Falls back to Tender Types' own Credit Card / Debit
    Card aggregate lines if no Sales By Card Type breakdown is present, so
    the entry still balances either way."""
    tenders: dict = {}

    end_labels = ["Sales By Card Type", "Revenue Classes", "Cash Deposits",
                  "Cash Adjustments", "Tax Details", "Unpaid Balance"]
    tender_section = _section(text, "Tender Types", end_labels)
    card_section = _section(text, "Sales By Card Type", end_labels)

    if card_section:
        lines = [l for l in card_section.splitlines() if l.strip()]
        for name, values in _scan_records(lines, KNOWN_CARD_TYPE_NAMES).items():
            if name == "Total":
                continue
            amt = _last_money(values)
            if amt is not None:
                tenders[name] = tenders.get(name, Decimal("0.00")) + amt

    if tender_section:
        lines = [l for l in tender_section.splitlines() if l.strip()]
        # If a separate Sales By Card Type breakdown already supplied the
        # granular card amounts, only take Cash/Gift Certs/Room Charge here
        # (skip Credit Card/Debit Card aggregates and any repeated card
        # names to avoid double-counting). Otherwise, this table might
        # itself list card brands directly (no separate breakdown table at
        # all), or only aggregate Credit Card/Debit Card lines - accept
        # either so the entry still balances.
        names = KNOWN_CASH_AND_OTHER_TENDER_NAMES + (
            [] if tenders else KNOWN_CARD_TYPE_NAMES + KNOWN_AGGREGATE_TENDER_NAMES
        )
        for name, values in _scan_records(lines, names).items():
            if name == "Total":
                continue
            amt = _last_money(values)
            if amt is not None:
                tenders[name] = tenders.get(name, Decimal("0.00")) + amt

    if not tenders:
        raise ClarifyNeeded("Could not find any tender/card-type amounts in the Clover report.")
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
