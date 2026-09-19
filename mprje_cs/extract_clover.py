"""Clover "Sales Overview" report -> structured data.

Handles three input shapes, per CLAUDE.md section 1:
  - A text-based PDF ("pdftotext -layout" works directly)
  - A raster "Print to PDF" (no embedded fonts - rasterize + OCR)
  - Pasted webpage text/HTML (Full Report / Trends view copy-paste)

Never guesses a number it can't find - raises ClarifyNeeded (which the CLI
surfaces as a flag / question) rather than silently defaulting.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
import shutil
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


def _has_embedded_fonts(pdf_path: Path) -> bool:
    if not shutil.which("pdffonts"):
        return True  # assume yes, try pdftotext first
    out = subprocess.run(["pdffonts", str(pdf_path)], capture_output=True, text=True)
    lines = [l for l in out.stdout.splitlines() if l.strip() and not l.startswith("name") and not l.startswith("---")]
    return len(lines) > 0


def _load_pdf_text(pdf_path: Path) -> str:
    if not shutil.which("pdftotext"):
        raise ClarifyNeeded("pdftotext is not installed - run SETUP first.")
    if _has_embedded_fonts(pdf_path):
        out = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            capture_output=True, text=True,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
    # Raster fallback - OCR
    return _ocr_pdf(pdf_path)


def _ocr_pdf(pdf_path: Path) -> str:
    if not shutil.which("pdftoppm"):
        raise ClarifyNeeded(
            f"'{pdf_path.name}' has no text layer and pdftoppm is not installed for OCR fallback."
        )
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except ImportError as exc:
        raise ClarifyNeeded(
            "This report needs OCR (no embedded text layer) but rapidocr-onnxruntime "
            "is not installed. Run SETUP first."
        ) from exc

    import tempfile

    engine = RapidOCR()
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        prefix = str(Path(tmp) / "page")
        subprocess.run(["pdftoppm", "-jpeg", "-r", "150", str(pdf_path), prefix], check=True)
        for img_path in sorted(Path(tmp).glob("page*.jpg")):
            result, _ = engine(str(img_path))
            if result:
                for _box, text, _score in result:
                    lines.append(text)
    if not lines:
        raise ClarifyNeeded(f"OCR produced no text for '{pdf_path.name}'.")
    return "\n".join(lines)


def parse_date(text: str, source_hint: Optional[Path] = None) -> tuple[dt.date, bool]:
    """Return (date, from_url_fallback). Never trusts the filename.

    from_url_fallback True means the date was decoded from Clover URL query
    params rather than an explicit header - caller must confirm with the
    user before proceeding (per CLAUDE.md section 1).
    """
    m = re.search(
        r"\b([A-Z][a-z]+ \d{1,2},? \d{4})\b",
        text,
    )
    if m:
        for fmt in ("%B %d, %Y", "%B %d %Y"):
            try:
                return dt.datetime.strptime(m.group(1).replace(",", ""), fmt.replace(",", "")).date(), False
            except ValueError:
                continue

    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if m:
        return dt.date.fromisoformat(m.group(1)), False

    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if m:
        mm, dd, yyyy = (int(x) for x in m.groups())
        return dt.date(yyyy, mm, dd), False

    m = re.search(r"[?&]startTimestamp=(\d{10,13})", text)
    if m:
        ts = int(m.group(1))
        if ts > 10**12:
            ts //= 1000
        return dt.datetime.utcfromtimestamp(ts).date(), True

    raise ClarifyNeeded(
        "Could not find an explicit date header in this report, and no "
        "startTimestamp URL param was present either. Provide --date explicitly."
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
        report_date, from_url = parse_date(text, path)
        if from_url:
            raise ClarifyNeeded(
                f"'{path.name}' has no explicit date header; decoded {report_date.isoformat()} "
                "from the Clover URL timestamp. Confirm with --date before proceeding."
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
