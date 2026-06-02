"""Skill patch constraint gates — reject malformed patches before shadow.

Pattern lifted from NousResearch/hermes-agent-self-evolution
evolution/core/constraints.py (~174 LOC), adapted for Dash's tool-patch +
dashboard-skill-patch lifecycle (`dash_tool_patches`, `dash_skill_patches`).

Why:
  LLM-proposed patches can be malformed:
    - 50K-char ramble that doesn't even parse
    - Empty/near-empty (gutted instructions)
    - Lost critical sections (## Rules, ## Examples)
    - Bloated 5× larger than baseline (slow + expensive at every chat)
    - Lost frontmatter keys that downstream code depends on
  Shadow validation catches accuracy regressions BUT burns LLM $$$ first.
  Structural gates run in microseconds, reject obvious garbage before the
  shadow call. Defense-in-depth + cost saver.

Usage:
  result = validate_patch(old, new, kind="tool")  # or "skill"
  if not result.ok:
      log_rejection(result.issues)
      continue  # skip shadow + apply
  shadow_result = shadow_validate(...)
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────────────────
# Per-kind constraint defaults
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class PatchConstraints:
    max_size_bytes: int = 8000             # absolute upper bound
    min_size_bytes: int = 50               # prevent gut-to-empty
    max_growth_ratio: float = 2.5          # candidate / baseline upper bound
    min_growth_ratio: float = 0.3          # candidate / baseline lower bound
    required_sections: list[str] = field(default_factory=list)  # markdown headers
    required_frontmatter_keys: list[str] = field(default_factory=list)
    forbidden_phrases: list[str] = field(default_factory=lambda: [
        "TODO", "FIXME", "[placeholder]", "<insert ", "<your "
    ])

# Tool patches are SHORT descriptions (one-liners). Skill patches are LONG
# markdown blocks. Different bounds.
_TOOL_CONSTRAINTS = PatchConstraints(
    max_size_bytes=2000,
    min_size_bytes=20,
    max_growth_ratio=3.0,
    min_growth_ratio=0.2,
    required_sections=[],
    required_frontmatter_keys=[],
)

_SKILL_CONSTRAINTS = PatchConstraints(
    max_size_bytes=8000,
    min_size_bytes=200,
    max_growth_ratio=2.0,
    min_growth_ratio=0.4,
    required_sections=[],  # opt-in per-skill via validate_patch(required_sections=...)
    required_frontmatter_keys=[],
)


# ───────────────────────────────────────────────────────────────────────────
# Result type
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    ok: bool
    issues: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Best-effort YAML-frontmatter parse (key: value lines only)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


# ───────────────────────────────────────────────────────────────────────────
# Public API
# ───────────────────────────────────────────────────────────────────────────

def validate_patch(
    original: str | None,
    candidate: str | None,
    *,
    kind: Literal["tool", "skill"] = "tool",
    required_sections: list[str] | None = None,
    required_frontmatter_keys: list[str] | None = None,
    constraints: PatchConstraints | None = None,
) -> ValidationResult:
    """Run all gates. Fail-fast on first severe issue, collect all warnings.

    Returns ValidationResult.ok == True only if every gate passes.
    Empty/None original is allowed (treated as 1-byte baseline for ratio calc).
    """
    issues: list[str] = []
    stats: dict = {}

    if not isinstance(candidate, str):
        return ValidationResult(ok=False, issues=["candidate_not_a_string"], stats={})

    cons = constraints or (_TOOL_CONSTRAINTS if kind == "tool" else _SKILL_CONSTRAINTS)
    if required_sections is not None:
        cons.required_sections = required_sections
    if required_frontmatter_keys is not None:
        cons.required_frontmatter_keys = required_frontmatter_keys

    cand_size = len(candidate.encode("utf-8"))
    orig_size = max(len((original or "").encode("utf-8")), 1)
    growth = cand_size / orig_size

    stats["candidate_bytes"] = cand_size
    stats["original_bytes"] = orig_size
    stats["growth_ratio"] = round(growth, 3)

    # Gate 1: absolute size ceiling
    if cand_size > cons.max_size_bytes:
        issues.append(f"too_large: {cand_size}b > max {cons.max_size_bytes}b")

    # Gate 2: absolute size floor (prevent gut-to-empty)
    if cand_size < cons.min_size_bytes:
        issues.append(f"too_small: {cand_size}b < min {cons.min_size_bytes}b")

    # Gate 3: growth ratio bounds (only meaningful when original is non-trivial)
    if original and len(original) >= 50:
        if growth > cons.max_growth_ratio:
            issues.append(f"growth_too_high: {growth:.2f}x > max {cons.max_growth_ratio}x")
        if growth < cons.min_growth_ratio:
            issues.append(f"growth_too_low: {growth:.2f}x < min {cons.min_growth_ratio}x")

    # Gate 4: frontmatter integrity (only for skill kind w/ requirements)
    if cons.required_frontmatter_keys:
        fm = _parse_frontmatter(candidate)
        stats["frontmatter_keys"] = sorted(fm.keys())
        for key in cons.required_frontmatter_keys:
            if key not in fm:
                issues.append(f"missing_frontmatter: {key}")

    # Gate 5: required section markers (markdown headings)
    if cons.required_sections:
        present = []
        missing = []
        for section in cons.required_sections:
            if section in candidate:
                present.append(section)
            else:
                missing.append(section)
        stats["sections_present"] = present
        if missing:
            issues.append(f"missing_sections: {', '.join(missing)}")

    # Gate 6: forbidden placeholders (catches LLM TODO/FIXME emissions)
    forbidden_found = [p for p in cons.forbidden_phrases if p in candidate]
    if forbidden_found:
        issues.append(f"forbidden_phrases: {', '.join(forbidden_found)}")

    # Gate 7: no-op detection (candidate == original, useless patch)
    if original and candidate.strip() == original.strip():
        issues.append("no_op_patch: candidate identical to original")

    return ValidationResult(ok=(len(issues) == 0), issues=issues, stats=stats)


# Disable switch for the rare case a sysadmin wants to ship without gates.
_DISABLED = os.getenv("SKILL_PATCH_CONSTRAINTS_DISABLED", "0") == "1"

def is_disabled() -> bool:
    return _DISABLED
