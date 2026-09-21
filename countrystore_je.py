#!/usr/bin/env python3
"""Country Store Daily Revenue JE builder - same CLI shape as the
Accommodation builder, deliberately.

    python countrystore_je.py --inbox
    python countrystore_je.py --dayend-report Clover.pdf --tax-report CloverTax.pdf \\
        [--journal-no JJxxxx] [--date "7 September 2026"]
    python countrystore_je.py --set-journal JJ3148
    python countrystore_je.py --reset-journal
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import sys
from pathlib import Path

import yaml

import mprje_cs
from mprje_cs import extract_clover, extract_clover_tax, build, csv_writer, journal
from mprje_cs.extract_clover import ClarifyNeeded
from mprje_cs.csv_writer import CSVValidationError

ROOT = Path(__file__).resolve().parent
MAPPING_PATH = ROOT / "gl_mapping.yaml"
INBOX_DIR = ROOT / "inbox"
OUTPUT_DIR = ROOT / "output"
STATE_PATH = ROOT / "journal_state.json"

# Bumped on every fix, in lockstep with mprje_cs.PACKAGE_VERSION. Printed on
# every run so it's never ambiguous whether you're running the current
# code. The two are checked against each other below: a mismatch means the
# mprje_cs/ folder and countrystore_je.py came from different downloads (a
# partial extraction/overwrite), which the banner alone can't catch since
# it only lives in this file - the parsing logic doing the actual work
# lives in mprje_cs/, and that's the half that matters most.
BUILD_VERSION = "2026-09-21.1"

if mprje_cs.PACKAGE_VERSION != BUILD_VERSION:
    print(f"Country Store JE builder - build {BUILD_VERSION}")
    print(
        f"STOPPED - version mismatch: countrystore_je.py is build {BUILD_VERSION} but the "
        f"mprje_cs/ folder next to it is build {mprje_cs.PACKAGE_VERSION}. These must come from "
        "the SAME download/extraction. Delete this whole folder, extract a fresh copy of the "
        "ZIP you were given, and run from there - do not copy just one file into an old folder.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def load_mapping() -> dict:
    with open(MAPPING_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _edit_distance(a: str, b: str) -> int:
    """Damerau-Levenshtein distance (insert/delete/substitute/adjacent-swap)
    between two short words - pure stdlib, no dependency. Treats a swapped
    pair of adjacent letters ("Sotre" for "Store") as a single edit, since
    that's one of the most common typo shapes, not two substitutions."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def _fuzzy_word_in_filename(filename: str, keyword: str) -> bool:
    """True if any word in the filename is `keyword`, a typo of it (small
    edit distance), or contains it as a substring - tolerates things like
    "Counntry" for "country", "Sotre" for "store", "Taxx" for "tax", without
    needing an exact spelling or any external dependency."""
    words = re.split(r"[^a-z0-9]+", filename.lower())
    max_dist = 1 if len(keyword) <= 5 else 2
    for word in words:
        if not word:
            continue
        if keyword in word or word in keyword:
            return True
        if abs(len(word) - len(keyword)) <= max_dist and _edit_distance(word, keyword) <= max_dist:
            return True
    return False


def _is_tax_report(filename: str) -> bool:
    is_store_or_clover = _fuzzy_word_in_filename(filename, "store") or _fuzzy_word_in_filename(filename, "clover")
    return _fuzzy_word_in_filename(filename, "tax") and is_store_or_clover


def _is_daily_report(filename: str) -> bool:
    return _fuzzy_word_in_filename(filename, "store") or _fuzzy_word_in_filename(filename, "clover")


def parse_date_arg(value: str) -> dt.date:
    for fmt in ("%d %B %Y", "%B %d %Y", "%B %d, %Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise SystemExit(f"Could not parse --date '{value}'. Try e.g. \"7 September 2026\".")


