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
import shutil
import sys
from pathlib import Path

import yaml

from mprje_cs import extract_clover, extract_clover_tax, build, csv_writer, journal
from mprje_cs.extract_clover import ClarifyNeeded

ROOT = Path(__file__).resolve().parent
MAPPING_PATH = ROOT / "gl_mapping.yaml"
INBOX_DIR = ROOT / "inbox"
OUTPUT_DIR = ROOT / "output"
STATE_PATH = ROOT / "journal_state.json"


def load_mapping() -> dict:
    with open(MAPPING_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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

    out_dir = OUTPUT_DIR / result.date.isoformat()
    out_csv = out_dir / f"CountryStore_{result.date.isoformat()}_{result.journal_no}.csv"
    csv_writer.write(result, mapping.get("description", {}).get("memo_template",
                                                                  "Country Store Daily Revenue {date}"), out_csv)
    _write_flags(result, out_dir)
    _report(result, out_csv)
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    mapping = load_mapping()
    counter = journal.JournalCounter(STATE_PATH)

    files = [p for p in INBOX_DIR.iterdir() if p.is_file()]
    daily_files = [p for p in files if "clovertax" not in p.name.lower() and "clover" in p.name.lower()]
    tax_files = [p for p in files if "clovertax" in p.name.lower()]

    if not daily_files and not tax_files:
        print("No files matching 'Clover' / 'CloverTax' found in inbox/.")
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

        out_dir = OUTPUT_DIR / date_.isoformat()
        out_csv = out_dir / f"CountryStore_{date_.isoformat()}_{result.journal_no}.csv"
        csv_writer.write(result, mapping.get("description", {}).get("memo_template",
                                                                      "Country Store Daily Revenue {date}"), out_csv)
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
    parser = argparse.ArgumentParser(description="Country Store Daily Revenue JE builder")
    parser.add_argument("--inbox", action="store_true", help="Build every complete day sitting in inbox/")
    parser.add_argument("--dayend-report", help="Path to a single day's Clover Report")
    parser.add_argument("--tax-report", help="Path to a single day's Clover Tax Report")
    parser.add_argument("--journal-no", help="One-off journal number override (does not touch the stored counter)")
    parser.add_argument("--date", help='Explicit report date, e.g. "7 September 2026" (overrides parsing)')
    parser.add_argument("--set-journal", metavar="JJxxxx", help="Store the next journal number")
    parser.add_argument("--reset-journal", action="store_true", help="Clear the stored journal number (asks to confirm)")

    args = parser.parse_args()

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
