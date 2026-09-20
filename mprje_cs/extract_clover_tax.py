"""Clover "Tax Details" / Taxes Report -> structured data.

Per CLAUDE.md: this is the source of truth for GST/PST/Liquor Tax whenever
it disagrees with the main report's own Revenue Classes "Taxes & Fees"
total. "No Tax (0%)" rows are informational only and never get a GL line.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .extract_clover import ClarifyNeeded, _section, _last_money, _flex_label_pattern, load_text, parse_date


@dataclass
class TaxReport:
    date: dt.date
    taxes: dict = field(default_factory=dict)  # tax name -> net tax amount (Decimal)
    no_tax_amount: Optional[Decimal] = None  # informational only
    net_taxes_total: Optional[Decimal] = None
    source_path: Optional[Path] = None
    raw_text: str = ""


# Like Revenue Classes, the Tax Details table renders one value per line.
# Each row's name carries its own rate suffix ("GST (5%)", "Liquor Tax(10%)")
# with inconsistent spacing, and the Total row omits the "Applicable sales"
# column entirely (3 values instead of 4). Rather than depend on column
# count, the amount we want - Net taxes, post-refund - is always the LAST
# value in a row's run, whether that run has 3 or 4 numbers.

KNOWN_TAX_NAMES = ["GST", "PST", "Liquor Tax", "No Tax", "HST", "QST"]


def _split_tax_name_and_rest(line: str, known_names: list) -> tuple:
    """Like extract_clover._split_name_and_rest, but a tax row's name always
    carries its own rate suffix ("GST (5%)", "Liquor Tax(10%)", "LiquorTax(10%)"
    with fully inconsistent spacing - some reports even drop the space
    inside a two-word name entirely) which is stripped/tolerated before
    matching. Handles both one-value-per-line (real Clover PDF) and
    single-line tabular rows (pasted text)."""
    stripped = line.strip()
    if not stripped:
        return None, None
    all_names = known_names + ["Total"]

    # Whole-line match: strip a trailing rate suffix like "(5%)"/"(10%)"
    # first, then compare tolerating missing/extra whitespace in the name.
    whole_base = re.sub(r"\s*\([^)]*\)\s*$", "", stripped).strip()
    for name in all_names:
        if re.fullmatch(_flex_label_pattern(name), whole_base, re.I):
            return name, []

    # Line starts with a known name (possibly with the rate suffix attached
    # right after, with or without a space), followed by more content -
    # the single-line tabular layout.
    best = None
    best_end = 0
    for name in all_names:
        pattern = r"^" + _flex_label_pattern(name) + r"\s*(?:\([^)]*\))?"
        m = re.match(pattern, stripped, re.I)
        if m and m.end() > best_end:
            best = name
            best_end = m.end()
    if best:
        rest = stripped[best_end:].strip()
        return best, rest.split()

    return None, None


def parse_tax_details(text: str, no_tax_labels: list[str]) -> tuple[dict, Optional[Decimal], Optional[Decimal]]:
    section = _section(text, "Tax Details", ["Tender Types", "Sales By Card Type"])
    if section is None:
        section = text

    lines = [l for l in section.splitlines() if l.strip()]
    known_names = KNOWN_TAX_NAMES + no_tax_labels

    records: dict = {}
    current_name = None
    current_values: list = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("net tax"):
            # A standalone "Net taxes: $X" summary line some report layouts
            # print separately from the table - it's redundant with the
            # Total row's own last value, so just skip it rather than let
            # it corrupt whatever record came right before it.
            continue
        matched, rest_tokens = _split_tax_name_and_rest(stripped, known_names)
        if matched:
            if current_name is not None:
                records.setdefault(current_name, []).extend(current_values)
            current_name = matched
            current_values = list(rest_tokens)
        elif current_name is not None:
            current_values.extend(stripped.split())
    if current_name is not None:
        records.setdefault(current_name, []).extend(current_values)

    taxes: dict = {}
    no_tax_amount = None
    net_total = None
    for name, values in records.items():
        amount = _last_money(values)
        if amount is None:
            continue
        if name == "Total":
            net_total = amount
        elif any(label.lower() == name.lower() for label in no_tax_labels):
            no_tax_amount = amount
        else:
            taxes[name] = taxes.get(name, Decimal("0.00")) + amount

    if not taxes:
        raise ClarifyNeeded(
            "Found a 'Tax Details' section but couldn't read any tax amounts. "
            "Preview of that section:\n" + "\n".join(lines[:40])
        )
    return taxes, no_tax_amount, net_total


def extract(path: Path, no_tax_labels: list[str], date_override: Optional[dt.date] = None) -> TaxReport:
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

    taxes, no_tax_amount, net_total = parse_tax_details(text, no_tax_labels)

    return TaxReport(
        date=report_date,
        taxes=taxes,
        no_tax_amount=no_tax_amount,
        net_taxes_total=net_total,
        source_path=path,
        raw_text=text,
    )
