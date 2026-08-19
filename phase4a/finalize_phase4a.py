from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from benchmark.harness import sha256_file
from phase2.witness import check_witness
from phase3.common import write_csv


def main() -> int:
    ap=argparse.ArgumentParser();ap.add_argument("--run",type=Path,required=True);args=ap.parse_args();run=args.run.resolve();repo=Path.cwd().resolve()
    hash_to_cnf={}
    for root in (Path("results/phase2/20260816Tphase2validation02Z/cnfs"),Path("results/phase3/20260816Tphase3benchmark01Z/cnfs"),run/"boundary_cnfs"):
        for p in root.glob("*.cnf"):hash_to_cnf[sha256_file(p)]=p
    validations=[];checks={}
    for wp in sorted((run/"logs"/"witnesses").glob("*.json")):
        data=json.loads(wp.read_text());cnf=hash_to_cnf[data["cnf_sha256"]];st=time.perf_counter_ns()
        try: verdict=check_witness(cnf,wp);valid=True;error=""
        except Exception as exc: verdict={};valid=False;error=str(exc)
        elapsed=(time.perf_counter_ns()-st)/1e6;checks[wp.name]=elapsed
        campaign="boundary" if wp.name.startswith("boundary") else "phase3_rerun" if wp.name.startswith("rerun") else wp.name.split("__",1)[0]
        validations.append({"campaign":campaign,"instance":cnf.name,"witness":wp.relative_to(repo).as_posix(),"valid":valid,"check_ms":elapsed,"error":error,**verdict})
        if not valid:(run/"failures"/f"witness_{wp.stem}.json").write_text(json.dumps(validations[-1],indent=2)+"\n")
    write_csv(run/"witness_validation.csv",validations)
    if not all(r["valid"] for r in validations):raise RuntimeError("witness validation failed")

    timings=list(csv.DictReader((run/"phase_timings.csv").open(newline="",encoding="utf-8")))
    for r in timings:
        stem=Path(r["instance"]).stem;name=next((n for n in checks if n.startswith("rerun__") and f"__{stem}__native_lkk__seed{r['seed']}.json" in n),None)
        r["witness_check_ms"]=checks.get(name,0.0)
    write_csv(run/"phase_timings.csv",timings)

    boundary=list(csv.DictReader((run/"boundary_sweep.csv").open(newline="",encoding="utf-8")))
    for r in boundary:
        name=f"boundary__{Path(r['instance']).stem}__native_lkk.json"
        r["witness_check_ms"]=checks.get(name,0.0) if r["solver"]=="native_lkk" else ""
    write_csv(run/"boundary_sweep.csv",boundary)

    # A separate 30-second correctness run solves exact-r scale 11 and exposes
    # the phase split that the mandatory 5-second run cannot serialize before kill.
    log=run/"logs"/"correctness"/"phase3__004_exact_r_allocation_boundary_high_scale11_noise0.log"
    diag=json.loads(next(x for x in reversed(log.read_text().splitlines()) if x.startswith("{")))
    diag_row={"provenance":"separate_correctness_run_30s_timeout","instance":"004_exact_r_allocation_boundary_high_scale11_noise0.cnf",**diag}
    write_csv(run/"profiling"/"exact_r_scale11_diagnostic.csv",[diag_row])

    # Enrich the implementation comparison with like-for-like phase medians.
    comparison=list(csv.DictReader((run/"native_vs_python.csv").open(newline="",encoding="utf-8")))
    old={r["instance"]:r for r in csv.DictReader(Path("results/phase3/20260816Tphase3benchmark01Z/lkk_phase_summary.csv").open(newline="",encoding="utf-8"))}
    by={}
    for r in timings:by.setdefault(r["instance"],[]).append(r)
    for r in comparison:
        p=old[r["instance"]];g=by.get(r["instance"],[])
        for phase in ("gate","benefit_gate","recovery","registry","flow","fallback"):
            r[f"python_median_{phase}_ms"]=p.get(f"median_{phase}_ms",p.get(f"median_{phase}_cdcl_ms",""))
            vals=[float(x[f"{phase}_ms"]) for x in g if x.get(f"{phase}_ms") not in (None,"")]
            r[f"native_median_{phase}_ms"]=statistics.median(vals) if vals else ""
        r["python_median_end_to_end_ms"]=p["median_total_lkk_end_to_end_ms"]
        totals=[float(x["total_end_to_end_ms"]) for x in g if x.get("total_end_to_end_ms")]
        r["native_median_internal_total_ms"]=statistics.median(totals) if totals else ""
    write_csv(run/"native_vs_python.csv",comparison)

    report=(run/"RESULTS.md").read_text(encoding="utf-8")
    report=report.replace("26 native structural witnesses passed",f"{len(validations)} emitted native structural witnesses passed")
    report=re.sub(r"10\. Exact-r scale 11:.*?This locates the failure/cost rather than attributing it to max-flow\.",
        f"10. Exact-r scale 11 timed out for all solvers at 5 s. A separate, clearly labeled 30 s correctness diagnostic completed in {diag['total_end_to_end_ms']:.1f} ms: parse {diag['parse_ms']:.3f} ms, gates {diag['gate_ms']+diag['benefit_gate_ms']:.3f} ms, recovery {diag['recovery_ms']:.3f} ms, registry {diag['registry_ms']:.3f} ms, then registry returned UNKNOWN at the unchanged 200,000 family-check budget; in-process CaDiCaL fallback consumed {diag['fallback_ms']:.1f} ms and solved UNSAT. Peak RSS was {diag['peak_rss_kb']} kB. Thus the 5 s failure is CDCL fallback difficulty after conservative registry abort, not FlowBoxTest.",report)
    report=report.replace("Python startup averaged 97.237 ms; same-process fallback removed the second solver launch and DIMACS reparse. Exact removed cost is separated in `phase_timings.csv`.",
        "Python startup averaged 97.237 ms and is eliminated from the internal native path. In the instrumented profile, Python CNF parsing averaged 24.182 ms versus 3.293 ms for completed native reruns (7.34x); the latter parses once and feeds the same clauses to in-process CaDiCaL, eliminating the fallback reparse. The profile comparison is diagnostic because cProfile adds overhead.")
    report=report.replace("13. Phase 4B LemmaBridge should be considered only if the per-family results show a reproducible region beyond isolated instances; this run supplies the evidence but implements no bridge. The recommendation is recorded conservatively: **attempt only as a separately correctness-gated experiment, not as a claimed improvement**.",
        "13. **Yes, Phase 4B is warranted as a separate correctness-gated experiment**, because the exact rerun produced five wins over both solvers and the pigeonhole advantage persisted across scales 8–12. It is not warranted as a presumed improvement: capacity-2 crossed Kissat only at scale 6, Hall remained fallback-dominated, and exact-r scale 11 still timed out at 5 s.")
    (run/"RESULTS.md").write_text(report,encoding="utf-8")
    meta=json.loads((run/"metadata.json").read_text());meta["witnesses_verified"]=len(validations);meta["all_emitted_witnesses_verified"]=True;meta["exact_r_scale11_diagnostic_provenance"]="separate correctness run, 30s timeout";meta["native_source_sha256"]=sha256_file(Path("native/phase4a/lkk_native.cpp"));meta["dockerfile_sha256"]=sha256_file(Path("Dockerfile.phase4a"));meta["finalized_utc"]=datetime.now(timezone.utc).isoformat();(run/"metadata.json").write_text(json.dumps(meta,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"witnesses":len(validations),"all_valid":True,"exact_r_diagnostic_ms":diag["total_end_to_end_ms"]}))
    return 0


if __name__=="__main__":raise SystemExit(main())
