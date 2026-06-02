"""kpt pattern #14: Counter-hypothesis (null-hypothesis) generation.

For each primary hypothesis, generate the *opposite* claim, run both through
the verifier, then keep the one that scores higher. Reduces confirmation bias
in the self-learning loop — a hypothesis only "wins" if its negation loses.

Fires only when ``KPT_COUNTER_HYP=1``. Triggered inside
``cycle.py::_process_question`` right before the verifier loop. Intentionally
small surface — no module-level state, returns a dict of {primary_id: counter_h}
so the verifier can compare.
"""
from __future__ import annotations

import copy
import logging
import os
from typing import List

logger = logging.getLogger(__name__)

# Cheap rule-based negation prefixes — avoids any LLM call.
_NEGATION_OPENERS = (
    "It is not the case that ",
    "Contrary to expectation, ",
)


def negate_statement(statement: str) -> str:
    """Return the opposite claim of ``statement`` without calling an LLM.

    Strategy:
      1. If statement contains "increase"/"decrease", swap them.
      2. If contains "is", inject "is not".
      3. Otherwise prepend a negation opener.
    """
    s = statement.strip()
    low = s.lower()
    if " increase" in low and " decrease" not in low:
        return s.replace("increase", "decrease").replace("Increase", "Decrease")
    if " decrease" in low and " increase" not in low:
        return s.replace("decrease", "increase").replace("Decrease", "Increase")
    if " is " in low:
        return s.replace(" is ", " is not ", 1)
    if " are " in low:
        return s.replace(" are ", " are not ", 1)
    return _NEGATION_OPENERS[0] + s[0].lower() + s[1:]


def build_counters(hyps: List) -> List:
    """Return a parallel list of counter-hypotheses (deep-copied + negated)."""
    counters = []
    for h in hyps:
        try:
            ch = copy.copy(h)
            stmt = getattr(h, "statement", None) or getattr(h, "claim", "") or ""
            new_stmt = negate_statement(stmt)
            if hasattr(ch, "statement"):
                ch.statement = new_stmt
            elif hasattr(ch, "claim"):
                ch.claim = new_stmt
            # Mark as counter so verifier / consolidator can see it
            meta = dict(getattr(ch, "metadata", None) or {})
            meta["counter_of"] = getattr(h, "id", None)
            meta["is_counter_hypothesis"] = True
            try:
                ch.metadata = meta
            except Exception:
                pass
            # Counters never carry parent lineage (they are control arms)
            if hasattr(ch, "parent_hypothesis_id"):
                ch.parent_hypothesis_id = None
            # Reset id so DB insert doesn't collide
            if hasattr(ch, "id"):
                ch.id = None
            counters.append(ch)
        except Exception as e:
            logger.warning(f"counter_hypothesis build failed: {e}")
    return counters


def is_enabled() -> bool:
    return os.getenv("KPT_COUNTER_HYP", "0") in ("1", "true", "True")
