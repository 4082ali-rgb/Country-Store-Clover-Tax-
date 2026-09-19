"""Balance checks, GL lookups, and JE line construction for Country Store.

Rules encoded here come straight from CLAUDE.md (the filled-in equivalent of
build-instructions sections 3/4). Nothing here invents an account, a sign,
or a plug - see the four-point balance checklist and the special cases
below.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from .extract_clover import DailyReport, ClarifyNeeded
from .extract_clover_tax import TaxReport

PENNY = Decimal("0.01")


def q(amount: Decimal) -> Decimal:
    return amount.quantize(PENNY, rounding=ROUND_HALF_UP)


@dataclass
class JELine:
    account: str
    debit: Decimal
    credit: Decimal
    prefix: str = ""
    flag: str = ""
    name: str = ""
    location: str = ""
    class_: str = "0030-COUNTRY STORE"


@dataclass
class BuildResult:
    journal_no: str
    date: dt.date
    lines: list = field(default_factory=list)  # list[JELine]
    flags: list = field(default_factory=list)  # list[str]
    total_debits: Decimal = Decimal("0.00")
    total_credits: Decimal = Decimal("0.00")


def _lookup(table: dict, name: str) -> Optional[dict]:
    for key, val in table.items():
        if key.strip().lower() == name.strip().lower():
            return val
    # loose match: table key is a substring of the report's label or vice versa
    for key, val in table.items():
        if key.strip().lower() in name.strip().lower() or name.strip().lower() in key.strip().lower():
            return val
    return None


def run_balance_checklist(daily: DailyReport, tax: TaxReport, mapping: dict) -> list[str]:
    """The four-point checklist from CLAUDE.md section 2. Returns non-fatal
    discrepancy notes; raises ClarifyNeeded on the master check failure with
    no special case available (handled by caller, not here).
    """
    notes: list[str] = []

    # 1. revenue categories sum to reported Net Sales
    rev_sum = q(sum(daily.revenue_classes.values(), Decimal("0.00")))
    if rev_sum != q(daily.net_sales_total):
        notes.append(
            f"Revenue Classes sum ({rev_sum}) does not equal reported Net Sales "
            f"({q(daily.net_sales_total)})."
        )

    # 2. tax details sum vs reported Taxes & Fees - Tax Details is the source of truth
    tax_sum = q(sum(tax.taxes.values(), Decimal("0.00")))
    if tax.net_taxes_total is not None and q(tax.net_taxes_total) != tax_sum:
        notes.append(
            f"Tax Details rows sum ({tax_sum}) does not equal the Tax Details report's own "
            f"'Net taxes' total ({q(tax.net_taxes_total)}) - using the row sum."
        )
    if tax_sum != q(daily.taxes_fees_total):
        notes.append(
            f"Tax Details total ({tax_sum}) disagrees with the Clover report's own "
            f"Taxes & Fees total ({q(daily.taxes_fees_total)}) - using Tax Details "
            "as source of truth, per standing rule."
        )

    # 3. tenders sum to reported Amount Collected
    tender_sum = q(sum(daily.tenders.values(), Decimal("0.00")))
    if tender_sum != q(daily.amount_collected_total):
        notes.append(
            f"Tender Types sum ({tender_sum}) does not equal reported Amount Collected "
            f"({q(daily.amount_collected_total)})."
        )

    return notes


def _detect_missing_sku(daily: DailyReport) -> bool:
    """CLAUDE.md 'missing SKU' special case: categories excluding Unclassified
    already equal Net Sales, and the report's own %-of-Net-Sales total is
    broken (> 100%)."""
    unclassified = _lookup(daily.revenue_classes, "Unclassified")
    if unclassified is None or unclassified <= 0:
        return False
    others_sum = q(sum(
        (v for k, v in daily.revenue_classes.items() if k.strip().lower() != "unclassified"),
        Decimal("0.00"),
    ))
    pct_broken = daily.revenue_pct_total is not None and daily.revenue_pct_total > Decimal("100.5")
    return others_sum == q(daily.net_sales_total) and pct_broken


def build_entry(
    daily: DailyReport,
    tax: TaxReport,
    mapping: dict,
    journal_no: str,
) -> BuildResult:
    flags: list[str] = []
    checklist_notes = run_balance_checklist(daily, tax, mapping)
    flags.extend(checklist_notes)

    class_default = mapping.get("class_default", "0030-COUNTRY STORE")
    lines: list[JELine] = []

    revenue_classes = dict(daily.revenue_classes)

    # Missing-SKU special case: exclude entirely, flag for Beverly.
    if _detect_missing_sku(daily):
        amt = revenue_classes.pop("Unclassified")
        flags.append(
            f"Excluded 'Unclassified' (${amt}) - looks like the Aug 7 missing-SKU pattern "
            "(reconciles outside Net Sales, %-of-Net-Sales total broken). "
            "Flag for Beverly to assign a proper SKU/category. Not posted today."
        )

    # Ambiguous-refund special case: negative Unclassified -> Refunds-Allowances debit.
    refunds_allowances_amount = Decimal("0.00")
    if "Unclassified" in revenue_classes and revenue_classes["Unclassified"] < 0:
        refunds_allowances_amount = -revenue_classes.pop("Unclassified")
        flags.append(
            f"'Unclassified' was negative (refund with no matching sale this period) - "
            f"booked ${q(refunds_allowances_amount)} as a debit to Refunds-Allowances "
            "instead of a negative revenue credit, per the Aug 8 precedent. Confirm this "
            "matches the actual bank deposit if it hasn't been confirmed already."
        )

    # --- Credit lines: revenue categories ---
    for category, amount in revenue_classes.items():
        if q(amount) == Decimal("0.00"):
            continue
        entry = _lookup(mapping.get("revenue_classes", {}), category)
        if entry is None:
            fallback = mapping["unmapped_default"]
            lines.append(JELine(
                account=fallback["account"],
                debit=Decimal("0.00"),
                credit=q(amount),
                prefix=fallback.get("prefix", ""),
                flag=f"FIRST-APPEARANCE CODE {category} - best guess",
                class_=fallback.get("class", class_default),
            ))
            flags.append(f"Unmapped revenue category '{category}' (${q(amount)}) - posted to "
                         f"{fallback['account']} as a flagged placeholder. Add it to gl_mapping.yaml.")
        else:
            lines.append(JELine(
                account=entry["account"],
                debit=Decimal("0.00"),
                credit=q(amount),
                prefix=entry.get("prefix", ""),
                class_=entry.get("class", class_default),
            ))

    if refunds_allowances_amount > 0:
        ra = mapping["refunds_allowances"]
        lines.append(JELine(
            account=ra["account"],
            debit=q(refunds_allowances_amount),
            credit=Decimal("0.00"),
            prefix=ra.get("prefix", ""),
            class_=ra.get("class", class_default),
        ))

    # --- Credit lines: taxes (Tax Details table = source of truth) ---
    for tax_name, amount in tax.taxes.items():
        if q(amount) == Decimal("0.00"):
            continue
        entry = _lookup(mapping.get("taxes", {}), tax_name)
        if entry is None:
            fallback = mapping["unmapped_default"]
            lines.append(JELine(
                account=fallback["account"],
                debit=Decimal("0.00"),
                credit=q(amount),
                prefix=fallback.get("prefix", ""),
                flag=f"FIRST-APPEARANCE CODE {tax_name} (tax) - best guess",
                class_=fallback.get("class", class_default),
            ))
            flags.append(f"Unmapped tax line '{tax_name}' (${q(amount)}) - posted to "
                         f"{fallback['account']} as a flagged placeholder. Add it to gl_mapping.yaml.")
        else:
            lines.append(JELine(
                account=entry["account"],
                debit=Decimal("0.00"),
                credit=q(amount),
                prefix=entry.get("prefix", ""),
                class_=entry.get("class", class_default),
            ))

    # --- Debit lines: tenders ---
    for tender_name, amount in daily.tenders.items():
        if q(amount) == Decimal("0.00"):
            continue
        entry = _lookup(mapping.get("tenders", {}), tender_name)
        if entry is None:
            fallback = mapping["unmapped_default"]
            lines.append(JELine(
                account=fallback["account"],
                debit=q(amount),
                credit=Decimal("0.00"),
                prefix=f"{tender_name} - ",
                flag=f"FIRST-APPEARANCE CODE {tender_name} (tender) - best guess",
                class_=fallback.get("class", class_default),
            ))
            flags.append(f"Unmapped tender '{tender_name}' (${q(amount)}) - posted to "
                         f"{fallback['account']} as a flagged placeholder. Add it to gl_mapping.yaml.")
        else:
            lines.append(JELine(
                account=entry["account"],
                debit=q(amount),
                credit=Decimal("0.00"),
                prefix=entry.get("prefix", ""),
                class_=entry.get("class", class_default),
            ))

    # Disambiguate: only apply a line's prefix if its account repeats in this
    # entry (or the mapping file's global always_label is set), per CLAUDE.md
    # section 6 ("lines needing disambiguation ... get a short prefix").
    always_label = mapping.get("description", {}).get("always_label", False)
    account_counts: dict = {}
    for line in lines:
        account_counts[line.account] = account_counts.get(line.account, 0) + 1
    for line in lines:
        if not always_label and account_counts[line.account] <= 1 and not line.flag:
            line.prefix = ""

    total_debits = q(sum((l.debit for l in lines), Decimal("0.00")))
    total_credits = q(sum((l.credit for l in lines), Decimal("0.00")))

    if total_debits != total_credits:
        raise ClarifyNeeded(
            f"Entry does not balance: Debits {total_debits} != Credits {total_credits}. "
            "Per the non-negotiable rule, no CSV is written. Diagnostic notes: "
            + "; ".join(checklist_notes) if checklist_notes else
            f"Entry does not balance: Debits {total_debits} != Credits {total_credits}. "
            "No CSV written."
        )

    return BuildResult(
        journal_no=journal_no,
        date=daily.date,
        lines=lines,
        flags=flags,
        total_debits=total_debits,
        total_credits=total_credits,
    )
