"""Establish the SAT/UNSAT status of the corpus with a locked baseline solver.

This stage exists because the headline telemetry number - zero structural
refutations over the corpus - is only interpretable against the number of
instances that are refutable at all. A satisfiable instance cannot be refuted by
any sound procedure, so it belongs in the denominator of "coverage", never in
the denominator of "missed refutations".

Answers only. Timing here is meaningless (the pool is parallel) and is recorded
solely to show how much of the corpus the baseline itself could not decide.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from phase5.parallel import run_pool, worker_count
from phase5.run_phase5 import DEFINITE, direct_run, load_corpus, write_csv


def truth_row(repo: Path, out: Path, config: dict[str, Any], solver_key: str,
              timeout: float, item: dict[str, Any], index: int) -> dict[str, Any]:
    solver = config[solver_key]
    log = out / "logs" / f"{index:05d}_{Path(item['path']).stem[:60]}.log"
    run = direct_run(solver["executable"], solver["args"], Path(item["path"]),
                     1, timeout, log, repo)
    return {
        "instance": item["instance"], "source": item["source"], "set": item["set"],
        "domain": item["domain"], "cnf_sha256": item["cnf_sha256"],
        "solver": solver_key,
        "status": run["status"],
        # Wall time under a saturated pool is not a benchmark measurement; it is
        # kept only to separate "undecided because hard" from "decided fast".
        "untimed_wall_ms": run["runtime_ms"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=Path("results/phase5/ground_truth"))
    parser.add_argument("--config", type=Path, default=Path("phase5/config.json"))
    parser.add_argument("--solver", default="kissat")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    repo = Path.cwd().resolve()
    out = args.output.resolve()
    (out / "logs").mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text())

    # Hashes were verified when the telemetry stage read the same corpus; this
    # stage only needs the answers.
    corpus = load_corpus(config, repo, False)
    if args.limit:
        corpus = corpus[:args.limit]

    def work(pair):
        index, item = pair
        return truth_row(repo, out, config, args.solver, args.timeout, item, index)

    def progress(_row, done, total):
        if done % 50 == 0 or done == total:
            print(f"  {done}/{total}", flush=True)

    rows = run_pool(list(enumerate(corpus, start=1)), work,
                    workers=args.workers, on_result=progress)
    write_csv(out / "ground_truth.csv", rows)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    summary = {
        "instances": len(rows),
        "solver": args.solver,
        "timeout_seconds": args.timeout,
        "workers": worker_count(args.workers),
        "status_counts": counts,
        "decided": sum(c for s, c in counts.items() if s in DEFINITE),
        "unsat": counts.get("UNSATISFIABLE", 0),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
