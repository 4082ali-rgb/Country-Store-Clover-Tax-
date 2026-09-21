# Country Store Daily Revenue JE Automation — Manning Park Resort

## Purpose

Build QuickBooks Online (QBO) journal entry import CSVs from Clover POS
"Sales Overview" reports for the Manning Park Country Store outlet. This
document is the full rule set distilled from manual processing of the
July–August 2026 entries. Follow it exactly; where a report doesn't match
an expected pattern, stop and flag rather than guess silently.

This is the authoritative rule set for this repo. `gl_mapping.yaml` encodes
Section 3 (GL mapping); `mprje_cs/build.py` encodes Section 2 and 4 (the
balance checklist and special cases); `mprje_cs/extract_clover.py` and
`mprje_cs/extract_clover_tax.py` encode Section 1 and 7 (parsing). If a
real report doesn't match what's described here, the code is meant to flag
it rather than guess — update this file once the real rule is confirmed.

---

## 1. Inputs

- A Clover "Sales Overview" report for one calendar day. May arrive as:
  - A PDF (text-based, or sometimes a flat raster "Print to PDF" that
    needs OCR — the code reads text directly via PyMuPDF and only falls
    back to OCR when the PDF has no text layer at all)
  - Pasted webpage text/HTML copy-paste (common for the "Full Report" or
    "Trends" view)
  - A separate "Taxes Report" screenshot/paste showing the GST/PST/Liquor
    Tax breakdown (this is often sent as a follow-up, not part of the
    main PDF)
- The report covers exactly one day, midnight to 11:59 PM.
- A confirmed journal number is either provided explicitly by the user or
  must follow the auto-increment rule (see Section 5).

If a day's PDF has no explicit date header (some "Trends"/"Full Report"
pastes omit it), decode the date from the Clover URL query params
(`startTimestamp`, `endTimestamp`, Unix ms, UTC) and **confirm with the
user before proceeding** — do not assume.

**Real-world PDF quirks confirmed from actual exports (see
`mprje_cs/extract_clover.py`):**
- Every table cell can render on its own line (one value per line), not a
  single-line row — the parser handles both layouts.
- Category/tender names are sometimes truncated with an ellipsis by
  column width ("Non Alcohol..", "Alcohol Bev..."), and "Ice" has been
  seen rendered as "lce" due to a font glyph-mapping quirk in the PDF's
  text layer.
- The date header and other labels can be fully concatenated with zero
  spaces, e.g. `Sep1,202612:00AM-Sep1,202611:59PM`, `Taxes&Fees`.
- Card tenders may be broken out in a separate "Sales By Card Type" table
  (Interac/Visa/MasterCard) distinct from "Tender Types" (which may only
  show Credit Card/Debit Card aggregates + Cash) — the granular table is
  what reconciles to Amount Collected and is what `gl_mapping.yaml` maps.
- The Tax Details table's Total row can have one fewer column than the
  other rows (no "Applicable sales" figure) — the code always takes the
  LAST number in a row as its Net amount, which works either way.

---

## 2. Core balancing principle

Every entry must balance: total Debits == total Credits, penny-exact.
This is non-negotiable and is checked programmatically before any file
is written, never eyeballed.

**Standard structure:**
- **Debits**: tender totals (cards + cash, using **Amount Collected**,
  i.e. post-refund/post-discount figures — not "Sales Total"), plus any
  non-tender debit lines (Gift Certificates redeemed, Room Charge,
  Refunds-Allowances — see Section 4)
- **Credits**: revenue by category (using **Net Sales** per category,
  which is already net of discounts), plus GST/PST/Liquor Tax

**Verification checklist before building any entry — run all of these:**
1. Sum of all revenue category Net Sales == reported total Net Sales
2. Sum of GST + PST + Liquor Tax (from the Tax Details table) == reported
   Taxes & Fees
3. Sum of card tenders (Amount Collected, post-refund) + Cash == reported
   Amount Collected
