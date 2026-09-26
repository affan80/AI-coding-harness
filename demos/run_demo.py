"""One-command demo runner for judges (issue #19).

Usage::

    python -m demos.run_demo --scenario all --out runs/demo

Writes ``runs/demo/<scenario>/final-report.md`` plus a metrics summary to
stdout for every scenario. Exits non-zero if any scenario fails.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from demos.scenarios import SCENARIOS, ScenarioResult


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m demos.run_demo",
        description="Run the harness demo scenarios (A: bug fix with recovery, "
        "B: bounded feature, C: large-context proof).",
    )
    parser.add_argument(
        "--scenario", choices=[*SCENARIOS, "all"], default="all",
        help="which scenario to run (default: all)",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("runs") / "demo",
        help="output directory for final reports (default: runs/demo)",
    )
    args = parser.parse_args(argv)

    selected = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    results: list[ScenarioResult] = []
    with tempfile.TemporaryDirectory(prefix="harness-demo-") as workdir:
        for key in selected:
            result = SCENARIOS[key](Path(workdir), args.out)
            results.append(result)
            print(f"== {result.name}: {'PASS' if result.passed else 'FAIL'}")
            print(f"   {result.summary}")
            for line in result.metrics_lines:
                print(f"   {line}")
            print(f"   report: {result.report_path}")

    ok = all(r.passed for r in results)
    print(f"\n{'ALL SCENARIOS PASSED' if ok else 'SCENARIO FAILURES PRESENT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
