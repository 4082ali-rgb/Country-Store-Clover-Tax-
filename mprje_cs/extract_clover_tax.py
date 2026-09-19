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

from .extract_clover import ClarifyNeeded, MONEY_RE, _to_decimal, load_text, parse_date


@dataclass
class TaxReport:
    date: dt.date
    taxes: dict = field(default_factory=dict)  # tax name -> net tax amount (Decimal)
    no_tax_amount: Optional[Decimal] = None  # informational only
    net_taxes_total: Optional[Decimal] = None
    source_path: Optional[Path] = None
    raw_text: str = ""


def parse_tax_details(text: str, no_tax_labels: list[str]) -> tuple[dict, Optional[Decimal], Optional[Decimal]]:
    m = re.search(r"Tax Details(.*?)(?:Tender Types|Sales By Card Type|\Z)", text, re.S | re.I)
    section = m.group(1) if m else text

    row_re = re.compile(
        r"^(?!Total\b|Net taxes\b)([A-Za-z][A-Za-z 0-9()%.\-]*?)\s+(" + MONEY_RE + r")"
        r"(?:\s+(" + MONEY_RE + r"))?\s*$",
        re.M,
    )
    taxes: dict = {}
    no_tax_amount = None
    for row in row_re.finditer(section):
        name = row.group(1).strip()
        # Prefer the second (net, post-refund) amount when both gross and
        # net columns are present; otherwise use the only amount given.
        amount = _to_decimal(row.group(3) if row.group(3) else row.group(2))
        if any(label.lower() in name.lower() for label in no_tax_labels):
            no_tax_amount = amount
            continue
        taxes[name] = amount

    net_total = None
    net_row = re.search(r"Net taxes\D{0,10}(" + MONEY_RE + r")", section, re.I)
    if net_row:
        net_total = _to_decimal(net_row.group(1))

    if not taxes:
        raise ClarifyNeeded("Found a 'Tax Details' section but no tax rows under it.")
    return taxes, no_tax_amount, net_total


def extract(path: Path, no_tax_labels: list[str], date_override: Optional[dt.date] = None) -> TaxReport:
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

    taxes, no_tax_amount, net_total = parse_tax_details(text, no_tax_labels)

    return TaxReport(
        date=report_date,
        taxes=taxes,
        no_tax_amount=no_tax_amount,
        net_taxes_total=net_total,
        source_path=path,
        raw_text=text,
    )