4. Revenue + Tax == Amount Collected (this is the master check — if this
   doesn't hold, something is being missed or double-counted; do not
   force a plug. Stop and investigate, or ask the user.)

If the Revenue Classes table's own "Total" row for Taxes & Fees disagrees
with the Tax Details table (a recurring Clover rounding/internal
inconsistency — has happened repeatedly, e.g. $503.36 vs $503.87), **the
Tax Details table is the source of truth.** Always cross-check against it
even when the report doesn't explicitly flag a discrepancy.

---

## 3. GL account mapping (Country Store, Class 0030-COUNTRY STORE unless noted)

| Clover Revenue Class | GL Account | Notes |
|---|---|---|
| Souvenirs | 3018 Revenue - Souvenir | |
| Snacks | 3014 Revenue - Snacks | |
| Non Alcoholic Beverages | 3006 Revenue - Non-Alcoholic | |
| Alcohol Beverages | 3003 Revenue - Liquor | |
| Grocery | 3013 Revenue - Grocery | |
| Tobacco | 3017 Revenue - Tobacco | |
| Ice | 3020 Revenue - Ice | |
| Seasonal Items / Hard Goods | 3021 Revenue - Miscellaneous Retail | Description prefix: "Seasonal Items - " |
| Prepared Foods | 3002 Revenue - Food | **Class 0020-PINEWOODS**, not Country Store — confirmed exception |
| Firewood | 3019 Revenue - Firewood - Country Store | Distinct from 3008 Wood Sales (used elsewhere) |
| Unclassified (normal case) | 3029 Miscellaneous Revenue | Description prefix: "Unclassified - "; ONLY when it ties into Net Sales/Amount Collected cleanly — see Section 6 for the exception |

**Tender/payment lines:**

| Tender | GL Account | Notes |
|---|---|---|
| Interac / Debit | 1007 Visa / Mstrcrd / Debit Receivable | Description prefix: "Debit - " |
| Visa | 1007 Visa / Mstrcrd / Debit Receivable | Description prefix: "Visa - " |
| MasterCard | 1007 Visa / Mstrcrd / Debit Receivable | Description prefix: "MasterCard - " |
| Cash | 1002 Petty Cash in safe | No prefix |
| Gift Certificates (redeemed) | 2005 Gift Certificates | Liability account, debit reduces balance |
| Room Charge (guest billed to room folio) | 3051 ROOM CHARGE R/C | **Class 0030-COUNTRY STORE** (confirmed — not an Accommodation-side account); debit line |

