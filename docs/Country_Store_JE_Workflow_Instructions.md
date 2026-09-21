# Country Store Daily Revenue JE Automation — Workflow Instructions

This is the filled-in version of build-instructions sections 3 and 4 for the
Country Store outlet (Manning Park Resort), distilled from manual
processing of the July–August 2026 entries. It is the source of truth the
code in this repo is built against — `gl_mapping.yaml` encodes section 3;
`mprje_cs/build.py` encodes section 4 (balance checklist + special cases).

If a report doesn't match a pattern described here, the tool flags it
rather than guessing — update this document (and `gl_mapping.yaml`) once
the real rule is confirmed, the same way Accommodation's workflow doc grew
over its first few weeks of real use.

---

## 1. Inputs

- A Clover "Sales Overview" report for one calendar day (midnight to
  11:59 PM), as a text-based PDF, a raster "Print to PDF" (needs OCR), or
  pasted webpage text/HTML.
- A separate Clover "Taxes Report" (GST/PST/Liquor Tax breakdown) for the
  same day — often sent as a follow-up, not part of the main PDF.
- A confirmed journal number, either explicit or from the stored counter.
- If a report has no explicit date header, the date is decoded from the
  Clover URL's `startTimestamp` query param (Unix ms, UTC) — the tool
  always flags this and requires `--date` to confirm before building.

## 2. Core balancing principle

Debits must equal Credits to the penny, checked programmatically, never
eyeballed.

- **Debits**: tender totals (cards + cash, using Amount Collected —
  post-refund/post-discount), plus Gift Certificates redeemed, Room
  Charge, and Refunds-Allowances when the ambiguous-refund case applies.
- **Credits**: revenue by category (Net Sales, already net of discounts),
  plus GST/PST/Liquor Tax.

**Four-point balance checklist, run before every build:**
1. Sum of all revenue category Net Sales == reported total Net Sales.
2. Sum of GST + PST + Liquor Tax (Tax Details table) == reported Taxes &
   Fees.
3. Sum of card tenders (Amount Collected, post-refund) + Cash == reported
   Amount Collected.
4. Revenue + Tax == Amount Collected (the master check — if this fails,
   something is missing or double-counted; never force a plug).

If the Revenue Classes table's own Taxes & Fees total disagrees with the
Tax Details table, **the Tax Details table is the source of truth** — the
code always cross-checks against it, whether or not the report flags a
discrepancy.

## 3. GL account mapping

See `gl_mapping.yaml` for the authoritative, editable table. Summary:

| Clover Revenue Class | GL Account | Notes |
|---|---|---|
| Souvenirs | 3018 Revenue - Souvenir | |
| Snacks | 3014 Revenue - Snacks | |
| Non Alcoholic Beverages | 3006 Revenue - Non-Alcoholic | |
| Alcohol Beverages | 3003 Revenue - Liquor | |
| Grocery | 3013 Revenue - Grocery | |
| Tobacco | 3017 Revenue - Tobacco | |
| Ice | 3020 Revenue - Ice | |
| Seasonal Items / Hard Goods | 3021 Revenue - Miscellaneous Retail | Prefix "Seasonal Items - " |
| Prepared Foods | 3002 Revenue - Food | Class 0020-PINEWOODS, confirmed exception |
| Firewood | 3019 Revenue - Firewood - Country Store | Distinct from 3008 Wood Sales |
| Unclassified (normal case) | 3029 Miscellaneous Revenue | Prefix "Unclassified - ", always shown (confirmed against real QBO exports); only when it ties into Net Sales/Amount Collected cleanly |

Any line this code genuinely doesn't recognize (a brand-new Clover
category, tender, or tax) posts to **3001 Revenue** (`unmapped_default` in
`gl_mapping.yaml`) instead — distinct from 3029, which is specifically
"Unclassified," a real, expected category. The 3001 placeholder always
carries the raw unrecognized name plus a `[FIRST-APPEARANCE CODE ...]`
flag in the Description, so it's easy to find and fix by hand in QBO or to
add a real mapping for in this file.

**Tender/payment lines:**

