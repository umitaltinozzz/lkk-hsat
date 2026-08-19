"""Stage 1 of Phase 5, run on its own and in parallel.

The telemetry pass runs only the structural front-end: no solver is invoked and
no timing claim is made, so it parallelises freely. It produces the §3
stratification of the whole corpus and, incidentally, the cost model needed to
predict how long the measured campaign will take.

    python3 -m phase5.run_telemetry --output results/phase5/telemetry
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from phase3.common import write_csv
from phase5 import classify as classifier
from phase5.parallel import run_pool, worker_count
from phase5.run_phase5 import load_corpus, native_run, split_of


def telemetry_row(repo: Path, out: Path, config: dict[str, Any],
                  item: dict[str, Any], index: int) -> dict[str, Any]:
    log = out / "logs" / f"{index:05d}_{Path(item['path']).stem[:60]}.log"
    run = native_run(repo, Path(item["path"]), item["cnf_sha256"], 1,
                     float(config["telemetry_timeout_seconds"]), log,
                     "none", "flow", config, telemetry_only=True)
    stratum, why = classifier.classify(run)
    return {
        "instance": item["instance"], "source": item["source"], "set": item["set"],
        "domain": item["domain"], "path": item["path"],
        "cnf_sha256": item["cnf_sha256"],
        "split": split_of(item["cnf_sha256"], float(config["calibration_fraction"])),
        "stratum": stratum, "stratum_reason": why,
        "telemetry_status": run.get("status"),
        "variables": run.get("variables"), "clauses": run.get("clauses"),
        "parse_ms": run.get("parse_ms"), "gate_ms": run.get("gate_ms"),
        "gate_decision": run.get("gate_decision"),
        "gate_monotone_positive": run.get("gate_monotone_positive"),
        "gate_monotone_negative": run.get("gate_monotone_negative"),
        "gate_mirror_pairs": run.get("gate_mirror_pairs"),
        "benefit_gate_decision": run.get("benefit_gate_decision"),
        "recovery_ms": run.get("recovery_ms"),
        "recovery_status": run.get("recovery_status"),
        "registry_ms": run.get("registry_ms"),
        "registry_status": run.get("registry_status"),
        # The reason names the closure condition that blocked, which is the only
        # way to tell "no structure" apart from "structure we cannot model".
        "registry_reason": run.get("registry_reason"),
        "boxes_found": run.get("boxes_found"),
        "capacity_groups": run.get("capacity_groups"),
        "flow_ms": run.get("flow_ms"),
        "structural_result": run.get("structural_result"),
        "front_end_ms": run.get("total_end_to_end_ms"),
        "wall_ms": run.get("runtime_ms"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("results/phase5/telemetry"))
    parser.add_argument("--config", type=Path, default=Path("phase5/config.json"))
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    repo = Path.cwd().resolve()
    out = args.output.resolve()
    (out / "logs").mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))

    corpus = load_corpus(config, repo, verify=False)
    if args.limit:
        corpus = corpus[: args.limit]
    workers = worker_count(args.workers or None)
    print(f"telemetry on {len(corpus)} instances across {workers} workers",
          flush=True)

    started = time.perf_counter()
    counter = {"n": 0}

    def report(_record: dict[str, Any], done: int, total: int) -> None:
        counter["n"] = done
        if done % 100 == 0 or done == total:
            rate = done / max(1e-9, time.perf_counter() - started)
            print(f"  {done}/{total}  {rate:.1f} inst/s  "
                  f"eta {(total - done) / max(rate, 1e-9):.0f}s", flush=True)

    # Index the items up front: looking the position up per call would make the
    # pass quadratic, which is invisible at forty instances and dominant at a
    # thousand.
    indexed = list(enumerate(corpus))
    rows = run_pool(
        indexed,
        lambda pair: telemetry_row(repo, out, config, pair[1], pair[0]),
        workers=workers, on_result=report)
    elapsed = time.perf_counter() - started

    write_csv(out / "benchmark_manifest.csv", rows)
    write_csv(out / "strata_summary.csv", classifier.summarize(rows))

    strata = Counter(r["stratum"] for r in rows)
    fronts = [float(r["front_end_ms"] or 0) for r in rows if r.get("front_end_ms")]
    summary = {
        "instances": len(rows), "workers": workers,
        "wall_seconds": round(elapsed, 1),
        "front_end_ms_median": round(statistics.median(fronts), 2) if fronts else None,
        "front_end_ms_p95": round(sorted(fronts)[int(0.95 * len(fronts))], 2)
        if fronts else None,
        "front_end_ms_total": round(sum(fronts), 1) if fronts else None,
        "strata": dict(strata),
        "structural_unsat": sum(1 for r in rows
                                if r.get("structural_result") == "UNSAT"),
        "gate_declined": sum(1 for r in rows
                             if r.get("gate_decision") != "RUN_LKK"),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
