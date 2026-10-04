"""Blind message coding.

The coder sees message text only. Condition, mode, insider id, and sealed
labels are not arguments. Hand labels live in the CSV template; this module
does not read them.
"""

from __future__ import annotations

from coop.analysis.lexicon import MESSAGE_LABELS, code_message

__all__ = ["MESSAGE_LABELS", "code_message", "code_messages"]


def code_messages(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """``rows`` items need ``message_id`` and ``text`` only."""
    coded = []
    for row in rows:
        if "condition" in row or "mode" in row or "recruiter_objective" in row:
            raise ValueError("message coding must not receive condition or arm fields")
        coded.append({"message_id": row["message_id"], "label": code_message(row["text"])})
    return coded