| Tender | GL Account | Notes |
|---|---|---|
| Interac / Debit | 1007 Visa / Mstrcrd / Debit Receivable | Prefix "Debit - " |
| Visa | 1007 Visa / Mstrcrd / Debit Receivable | Prefix "Visa - " |
| MasterCard | 1007 Visa / Mstrcrd / Debit Receivable | Prefix "MasterCard - " |
| Cash | 1002 Petty Cash in safe | No prefix |
| Gift Certificates (redeemed) | 2005 Gift Certificates | Liability account, debit reduces balance |
| Room Charge | 3051 ROOM CHARGE R/C | Class 0030-COUNTRY STORE (confirmed), debit line |

**Tax lines** (always from Tax Details, never the Revenue Classes table's
own total):

| Tax | GL Account |
|---|---|
| GST (5%) | 2029 GST Charged on Sales |
| PST (7%) | 2035 PST 7% Charged on Sales |
| Liquor Tax (10%) | 2039 PST 10% Liquor Charged on Sales |

"No Tax (0%)" rows never get their own GL line.

## 4. Special cases (handled in `mprje_cs/build.py`)

- **Gift Certificates tendered**: debit 2005 Gift Certificates.
- **Room Charge tendered**: debit 3051 ROOM CHARGE R/C, Class
  0030-COUNTRY STORE.
- **Refunds (clean case)**: most days are already fully netted by Clover
  into Net Sales, Net Taxes, and Amount Collected — use the post-refund
  figures throughout, no separate line.
- **Refunds (ambiguous case, Aug 8 precedent)**: if "Unclassified" goes
  negative, book it as a debit to Refunds-Allowances rather than folding
  it into 3029 — flagged every time this fires.
- **Missing-SKU case (Aug 7 precedent)**: if a large Unclassified line
  does NOT reconcile inside Net Sales (other categories alone already
  equal Net Sales, and the report's own %-of-Net-Sales total is broken),
  exclude it entirely and flag it for Beverly (Country Store manager) to
  assign a proper SKU/category. Never credit a revenue account with no
  matching debit.
- **Country Store's City-Ledger equivalent**: none identified so far.
  Room Charge is the closest analog (an inter-outlet transfer, not an
  unresolved report total) and is handled as an ordinary debit line, not
  a plug. If a genuine unresolved-total problem turns up, name it here
  before encoding a rule for it.

## 5. Journal number handling

- Country Store's journal number pool is shared with other outlets via a
  single QBO sequence — gaps between outlets are normal.
- Once a number is confirmed via `--set-journal`, every successful build
  auto-increments it by one.
- A new explicit number that breaks the running sequence is a full reset
  — resume auto-incrementing from there, don't try to reconcile the gap.
- Never guessed on its own initiative — `--set-journal` or a one-off
  `--journal-no` is required before the first build.

## 6. CSV format requirements (QBO import)

1. `*JournalNo`, `*JournalDate`, and `Memo` repeated on every row.
2. CRLF line endings, verified via raw byte check.
3. No em-dashes or other non-ASCII characters in Description — plain
   hyphens only.
4. Account names match the QBO Chart of Accounts exactly (spacing
   included).
5. Multi-entry files have historically imported more reliably than
   single-entry files — when possible, batch two or more days into one
   file (`--inbox` naturally does this when multiple complete days are
   sitting in `inbox/`). A single-entry file is acceptable when a second
   day isn't available yet; flag that it may need bundling if the import
   fails.
6. Description: never include commas — reworded instead of relying on CSV
   quoting.

**Description convention for Country Store**: every line repeats the full
memo `Country Store Daily Revenue [Month Day Year]`. Lines needing
disambiguation (the three 1007 tender lines, Seasonal Items, Unclassified,
Refunds-Allowances) get a short prefix before the full memo, e.g.
`Visa - Country Store Daily Revenue August 2 2026`.

## 7. Open questions / known unresolved issues

- Root cause of the single-entry-file QBO import failures was never
  conclusively identified — the multi-entry workaround is empirically
  supported but not mechanistically understood.
- Aug 8's $8.91 discrepancy between the structurally-correct balanced
  total and Clover's stated Amount Collected was never fully reconciled.
- Propane Firepit / Camping Supplies SKU (Aug 7 missing-SKU case) was
  flagged for Beverly but not confirmed resolved — check whether it
  recurs in later reports before assuming it's fixed.
