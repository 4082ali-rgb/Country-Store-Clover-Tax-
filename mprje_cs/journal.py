"""Journal-number counter with an explicit set/reset workflow.

Generic on purpose - knows nothing about Country Store specifically, only
about "a stored next number that advances by one on every successful build
and is never invented from nothing."
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class JournalCounterError(RuntimeError):
    pass


class JournalCounter:
    def __init__(self, state_path: Path):
        self.state_path = state_path

    def _load(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def peek(self) -> Optional[str]:
        """Return the currently stored next journal number, or None."""
        return self._load().get("next_journal_no")

    def set_journal(self, journal_no: str) -> None:
        """Explicitly store the next journal number (user confirmed in QBO first)."""
        journal_no = journal_no.strip()
        if not journal_no:
            raise JournalCounterError("Journal number cannot be blank.")
        self._save({"next_journal_no": journal_no})

    def reset(self) -> None:
        """Clear the stored number. Caller is responsible for confirming with the user first."""
        self._save({})

    def take(self, explicit_journal_no: Optional[str] = None) -> str:
        """Return the journal number to use for a build, advancing the stored counter.

        If explicit_journal_no is given, use it directly and do NOT touch the
        stored counter (a one-off override, same as Accommodation).
        Otherwise, use the stored number, advance it by one, and persist that.
        Raises if nothing is stored and no explicit number was given - a
        number is never guessed.
        """
        if explicit_journal_no:
            return explicit_journal_no.strip()

        stored = self.peek()
        if not stored:
            raise JournalCounterError(
                "No journal number is stored and none was given. "
                "Run --set-journal JJxxxx first (confirm the real next number in QBO), "
                "or pass --journal-no JJxxxx for a one-off build."
            )
        state = self._load()
        state["next_journal_no"] = _increment(stored)
        self._save(state)
        return stored


def _increment(journal_no: str) -> str:
    """Increment the trailing digit run of a journal number, preserving prefix and zero-padding.

    e.g. "JJ3148" -> "JJ3149", "JJ0099" -> "JJ0100"
    """
    prefix = ""
    digits = journal_no
    i = len(journal_no)
    while i > 0 and journal_no[i - 1].isdigit():
        i -= 1
    prefix = journal_no[:i]
    digits = journal_no[i:]
    if not digits:
        raise JournalCounterError(
            f"Cannot auto-increment journal number '{journal_no}' - no trailing digits found."
        )
    width = len(digits)
    incremented = str(int(digits) + 1).zfill(width)
    return f"{prefix}{incremented}"
