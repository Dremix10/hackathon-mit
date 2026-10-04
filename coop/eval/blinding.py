"""CI blinding check (protocol §4).

Grep every file under ``runs/<id>/`` except ``sealed.json`` for the arm
labels, and for any ``template_map`` key used as anything other than a
``payload.template_id`` value.

Assumption for the arm label ``null``: a raw grep matches JSON ``null``
(``temperature``, absent insider) and cannot be used. This check flags the
quoted JSON string ``"null"`` and the key ``recruiter_objective``. Bare
JSON null is allowed. ``resource_capture``, ``blame_avoidance``, and
``loyalty`` are matched as case-sensitive substrings, including in the
directory name.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from coop.eval.contract import SEALED_NAME

_SUBSTRING_ARMS = ("resource_capture", "blame_avoidance", "loyalty")
_QUOTED_NULL = re.compile(r'"null"')
_OBJECTIVE_KEY = "recruiter_objective"


def _template_ids(sealed_path: Path) -> list[str]:
    if not sealed_path.is_file():
        return []
    try:
        sealed = json.loads(sealed_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    mapping = sealed.get("template_map") if isinstance(sealed, dict) else None
    if not isinstance(mapping, dict):
        return []
    return [key for key in mapping if isinstance(key, str) and key]


def _strip_legitimate_template_ids(text: str, template_ids: list[str]) -> str:
    """Drop template ids that appear only as ``template_id`` values."""
    cleaned = text
    for template_id in template_ids:
        cleaned = re.sub(
            rf'("template_id"\s*:\s*"){re.escape(template_id)}(")',
            r'\1\2',
            cleaned,
        )
    return cleaned


def _inside_longer_hex(text: str, start: int, length: int) -> bool:
    """True when the hit sits inside a longer hex token (a hash)."""
    before = text[start - 1] if start > 0 else ""
    after_at = start + length
    after = text[after_at] if after_at < len(text) else ""
    hexdigits = set("0123456789abcdefABCDEF")
    return before in hexdigits or after in hexdigits


def blinding_problems(run_dir: Path) -> list[str]:
    """Problems for one run directory. Empty means the directory is blind."""
    run_dir = Path(run_dir)
    problems: list[str] = []
    for arm in _SUBSTRING_ARMS:
        if arm in run_dir.name:
            problems.append(f"{run_dir.name}: directory name contains arm string {arm!r}")
    if not run_dir.is_dir():
        problems.append(f"{run_dir}: not a directory")
        return problems
    template_ids = _template_ids(run_dir / SEALED_NAME)
    files = [path for path in sorted(run_dir.rglob("*")) if path.is_file() and path.name != SEALED_NAME]
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"{path.name}: not utf-8; blinding grep skipped the bytes")
            continue
        rel = path.name
        for arm in _SUBSTRING_ARMS:
            if arm in text:
                problems.append(f"{rel}: contains arm string {arm!r}")
        if _QUOTED_NULL.search(text):
            problems.append(f'{rel}: contains quoted arm string "null"')
        if _OBJECTIVE_KEY in text:
            problems.append(f"{rel}: contains { _OBJECTIVE_KEY }")
        if template_ids:
            remainder = _strip_legitimate_template_ids(text, template_ids)
            for template_id in template_ids:
                start = 0
                while True:
                    found = remainder.find(template_id, start)
                    if found < 0:
                        break
                    if not _inside_longer_hex(remainder, found, len(template_id)):
                        problems.append(
                            f"{rel}: template_map key {template_id!r} appears outside template_id"
                        )
                        break
                    start = found + len(template_id)
    return problems