**Tax lines (always from the Tax Details table, never the Revenue Classes
table's own total):**

| Tax | GL Account |
|---|---|
| GST (5%) | 2029 GST Charged on Sales |
| PST (7%) | 2035 PST 7% Charged on Sales |
| Liquor Tax (10%) | 2039 PST 10% Liquor Charged on Sales |

"No Tax (0%)" rows never get their own GL line — they're informational
only, already reflected in the revenue category totals.

---

## 4. Special cases

### Gift Certificates tendered
Appears as its own row in Tender Types (e.g. "$6.20"). Debit
2005 Gift Certificates for the amount. Confirmed by user (not a guess).

### Room Charge tendered
Appears as its own row in Tender Types. Debit 3051 ROOM CHARGE R/C,
Class 0030-COUNTRY STORE (confirmed by user — this is the Country
Store side of an inter-outlet transfer; the Accommodation/Roomaster
side will show the offsetting entry separately, out of scope here).

### Refunds (the common, clean case)
Most days with a refund are already fully netted by Clover into:
- Net Sales per category (post-discount, post-refund)
- Net Taxes (Tax Details table's own "Net taxes" column, post-refund)
- Amount Collected by card type (post-refund)

When this is true (verify with the balance checklist in Section 2), **no
separate refund line is needed** — just use the post-refund figures
throughout. Confirmed working this way for July 31, Aug 26, Aug 29,
Aug 31.

### Refunds (the ambiguous/unclean case — Aug 8 precedent)
Occasionally the "Unclassified" revenue category itself goes **negative**
(e.g. -$8.91), representing a refund against a category with no matching
sale that period. When this happens:
1. Do NOT assume the reported "Amount Collected" figure already reflects
   it — check both possible balancing totals (with vs. without the
   negative folded into a separate line) and see which one the user
   confirms against their own source (e.g. actual bank deposit).
2. If ambiguous and the user can't resolve it definitively, use the
   structurally correct accounting treatment: book the negative amount as
   a **debit to "Refunds-Allowances"** (a dedicated Income-type contra
   account in the Chart of Accounts — GL_Code.docx), as its own line,
   rather than folding it into 3029 or another category. This keeps
   Debits = Credits by definition; the resulting total may differ
   slightly from a headline "Amount Collected" figure on the report if
   Clover's own report has an internal inconsistency — flag this
   explicitly rather than silently picking a number.

### "Unclassified" caused by a missing SKU (Aug 7 precedent — EXCLUDE, don't post)
If a large, suspicious "Unclassified" line appears (e.g. $980.00) AND it
does NOT reconcile inside Net Sales or Amount Collected (i.e., summing
the other categories alone already equals the reported Net Sales, and
the Revenue Classes table's own "% Net Sales" total is broken, e.g.
116.70% instead of 100%), this indicates a **missing SKU/Product Code**
in Clover causing that specific item to fall out of the day's totals
entirely, even though it appears as a line in the Revenue Classes table.

In this case:
1. Do NOT credit any revenue account for it — there is no matching debit
   (it's not in Amount Collected either).
2. **Exclude the line entirely** from the day's entry.
3. Flag it clearly for follow-up with Beverly (Country Store manager,
   responsible for Clover product/SKU setup) to assign a proper SKU and
   revenue category.
4. Note that once fixed, the item should flow through correctly in a
   future day's report — it is not lost, just not in *this* day's books.
5. Investigate the specific product name via the Clover "Items" report
   (Reporting → Items, grouped by category) if unsure what the
   Unclassified line actually is — don't guess.

**Rule of thumb to distinguish the two Unclassified cases:** if
Revenue(all categories including Unclassified) + Tax == Amount Collected,
it's the normal case (→ 3029). If Revenue(all categories) + Tax >
Amount Collected by exactly the Unclassified amount, and the categories
excluding Unclassified alone already equal Net Sales, it's the missing-SKU
case (→ exclude, flag for Beverly).

### Partial/unpaid orders (Aug 7 red herring — do not assume this by default)
An "Unpaid Balance" note may appear on some report layouts. This turned
out, on investigation, to be unrelated to the day's actual Unclassified
line in the one case it came up (a coincidental dollar match). Do not
assume a large Unclassified figure is an unpaid balance without
checking — cross-reference against the Clover "Items" report detail to
confirm what the line item actually is before choosing a treatment.

---

## 5. Journal number handling

- Country Store's journal number pool is shared with other outlets via a
  single QBO sequence — gaps between outlets are normal and expected.
- **Default behavior**: once the user gives an explicit journal number,
  auto-increment by 1 for each subsequent Country Store entry without
  asking again, and state the number used in the response.
- **The moment the user provides a new explicit number that breaks the
  running sequence** (e.g. jumping from JJ3148 to JJ3197, or from JJ3228
  to JJ3343), treat that as a full reset: resume auto-incrementing from
  the new number going forward, and don't try to reconcile or explain the
  gap — it just means other outlets used numbers in between.
- Never guess a journal number on your own initiative. If none has been
  given yet, use an obvious placeholder like `JJ_TBD` (not a fabricated
  real-looking number) and flag it prominently.

---

## 6. CSV format requirements (QBO import — hard requirements)

These were the actual root causes of repeated "fewer than two lines /
unbalanced / missing journal numbers" import errors on files that were
otherwise correct and balanced:

1. **`*JournalNo`, `*JournalDate`, and `Memo` must be repeated on every
   single row** of the entry — not just the first row with blanks after,
   despite QBO's own sample template showing blanks after row 1. This was
   the single most costly bug across multiple sessions.
2. **Line endings must be CRLF (`\r\n`)**, not bare LF. QBO's own sample
   template uses CRLF; switching to LF (even for an unrelated fix, like
   removing an em-dash) silently broke imports.
3. **No em-dashes or other non-ASCII characters** in Description fields —
   use plain hyphens (`-`). Confirmed to corrupt QBO's CSV parsing on
   affected rows.
4. **Account names must exactly match QBO's Chart of Accounts spacing**
   (e.g., "Revenue - Food" with spaces around the dash, not "Revenue-Food").
5. **Multi-entry files import more reliably than single-entry files.**
   This was heavily tested: identical, byte-verified, balanced
   single-entry CSVs failed the QBO import repeatedly with "fewer than
   two lines / unbalanced / missing journal numbers" errors, while the
   same content bundled with a second real entry succeeded. Root cause
   was never fully confirmed (see open question below), but the working
   rule is: **when possible, batch two or more days into one file**
   rather than importing one day at a time. Never pad a batch with fake
   placeholder/test rows mixed into real data — if a second real entry
   isn't available yet, it's acceptable to submit a single-entry file
   and flag that it may need bundling if the import fails again.
6. Description field: **never include commas** — reword to avoid them
   entirely (do not rely on CSV quoting to protect embedded commas, since
   QBO's importer appears to naive-split on commas in some cases per
   other outlets' experience).

**CSV column order** (fixed, from QBO's own template):
```
*JournalNo,*JournalDate,Memo,*AccountName,Debits,Credits,Description,Name,Location,Class
```

**Description convention for Country Store specifically** (differs from
other outlets — do not conflate): every single line repeats the full
memo text `Country Store Daily Revenue [Month Day Year]`. Lines needing
disambiguation (the three 1007 tender lines, Seasonal Items, Unclassified)
get a short prefix before the full memo, e.g.:
`Visa - Country Store Daily Revenue August 2 2026`
`Seasonal Items - Country Store Daily Revenue August 2 2026`

This is NOT the same convention used for Bears Den/Pinewoods (plain memo,
no repetition) or other outlets — check `je-rules-and-conventions.md` in
memory before assuming a pattern carries across outlets.

---

## 7. Build workflow (recommended script structure)

For each day:

1. Parse the Clover PDF/paste. If raster-only, rasterize and read
   visually (`mprje_cs/extract_clover.py` does this via PyMuPDF, falling
   back to OCR only when there's no text layer at all) — don't trust a
   text extraction that comes back empty.
2. Extract: Gross Sales, Discounts, Refunds, Net Sales, Taxes & Fees,
   Amount Collected; full Revenue Classes table; full Tax Details table
   (GST/PST/Liquor Tax, request separately if not included — it very
   often isn't in the main PDF); Tender Types and Sales By Card Type
   tables; any Gift Certificate / Room Charge tender rows.
3. Run the four-point balance checklist (Section 2) programmatically
   before writing any file.
4. If it doesn't balance cleanly, work through Section 4's special cases
   in order (Gift Cert / Room Charge / clean refund / Unclassified
   ambiguity / missing-SKU exclusion) rather than forcing a plug number.
   If genuinely stuck, present the numbers and ask the user rather than
   guessing.
5. Map every line per Section 3, apply the journal number per Section 5.
6. Write the CSV per Section 6's exact format requirements. Verify
   programmatically: correct row count, journal number identical on
   every row, CRLF confirmed via raw byte check (not just visual), debits
   == credits to the penny.
7. Deliver the file and a short Flags/Issues section (see Section 8) —
   always, even when everything reconciled cleanly (state "clean day, no
   flags" explicitly rather than omitting the section).

---

## 8. Output conventions

- **Accuracy over speed.** Never silently assume a GL account, a class,
  or a journal number — flag it.
- **Every response ends with a clearly labeled Flags/Issues section**
  when any judgment call, fallback, or placeholder was used. State
  explicitly when a day was clean with nothing to flag.
- **Once a CSV is delivered, do not regenerate it for corrections** —
  the user makes manual adjustments directly in QBO. Only rebuild when
  explicitly asked, or when building a genuinely new day's entry.
- State the journal number used in every response.
- If a day appears to be missing from a sequential batch (e.g. Aug 26–27
  absent between Aug 25 and Aug 28), flag it rather than silently
  skipping.

---

## 9. Open questions / known unresolved issues

- **Root cause of the single-entry-file QBO import failures was never
  conclusively identified.** The working theory (multi-entry files import
  more reliably) is empirically supported but not mechanistically
  understood. If this recurs, consider testing systematically: a
  single-entry file with zero special characters, confirmed CRLF, and a
  fresh/known-good journal number, to isolate whether it's genuinely a
  "must have 2+ entries" QBO quirk or something else entirely (e.g. a
  stale column-mapping cache in the QBO import wizard tied to the
  person's browser session).
- **Aug 8's $8.91 discrepancy** between the structurally-correct balanced
  total ($7,864.74) and Clover's stated "Amount Collected" ($7,855.83)
  was never fully reconciled — the user was not certain which was
  correct. If this pattern recurs, it may be worth pulling the actual
  bank deposit or Clover's underlying transaction-level export to settle
  it definitively rather than re-guessing each time.
- **Propane Firepit / Camping Supplies SKU** (Aug 7 missing-SKU case) was
  flagged for Beverly but not confirmed resolved as of the last session —
  check whether it recurs in later August/September reports before
  assuming it's been fixed.

---

## 10. Implementation notes for this repo specifically

- `gl_mapping.yaml` is the only place account/Class/prefix rules live —
  never hardcode an account name in Python.
- `mprje_cs/extract_clover.py` and `mprje_cs/extract_clover_tax.py` do all
  PDF/text parsing; both are built to tolerate the real-world PDF quirks
  listed in Section 1, and raise `ClarifyNeeded` (never guess) when a
  report doesn't match anything recognized — the error message includes a
  preview of the actual extracted text so a new quirk can be diagnosed
  from the error alone.
- `mprje_cs/build.py` runs the balance checklist and special cases, and
  refuses to produce an entry that doesn't balance to the penny.
- `mprje_cs/csv_writer.py` writes and then re-reads the CSV from disk to
  verify CRLF, column count, balance, and Class-never-blank before
  trusting it's correct.
- Known real-world Clover text-extraction artifacts are recorded directly
  in `extract_clover.py` (e.g. `_CATEGORY_ALIASES = {"lce": "Ice"}`) —
  add new ones there, with a comment noting they're a confirmed artifact,
  not a guess.
- `mprje_cs/__init__.py`'s `PACKAGE_VERSION` and `countrystore_je.py`'s
  `BUILD_VERSION` are checked against each other on every run and must be
  bumped together on every code change - a mismatch means the two came
  from different downloads/extractions, and the program refuses to run
  rather than silently executing stale logic under a fresh-looking banner.
- The generic "this code doesn't recognize this line" placeholder account
  is **3001 Revenue** (`unmapped_default` in `gl_mapping.yaml`), distinct
  from 3029 Miscellaneous Revenue (which is specifically "Unclassified,"
  a real, expected Clover category, not an unknown one). The placeholder
  is always labeled with the raw unrecognized name in the Description, so
  it's easy to find and fix by hand.
- Seasonal Items and Unclassified are **always** labeled with their prefix
  in the Description, regardless of whether their account happens to
  repeat elsewhere in that day's entry (`always_label: true` on those two
  entries in `gl_mapping.yaml`) - confirmed against real QBO exports for
  Aug 30 and Aug 31 2026, both of which show the prefix even on a day
  where no other line shares that account.
