"""
Eval Runner
===========

Unified runner for all Dash eval categories.

Usage:
    python -m evals
    python -m evals --category security
    python -m evals --verbose
"""

from __future__ import annotations

import importlib
import sys
import time
from typing import Literal

from agno.eval.accuracy import AccuracyEval
from agno.eval.agent_as_judge import AgentAsJudgeEval
from agno.eval.reliability import ReliabilityEval

from evals import CATEGORIES, JUDGE_MODEL


def _get_team():
    """Lazy import to avoid loading the team until needed."""
    from dash.team import dash

    return dash


# ---------------------------------------------------------------------------
# Runners (one per eval type)
# ---------------------------------------------------------------------------


def run_judge_cases(
    cases: list[str],
    criteria: str,
    category: str,
    scoring: Literal["numeric", "binary"],
    verbose: bool = False,
) -> list[dict]:
    """Run AgentAsJudgeEval cases (binary or numeric)."""
    team = _get_team()
    judge = AgentAsJudgeEval(
        name=f"Dash {category}",
        criteria=criteria,
        scoring_strategy=scoring,
        model=JUDGE_MODEL,
    )

    results: list[dict] = []
    for i, question in enumerate(cases, 1):
        print(f"  [{i}/{len(cases)}] {category}: {question[:60]}...")
        start = time.time()
        try:
            run_result = team.run(question)
            response = run_result.content or ""
            duration = round(time.time() - start, 2)

            eval_result = judge.run(input=question, output=response)
            passed = eval_result is not None and eval_result.pass_rate == 100.0

            result: dict = {
                "question": question,
                "category": category,
                "status": "PASS" if passed else "FAIL",
                "duration": duration,
            }
            if not passed and eval_result and eval_result.results:
                result["reason"] = eval_result.results[0].reason
            if verbose:
                result["response_preview"] = response[:200]
        except Exception as e:
            result = {
                "question": question,
                "category": category,
                "status": "ERROR",
                "reason": str(e),
                "duration": round(time.time() - start, 2),
            }
        results.append(result)
        _print_status(result, verbose)
    return results


def run_reliability_cases(
    cases: list[dict],
    category: str,
    verbose: bool = False,
) -> list[dict]:
    """Run ReliabilityEval cases (expected tool calls)."""
    team = _get_team()
    results: list[dict] = []

    for i, case in enumerate(cases, 1):
        question = case["input"]
        expected_tools = case["expected_tools"]
        print(f"  [{i}/{len(cases)}] {category}: {question[:60]}...")
        start = time.time()
        try:
            run_result = team.run(question)
            duration = round(time.time() - start, 2)

            eval_result = ReliabilityEval(
                name=f"Routing: {question[:40]}",
                team_response=run_result,
                expected_tool_calls=expected_tools,
            ).run()

            passed = eval_result is not None and eval_result.eval_status == "PASSED"
            result: dict = {
                "question": question,
                "category": category,
                "status": "PASS" if passed else "FAIL",
                "duration": duration,
            }
            if not passed and eval_result:
                result["reason"] = f"expected {expected_tools}, failed: {eval_result.failed_tool_calls}"
            if verbose:
                result["response_preview"] = (run_result.content or "")[:200]
        except Exception as e:
            result = {
                "question": question,
                "category": category,
                "status": "ERROR",
                "reason": str(e),
                "duration": round(time.time() - start, 2),
            }
        results.append(result)
        _print_status(result, verbose)
    return results