def build_one_day(
    daily_path: Path,
    tax_path: Path,
    mapping: dict,
    counter: journal.JournalCounter,
    explicit_journal_no: str | None,
    date_override: dt.date | None,
) -> tuple[build.BuildResult, str]:
    daily = extract_clover.extract(daily_path, date_override=date_override)
    tax = extract_clover_tax.extract(
        tax_path, mapping.get("no_tax_labels", ["No Tax"]), date_override=date_override or daily.date
    )

    if daily.date != tax.date:
        raise ClarifyNeeded(
            f"Clover report date ({daily.date}) does not match Tax report date ({tax.date}). "
            "These must be for the same day."
        )

    journal_no = counter.take(explicit_journal_no)
    try:
        result = build.build_entry(daily, tax, mapping, journal_no)
    except ClarifyNeeded:
        # Building failed - the number was already advanced. Roll it back so
        # the next attempt (after a fix) doesn't skip a number silently.
        if not explicit_journal_no:
            counter.set_journal(journal_no)
        raise

    memo_template = mapping.get("description", {}).get("memo_template", "Country Store Daily Revenue {date}")
    text = csv_writer.render(result, memo_template)
    return result, text


def cmd_single(args: argparse.Namespace) -> int:
    mapping = load_mapping()
    counter = journal.JournalCounter(STATE_PATH)
    date_override = parse_date_arg(args.date) if args.date else None

    try:
        result, text = build_one_day(
            Path(args.dayend_report), Path(args.tax_report), mapping, counter,
            args.journal_no, date_override,
        )
    except ClarifyNeeded as exc:
        print(f"STOPPED - no CSV written: {exc}", file=sys.stderr)
        return 1
    except CSVValidationError as exc:
        print(f"STOPPED - CSV failed validation, nothing written: {exc}", file=sys.stderr)
        return 1

    out_dir = OUTPUT_DIR / result.date.isoformat()
    out_csv = out_dir / f"CountryStore_{result.date.isoformat()}_{result.journal_no}.csv"
    try:
        csv_writer.write(result, mapping.get("description", {}).get("memo_template",
                                                                      "Country Store Daily Revenue {date}"), out_csv)
    except CSVValidationError as exc:
        print(f"STOPPED - CSV failed validation, nothing written: {exc}", file=sys.stderr)
        return 1
    _write_flags(result, out_dir)
    _report(result, out_csv)
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    mapping = load_mapping()
    counter = journal.JournalCounter(STATE_PATH)

    files = [p for p in INBOX_DIR.iterdir() if p.is_file()]
    daily_files = [p for p in files if _is_tax_report(p.name) is False and _is_daily_report(p.name)]
    tax_files = [p for p in files if _is_tax_report(p.name)]

    if not daily_files and not tax_files:
        print("No files matching 'Country Store' / 'Country Store Tax' (or 'Clover' / 'CloverTax') "
              "found in inbox/.")
        return 0

    pairs: dict[dt.date, dict[str, Path]] = {}
    unresolved: list[str] = []

    for p in daily_files:
        try:
            d = extract_clover.extract(p)
        except ClarifyNeeded as exc:
            unresolved.append(f"{p.name}: {exc}")
            continue
        pairs.setdefault(d.date, {})["daily"] = p

    for p in tax_files:
        try:
            t = extract_clover_tax.extract(p, mapping.get("no_tax_labels", ["No Tax"]))
        except ClarifyNeeded as exc:
            unresolved.append(f"{p.name}: {exc}")
            continue
        pairs.setdefault(t.date, {})["tax"] = p

    for msg in unresolved:
        print(f"SKIPPED (needs clarification): {msg}", file=sys.stderr)

    any_built = False
    for date_, found in sorted(pairs.items()):
        if "daily" not in found or "tax" not in found:
            missing = "tax report" if "tax" not in found else "Clover report"
            print(f"{date_.isoformat()}: incomplete day, missing {missing} - leaving in inbox/.")
            continue

        try:
            result, _text = build_one_day(found["daily"], found["tax"], mapping, counter, None, date_)
        except ClarifyNeeded as exc:
            print(f"{date_.isoformat()}: STOPPED - no CSV written: {exc}", file=sys.stderr)
            continue
        except CSVValidationError as exc:
            print(f"{date_.isoformat()}: STOPPED - CSV failed validation, nothing written: {exc}", file=sys.stderr)
            continue

        out_dir = OUTPUT_DIR / date_.isoformat()
        out_csv = out_dir / f"CountryStore_{date_.isoformat()}_{result.journal_no}.csv"
        try:
            csv_writer.write(result, mapping.get("description", {}).get("memo_template",
                                                                          "Country Store Daily Revenue {date}"), out_csv)
        except CSVValidationError as exc:
            print(f"{date_.isoformat()}: STOPPED - CSV failed validation, nothing written: {exc}", file=sys.stderr)
            continue
        _write_flags(result, out_dir)
        _report(result, out_csv)

        out_dir.mkdir(parents=True, exist_ok=True)
        for key in ("daily", "tax"):
            src = found[key]
            shutil.move(str(src), str(out_dir / src.name))
        any_built = True

    if not any_built:
        print("No complete day pairs were built.")
    return 0


