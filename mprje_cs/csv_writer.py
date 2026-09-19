"""QBO-ready CSV formatting - identical rules to the Accommodation builder.

Every row repeats JournalNo/JournalDate/Memo, CRLF line endings, DD-MM-YYYY
dates, no commas in Description, no $0.00 lines, Class never blank. The
finished text is re-parsed before AND after writing to disk - never trust
a string that was built without checking it back.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path

from .build import BuildResult, q

HEADER = ["*JournalNo", "*JournalDate", "Memo", "*AccountName", "Debits", "Credits",
          "Description", "Name", "Location", "Class"]

_MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


class CSVValidationError(RuntimeError):
    pass


def _memo(date, template: str) -> str:
    return template.format(date=f"{_MONTHS[date.month]} {date.day} {date.year}")


def _description(prefix: str, flag: str, memo: str) -> str:
    # `prefix`, when present, already ends in "- " (e.g. "Visa - ") per gl_mapping.yaml.
    desc = prefix or ""
    if flag:
        desc += f"[{flag}]- "
    desc += memo
    if "," in desc:
        raise CSVValidationError(f"Description contains a comma, which is not allowed: {desc!r}")
    return desc


def render(result: BuildResult, memo_template: str) -> str:
    date_str = result.date.strftime("%d-%m-%Y")
    memo = _memo(result.date, memo_template)

    rows = []
    for line in result.lines:
        if q(line.debit) == Decimal("0.00") and q(line.credit) == Decimal("0.00"):
            continue
        description = _description(line.prefix, line.flag, memo)
        class_value = line.class_ or "0030-COUNTRY STORE"
        rows.append([
            result.journal_no,
            date_str,
            memo,
            line.account,
            f"{q(line.debit):.2f}" if line.debit else "",
            f"{q(line.credit):.2f}" if line.credit else "",
            description,
            line.name,
            line.location,
            class_value,
        ])

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(HEADER)
    for row in rows:
        writer.writerow(row)
    text = buf.getvalue()

    _validate(text, result, memo)
    return text


def _validate(text: str, result: BuildResult, memo: str) -> None:
    if "\r\n" not in text:
        raise CSVValidationError("CSV text does not contain CRLF line endings.")
    raw_lines = text.split("\r\n")
    raw_lines = [l for l in raw_lines if l != ""]
    reader = csv.reader(raw_lines)
    parsed = list(reader)
    if len(parsed) < 2:
        raise CSVValidationError("CSV has fewer than two lines (header + at least one row).")
    header, *data_rows = parsed
    if header != HEADER:
        raise CSVValidationError(f"CSV header mismatch: {header}")

    total_debits = Decimal("0.00")
    total_credits = Decimal("0.00")
    for row in data_rows:
        if len(row) != len(HEADER):
            raise CSVValidationError(f"Row has wrong column count ({len(row)}): {row}")
        journal_no, journal_date, row_memo, account, debits, credits, description, name, location, class_ = row
        if not journal_no:
            raise CSVValidationError("Row missing *JournalNo.")
        if journal_no != result.journal_no:
            raise CSVValidationError(f"Row JournalNo '{journal_no}' does not match '{result.journal_no}'.")
        if row_memo != memo:
            raise CSVValidationError(f"Row Memo does not match expected memo: {row_memo!r}")
        if not class_:
            raise CSVValidationError(f"Row has a blank Class: {row}")
        if debits == "0.00" or credits == "0.00":
            raise CSVValidationError(f"Row has a $0.00 amount, which is not allowed: {row}")
        if debits and credits:
            raise CSVValidationError(f"Row has both a Debit and a Credit: {row}")
        if "," in description:
            raise CSVValidationError(f"Description contains a comma: {description!r}")
        if not description.endswith(memo):
            raise CSVValidationError(f"Description does not end with the memo suffix: {description!r}")
        total_debits += Decimal(debits) if debits else Decimal("0.00")
        total_credits += Decimal(credits) if credits else Decimal("0.00")

    if q(total_debits) != q(total_credits):
        raise CSVValidationError(f"CSV does not balance: Debits {total_debits} != Credits {total_credits}")


def write(result: BuildResult, memo_template: str, out_path: Path) -> str:
    text = render(result, memo_template)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(text.encode("utf-8"))
    # Re-read from disk and validate again - never trust the in-memory string alone.
    on_disk = out_path.read_bytes().decode("utf-8")
    if b"\r\n" not in out_path.read_bytes():
        raise CSVValidationError("File on disk does not contain CRLF bytes.")
    _validate(on_disk, result, _memo(result.date, memo_template))
    return text
