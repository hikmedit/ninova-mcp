"""Parser and evaluator for ITU's course prerequisite expressions.

OBS publishes prerequisites as a Turkish boolean expression over minimum grades::

    ( BLG 252 MIN. DD Veya BLG 252E MIN. DD ) Ve ( MAT 281 MIN. DD Veya MAT 281E MIN. DD )

``Ve`` is AND, ``Veya`` is OR, and AND binds tighter than OR. Each atom is a
course code plus the minimum letter grade that satisfies it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# A course code is a 2-4 letter branch, 3-4 digits, and an optional suffix.
# The suffix alternation is explicit and guarded against a following lowercase
# letter because OBS renders some cells with the code glued to the course name
# ("YZV 231Veri Bilimleri..."), where a greedy suffix would swallow the name.
COURSE_CODE_PATTERN = r"[A-ZÇĞİÖŞÜ]{2,4}\s*\d{3,4}(?:EL|E|L|A)?(?![a-zçğıöşü])"
COURSE_CODE_RE = re.compile(COURSE_CODE_PATTERN)

TOKEN_RE = re.compile(
    rf"""
    (?P<lparen>\() |
    (?P<rparen>\)) |
    (?P<atom>{COURSE_CODE_PATTERN}\s+MIN\.\s*[A-Z]{{2}}\+?) |
    (?P<or>\bVeya\b) |
    (?P<and>\bVe\b) |
    (?P<space>\s+)
    """,
    re.VERBOSE,
)
ATOM_RE = re.compile(
    rf"^(?P<code>{COURSE_CODE_PATTERN})\s+MIN\.\s*(?P<grade>[A-Z]{{2}}\+?)$"
)


class PrerequisiteParseError(ValueError):
    """The expression did not match the grammar OBS publishes."""


def normalize_code(code: str) -> str:
    """``"YZV201E"``/``"yzv  201e"`` -> ``"YZV 201E"``."""
    cleaned = re.sub(r"\s+", "", (code or "").strip().upper())
    match = re.match(r"^([A-ZÇĞİÖŞÜ]{2,4})(\d{3,4}[A-Z]*)$", cleaned)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return re.sub(r"\s+", " ", (code or "").strip().upper())


def base_code(code: str) -> str:
    """Collapse a course code onto its language-independent form.

    ITU publishes the Turkish and English variants of one course under codes
    that differ only by an ``E``: ``BBF 201`` / ``BBF 201E``, ``FIZ 101L`` /
    ``FIZ 101EL``. Prerequisite expressions list both variants explicitly, so
    comparisons are done on the collapsed form. A trailing ``A`` (``ING 112A``)
    is a real section marker, not a language marker, and is left alone.
    """
    normalized = normalize_code(code)
    match = re.match(r"^([A-ZÇĞİÖŞÜ]{2,4} \d{3,4})(EL|E|L|A)?$", normalized)
    if match is None:
        return normalized
    suffix = match.group(2) or ""
    if suffix == "E":
        suffix = ""
    elif suffix == "EL":
        suffix = "L"
    return f"{match.group(1)}{suffix}"


@dataclass(slots=True)
class Atom:
    code: str
    min_grade: str

    def describe(self) -> str:
        return f"{self.code} (min {self.min_grade})"


@dataclass(slots=True)
class Node:
    operator: str  # "and" | "or"
    children: list[Any] = field(default_factory=list)


def tokenize(expression: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    text = expression.strip()
    while position < len(text):
        match = TOKEN_RE.match(text, position)
        if match is None:
            raise PrerequisiteParseError(
                f"Unrecognised text at position {position}: {text[position:position + 30]!r}"
            )
        kind = match.lastgroup
        if kind != "space":
            tokens.append((kind, match.group().strip()))
        position = match.end()
    return tokens


def parse(expression: str) -> Any | None:
    """Parse an expression into an :class:`Atom` / :class:`Node` tree."""
    if not expression or not expression.strip():
        return None
    tokens = tokenize(expression)
    if not tokens:
        return None
    node, index = _parse_or(tokens, 0)
    if index != len(tokens):
        raise PrerequisiteParseError(f"Trailing tokens after position {index}: {tokens[index:]}")
    return node


def _parse_or(tokens: list[tuple[str, str]], index: int) -> tuple[Any, int]:
    children = []
    node, index = _parse_and(tokens, index)
    children.append(node)
    while index < len(tokens) and tokens[index][0] == "or":
        node, index = _parse_and(tokens, index + 1)
        children.append(node)
    return (children[0] if len(children) == 1 else Node("or", children)), index


def _parse_and(tokens: list[tuple[str, str]], index: int) -> tuple[Any, int]:
    children = []
    node, index = _parse_factor(tokens, index)
    children.append(node)
    while index < len(tokens) and tokens[index][0] == "and":
        node, index = _parse_factor(tokens, index + 1)
        children.append(node)
    return (children[0] if len(children) == 1 else Node("and", children)), index


def _parse_factor(tokens: list[tuple[str, str]], index: int) -> tuple[Any, int]:
    if index >= len(tokens):
        raise PrerequisiteParseError("Expression ended unexpectedly.")
    kind, value = tokens[index]
    if kind == "lparen":
        node, index = _parse_or(tokens, index + 1)
        if index >= len(tokens) or tokens[index][0] != "rparen":
            raise PrerequisiteParseError("Unbalanced parenthesis.")
        return node, index + 1
    if kind == "atom":
        match = ATOM_RE.match(value)
        if match is None:
            raise PrerequisiteParseError(f"Malformed atom: {value!r}")
        return Atom(normalize_code(match.group("code")), match.group("grade")), index + 1
    raise PrerequisiteParseError(f"Unexpected token {value!r}.")


def iter_atoms(node: Any) -> list[Atom]:
    if node is None:
        return []
    if isinstance(node, Atom):
        return [node]
    atoms: list[Atom] = []
    for child in node.children:
        atoms.extend(iter_atoms(child))
    return atoms


def evaluate(
    node: Any,
    passed: dict[str, float],
    grade_points: dict[str, float],
) -> dict[str, Any]:
    """Evaluate the tree against ``{course code: earned grade points}``.

    Returns the verdict plus the atoms that were and were not satisfied, so a
    caller can explain *why* a course is blocked rather than only that it is.
    """
    if node is None:
        return {"satisfied": True, "satisfied_by": [], "missing": [], "detail": None}

    if isinstance(node, Atom):
        required = grade_points.get(node.min_grade)
        # `passed` is keyed by collapsed code so TR/EN variants match each other.
        earned = passed.get(base_code(node.code))
        ok = earned is not None and (required is None or earned >= required)
        entry = {"code": node.code, "min_grade": node.min_grade, "label": node.describe()}
        return {
            "satisfied": ok,
            "satisfied_by": [entry] if ok else [],
            "missing": [] if ok else [entry],
            "detail": {"type": "course", "code": node.code, "min_grade": node.min_grade, "met": ok},
        }

    results = [evaluate(child, passed, grade_points) for child in node.children]
    if node.operator == "and":
        satisfied = all(result["satisfied"] for result in results)
    else:
        satisfied = any(result["satisfied"] for result in results)

    satisfied_by: list[str] = []
    missing: list[str] = []
    for result in results:
        satisfied_by.extend(result["satisfied_by"])
        if not satisfied:
            missing.extend(result["missing"])

    return {
        "satisfied": satisfied,
        "satisfied_by": satisfied_by,
        # An unmet OR reports every branch; an unmet AND only its failing branches.
        "missing": missing,
        "detail": {"type": node.operator, "met": satisfied, "children": [r["detail"] for r in results]},
    }