def _write_flags(result: build.BuildResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    flags_path = out_dir / f"FLAGS_{result.date.isoformat()}.txt"
    with open(flags_path, "w", encoding="utf-8") as f:
        f.write(f"FLAGS - REVIEW BEFORE POSTING ({result.date.isoformat()}, {result.journal_no})\n")
        f.write("=" * 60 + "\n")
        if not result.flags:
            f.write("Clean day - no flags.\n")
        else:
            for flag in result.flags:
                f.write(f"- {flag}\n")


def _report(result: build.BuildResult, out_csv: Path) -> None:
    print(f"Built {result.date.isoformat()} - journal {result.journal_no} -> {out_csv}")
    print(f"  Debits {result.total_debits}  Credits {result.total_credits}  (balanced)")
    if result.flags:
        print("  FLAGS - REVIEW BEFORE POSTING:")
        for flag in result.flags:
            print(f"    - {flag}")
    else:
        print("  Clean day, no flags.")


def cmd_set_journal(args: argparse.Namespace) -> int:
    counter = journal.JournalCounter(STATE_PATH)
    counter.set_journal(args.set_journal)
    print(f"Stored next journal number: {args.set_journal}")
    print("Make sure this matches the real next number confirmed in QBO.")
    return 0


def cmd_reset_journal(args: argparse.Namespace) -> int:
    counter = journal.JournalCounter(STATE_PATH)
    current = counter.peek()
    print(f"This will clear the stored journal number (currently: {current or '(none)'}).")
    typed = input("Type YES to confirm: ").strip()
    if typed != "YES":
        print("Not confirmed - nothing changed.")
        return 1
    counter.reset()
    print("Journal number counter cleared. The next build needs --set-journal or --journal-no.")
    return 0


def main() -> int:
    print(f"Country Store JE builder - build {BUILD_VERSION}")
    parser = argparse.ArgumentParser(description="Country Store Daily Revenue JE builder")
    parser.add_argument("--inbox", action="store_true", help="Build every complete day sitting in inbox/")
    parser.add_argument("--dayend-report", help="Path to a single day's Clover Report")
    parser.add_argument("--tax-report", help="Path to a single day's Clover Tax Report")
    parser.add_argument("--journal-no", help="One-off journal number override (does not touch the stored counter)")
    parser.add_argument("--date", help='Explicit report date, e.g. "7 September 2026" (overrides parsing)')
    parser.add_argument("--set-journal", metavar="JJxxxx", help="Store the next journal number")
    parser.add_argument("--reset-journal", action="store_true", help="Clear the stored journal number (asks to confirm)")
    parser.add_argument("--version", action="store_true", help="Print the build version and exit")

    args = parser.parse_args()

    if args.version:
        return 0
    if args.set_journal:
        return cmd_set_journal(args)
    if args.reset_journal:
        return cmd_reset_journal(args)
    if args.inbox:
        return cmd_inbox(args)
    if args.dayend_report and args.tax_report:
        return cmd_single(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