def run_accuracy_cases(
    cases: list[dict],
    category: str,
    verbose: bool = False,
) -> list[dict]:
    """Run AccuracyEval cases (expected output comparison)."""
    team = _get_team()
    results: list[dict] = []

    for i, case in enumerate(cases, 1):
        question = case["input"]
        expected = case["expected_output"]
        guidelines = case.get("guidelines")
        print(f"  [{i}/{len(cases)}] {category}: {question[:60]}...")
        start = time.time()
        try:
            run_result = team.run(question)
            response = run_result.content or ""
            duration = round(time.time() - start, 2)

            eval_obj = AccuracyEval(
                name=f"Accuracy: {question[:40]}",
                input=question,
                expected_output=expected,
                model=JUDGE_MODEL,
                additional_guidelines=guidelines,
            )
            eval_result = eval_obj.run_with_output(output=response)

            passed = eval_result is not None and eval_result.avg_score >= 7.0
            result: dict = {
                "question": question,
                "category": category,
                "status": "PASS" if passed else "FAIL",
                "duration": duration,
            }
            if eval_result and eval_result.results:
                result["score"] = eval_result.results[0].score
                if not passed:
                    result["reason"] = eval_result.results[0].reason
            if verbose:
                result["response_preview"] = response[:200]
        except Exception as e:
            result = {
                "question": question,
                "category": category,
                "status": "ERROR",
                "reason": str(e),
                "duration": round(time.time() - start, 2),
            }
        results.append(result)
        _print_status(result, verbose)
    return results


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _print_status(result: dict, verbose: bool) -> None:
    icon = {"PASS": "PASS", "FAIL": "FAIL", "ERROR": "ERR "}.get(result["status"], "??? ")
    score = f" (score: {result['score']})" if "score" in result else ""
    print(f"         {icon} ({result['duration']}s){score}")
    if verbose and result.get("reason"):
        print(f"         Reason: {result['reason']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
RUNNERS = {
    "judge_binary": lambda mod, cat, v: run_judge_cases(mod.CASES, mod.CRITERIA, cat, "binary", v),
    "judge_numeric": lambda mod, cat, v: run_judge_cases(mod.CASES, mod.CRITERIA, cat, "numeric", v),
    "reliability": lambda mod, cat, v: run_reliability_cases(mod.CASES, cat, v),
    "accuracy": lambda mod, cat, v: run_accuracy_cases(mod.CASES, cat, v),
}


def run_evals(category: str | None = None, verbose: bool = False) -> bool:
    """Run eval categories and display results.

    Returns True if all cases passed, False otherwise.
    """
    return _run_evals_collect(category=category, verbose=verbose)[0]


def _run_evals_collect(category: str | None = None, verbose: bool = False) -> tuple[bool, list[dict]]:
    """Run eval categories, print summary, and return (all_passed, results)."""
    all_results: list[dict] = []
    total_start = time.time()

    for name, config in CATEGORIES.items():
        if category and name != category:
            continue

        module = importlib.import_module(config["module"])
        case_count = len(module.CASES)
        print(f"\n--- {name} ({case_count} cases) ---\n")

        runner = RUNNERS[config["type"]]
        all_results.extend(runner(module, name, verbose))

    if not all_results:
        print(f"No cases found for category: {category}")
        return False, []

    # Summary
    total_duration = round(time.time() - total_start, 2)
    passed = sum(1 for r in all_results if r["status"] == "PASS")
    failed = sum(1 for r in all_results if r["status"] == "FAIL")
    errors = sum(1 for r in all_results if r["status"] == "ERROR")

    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed, {errors} errors ({total_duration}s)")
    print(f"{'=' * 50}\n")

    return failed + errors == 0, all_results


def _pass_rate(results: list[dict]) -> float:
    """Overall pass-rate (%) across all results. ERROR counts as not-passed."""
    if not results:
        return 0.0
    passed = sum(1 for r in results if r["status"] == "PASS")
    return round(100.0 * passed / len(results), 1)


def _accuracy_only_category() -> str | None:
    """Return the single category whose type is 'accuracy', if exactly one exists.

    Used by the --golden flag to restrict the gate to accuracy/golden cases when
    they are cleanly separable. Returns None to signal 'run the full suite'.
    """
    accuracy_cats = [name for name, cfg in CATEGORIES.items() if cfg.get("type") == "accuracy"]
    return accuracy_cats[0] if len(accuracy_cats) == 1 else None


# ---------------------------------------------------------------------------
# CLI entry (golden gate)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """CLI entry for `python -m evals.run`.

    Supports a deploy gate:
        python -m evals.run --golden --min-pass 90

    When --min-pass N is given, computes overall pass-rate and exits 1 if
    pass_rate < N (else 0). Without --min-pass, runs the suite and does NOT
    sys.exit on success (preserves existing no-arg behavior).
    """
    import argparse

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Run Dash evals (golden gate)")
    parser.add_argument(
        "--category",
        type=str,
        choices=list(CATEGORIES.keys()),
        help="Run a single eval category",
    )
    parser.add_argument("--verbose", action="store_true", help="Show response previews and failure reasons")
    parser.add_argument(
        "--golden",
        action="store_true",
        help="Restrict the gate to accuracy/golden cases (if cleanly separable)",
    )
    parser.add_argument(
        "--min-pass",
        type=float,
        default=None,
        metavar="N",
        help="Gate: fail (exit 1) if overall pass-rate %% < N",
    )
    args = parser.parse_args(argv)

    category = args.category
    if args.golden and category is None:
        category = _accuracy_only_category()

    all_passed, results = _run_evals_collect(category=category, verbose=args.verbose)

    # Gate only when a threshold is given. Otherwise preserve old behavior:
    # never sys.exit on success.
    if args.min_pass is not None:
        rate = _pass_rate(results)
        verdict = "PASS" if rate >= args.min_pass else "FAIL"
        print(f"GOLDEN GATE: pass_rate={rate}% threshold={args.min_pass} -> {verdict}")
        sys.exit(0 if rate >= args.min_pass else 1)


if __name__ == "__main__":
    main()
