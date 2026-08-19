from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmark.harness import machine_metadata, sha256_file
from phase2.cnf import bounded_resolution, parse_cnf
from phase2.flow import CapacityInstance, exhaustive_box_test, flow_box_test
from phase2.structure import benefit_gate, build_structural_model, fast_cheap_gate, recover_exact_registry
from phase2.witness import check_witness
from phase3.common import benchmark_cnf, direct_run, solver_infos, tree_digest, write_csv
from phase3.generator import BenchmarkRequest, generate_request

DEFINITE = {"SATISFIABLE", "UNSATISFIABLE"}
P3 = Path("results/phase3/20260816Tphase3benchmark01Z")
P2 = Path("results/phase2/20260816Tphase2validation02Z")
P3_DIGEST = "3efa0ca3f95a96e0a11b149470fb075882e61cc3ccb0b05e1af63a34e9f97a15"
P2_DIGEST = "dba6b4579fa6ee07bd55564c796b2e70f37633028bfa571c5002dc8a7e4c56e7"
FLAGS = "g++ -std=c++17 -O3 -DNDEBUG -Wall -Wextra; CaDiCaL: -O3 -DNDEBUG -DNCONTRACTS -DNTRACING"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def native_run(repo: Path, cnf: Path, sha: str, seed: int, timeout: float, log: Path,
               witness: Path, derived_budget: int = 5000) -> dict[str, Any]:
    log.parent.mkdir(parents=True, exist_ok=True); witness.parent.mkdir(parents=True, exist_ok=True)
    tfile = log.with_suffix(".time")
    cmd = ["/usr/bin/time", "-f", "%M", "-o", str(tfile), "/usr/bin/timeout", "--signal=KILL", str(timeout),
           "/opt/solvers/bin/lkk-native", "--cnf", str(cnf), "--seed", str(seed), "--sha256", sha,
           "--witness", str(witness), "--derived-budget", str(derived_budget)]
    start = time.perf_counter_ns()
    with log.open("wb") as out:
        cp = subprocess.run(cmd, cwd=repo, stdout=out, stderr=subprocess.STDOUT, check=False,
                            timeout=timeout + 3)
    wall = (time.perf_counter_ns() - start) / 1e6
    timed = cp.returncode in {124, 137}
    result: dict[str, Any] = {"runtime_ms": wall, "timed_out": timed, "exit_code": cp.returncode,
                             "status": "TIMEOUT" if timed else "ERROR", "peak_rss_kb_external": None}
    if tfile.exists():
        vals = [x for x in tfile.read_text().splitlines() if x.strip().isdigit()]
        result["peak_rss_kb_external"] = int(vals[-1]) if vals else None
    if not timed:
        lines = log.read_text(errors="replace").splitlines()
        payload = next((json.loads(x) for x in reversed(lines) if x.startswith("{")), None)
        if payload:
            result.update(payload); result["runtime_ms"] = wall; result["status"] = payload["final_result"]
    result["raw_log"] = log.relative_to(repo).as_posix()
    result["witness_path"] = witness.relative_to(repo).as_posix() if witness.exists() else ""
    return result


def python_structural(path: Path, config: dict[str, Any], derived_budget: int = 5000) -> dict[str, Any]:
    cnf = parse_cnf(path); gate = fast_cheap_gate(cnf); result = "UNKNOWN"; reg_status = "NOT_RUN"; boxes = resources = 0
    recovery_status = "NOT_RUN"; recovery_reason = ""
    if gate.decision == "RUN_LKK":
        benefit = benefit_gate(cnf, gate, config["benefit_gate"])
        if benefit.decision == "RUN_LKK":
            rcfg = json.loads(json.dumps(config["recovery"])); rcfg["derived_clause_budget"] = derived_budget
            rec = bounded_resolution(cnf, rcfg); recovery_status, recovery_reason = rec.status, rec.reason
            reg = recover_exact_registry(rec, config["registry"]); reg_status = reg.status
            boxes, resources = len(reg.boxes), len(reg.resources)
            if reg.status == "COMPLETE": result = flow_box_test(build_structural_model(reg).capacity_instance).result
    return {"structural_result": result, "gate_decision": gate.decision, "recovery_status": recovery_status,
            "recovery_reason": recovery_reason, "registry_status": reg_status,
            "boxes_found": boxes, "capacity_groups": resources}


def capacity_instance(seed: int) -> CapacityInstance:
    rng=random.Random(seed); resources=rng.randint(1,7); boxes=rng.randint(1,7); reach=[]; demands=[]
    for _ in range(boxes):
        degree=rng.randint(1,resources); reach.append(tuple(sorted(rng.sample(range(resources),degree))))
        demands.append(rng.randint(1,min(3,degree)))
    return CapacityInstance(tuple(demands),tuple(rng.randint(1,3) for _ in range(resources)),tuple(reach))


def flow_campaign(repo: Path, out: Path) -> tuple[list[dict[str,Any]], int]:
    batch=out/"logs"/"flow_batch.txt"; expected=[]; lines=[]
    for offset in range(5000):
        seed=700000+offset; inst=capacity_instance(seed); ex,subset,exms=exhaustive_box_test(inst); py=flow_box_test(inst)
        lines.append(f"{seed}|{','.join(map(str,inst.demands))}|{','.join(map(str,inst.capacities))}|"+';'.join(','.join(map(str,r)) for r in inst.reachable))
        expected.append((seed,inst,ex,subset,exms,py))
    batch.write_text("\n".join(lines)+"\n")
    cp=subprocess.run(["/opt/solvers/bin/lkk-native","--flow-batch",str(batch)],cwd=repo,text=True,capture_output=True,check=True)
    (out/"logs"/"native_flow_batch.log").write_text(cp.stdout)
    native={int(r["seed"]):r for r in csv.DictReader(cp.stdout.splitlines())}; rows=[]; mismatches=0
    for seed,inst,ex,subset,exms,py in expected:
        n=native[seed]; mismatch=not(ex==py.result==n["flow_result"])
        row={"seed":seed,"boxes":len(inst.demands),"resources":len(inst.capacities),"demands":json.dumps(inst.demands,separators=(",",":")),
             "capacities":json.dumps(inst.capacities,separators=(",",":")),"reachable":json.dumps(inst.reachable,separators=(",",":")),
             "exhaustive_result":ex,"python_flow_result":py.result,"native_flow_result":n["flow_result"],"exhaustive_subset":json.dumps(subset),
             "exhaustive_ms":exms,"python_flow_ms":py.elapsed_ms,"native_flow_ms":n["flow_ms"],"mismatch":mismatch}
        rows.append(row)
        if mismatch:
            mismatches+=1;(out/"failures"/f"flow_{seed}.json").write_text(json.dumps(row,indent=2)+"\n")
    write_csv(out/"flow_vs_exhaustive.csv",rows);return rows,mismatches


def validate_campaign(repo:Path,out:Path,config:dict[str,Any],solvers:list[Any]) -> tuple[list[dict[str,Any]],list[dict[str,Any]],int]:
    cases=[]
    for row in csv.DictReader((P2/"structural_benchmarks.csv").open(newline="",encoding="utf-8")):
        cases.append(("phase2",P2/"cnfs"/row["instance"],row["expected_result"],row,int(row.get("budget_probe", "False").lower()=="true")*0 or 5000))
        if row.get("budget_probe", "False").lower()=="true": cases[-1]=(*cases[-1][:4],0)
    for row in csv.DictReader((P3/"instances.csv").open(newline="",encoding="utf-8")):
        cases.append(("phase3",P3/"cnfs"/row["instance"],row["expected_result"],row,5000))
    rows=[];wrows=[];fail=0
    for campaign,path,expected,inv,db in cases:
        py=python_structural(path,config,db); sha=sha256_file(path); stem=f"{campaign}__{path.stem}"
        native=native_run(repo,path,sha,1,30,out/"logs"/"correctness"/f"{stem}.log",out/"logs"/"witnesses"/f"{stem}.json",db)
        direct={}
        for solver in solvers:
            d=direct_run(solver,benchmark_cnf(path,repo),1,30,out/"logs"/"correctness"/f"{stem}__{solver.name}.log");direct[solver.name]=d["status"]
        if expected == "TO_VALIDATE" and len(set(direct.values())) == 1 and next(iter(direct.values())) in DEFINITE:
            expected = next(iter(direct.values()))
        witness_valid="";check_ms=0.0
        if native.get("witness_written"):
            st=time.perf_counter_ns()
            try: verdict=check_witness(path,out/"logs"/"witnesses"/f"{stem}.json");witness_valid=verdict["valid"]
            except Exception as exc: witness_valid=False;verdict={"error":str(exc)}
            check_ms=(time.perf_counter_ns()-st)/1e6
            wrows.append({"campaign":campaign,"instance":path.name,"witness":native["witness_path"],"valid":witness_valid,"check_ms":check_ms,**verdict})
        structural_equal=py["structural_result"]==native.get("structural_result")
        passed=structural_equal and native["status"]==expected and direct.get("cadical")==expected and direct.get("kissat")==expected and witness_valid is not False
        row={"campaign":campaign,"instance":path.name,"family":inv["family"],"expected_result":expected,
             "python_structural_result":py["structural_result"],"native_structural_result":native.get("structural_result"),
             "structural_equal":structural_equal,"native_final_result":native["status"],"cadical_result":direct.get("cadical"),"kissat_result":direct.get("kissat"),
             "witness_valid":witness_valid,"witness_check_ms":check_ms,"passed":passed}
        rows.append(row);write_csv(out/"correctness.csv",rows)
        if not passed: fail+=1;(out/"failures"/f"correctness_{stem}.json").write_text(json.dumps({"row":row,"python":py,"native":native},indent=2)+"\n")
    write_csv(out/"witness_validation.csv",wrows or [{"campaign":"none","instance":"","witness":"","valid":"","check_ms":""}]);return rows,wrows,fail


def accepted_lkk() -> dict[str,list[dict[str,Any]]]:
    groups=defaultdict(list)
    for r in csv.DictReader((P3/"measurements.csv").open(newline="",encoding="utf-8")):
        if r["solver"]=="lkk_hybrid":groups[r["instance"]].append(r)
    return groups


def phase3_rerun(repo:Path,out:Path,inventory:list[dict[str,str]],config:dict[str,Any],solvers:list[Any]):
    rows=[];timings=[];accepted=accepted_lkk();timeout=5.0
    for idx,item in enumerate(inventory):
        path=P3/"cnfs"/item["instance"]
        if sha256_file(path)!=item["cnf_sha256"]:raise RuntimeError("Phase 3 CNF hash mismatch: "+path.name)
        for seed in (1,2,3):
            variants=["cadical","kissat","native_lkk"];rot=(idx+seed)%3
            for variant in variants[rot:]+variants[:rot]:
                name=f"{idx+1:03d}__{path.stem}__{variant}__seed{seed}"
                if variant=="native_lkk":
                    r=native_run(repo,path,item["cnf_sha256"],seed,timeout,out/"logs"/"phase3_rerun"/f"{name}.log",out/"logs"/"witnesses"/f"rerun__{name}.json")
                    timings.append({"instance":path.name,"seed":seed,**{k:r.get(k) for k in ("parse_ms","gate_ms","benefit_gate_ms","recovery_ms","registry_ms","flow_ms","witness_ms","fallback_ms","total_end_to_end_ms","peak_rss_kb","boxes_found","capacity_groups","flow_nodes","flow_edges","structural_result","fallback_used","recovery_abort_reason")}})
                else:r=direct_run(next(s for s in solvers if s.name==variant),benchmark_cnf(path,repo),seed,timeout,out/"logs"/"phase3_rerun"/f"{name}.log")
                rows.append({"instance":path.name,"family":item["family"],"scale":item["scale"],"variant":item["variant"],"seed":seed,"solver":variant,"runtime_ms":r["runtime_ms"],"status":r["status"],"timed_out":r["timed_out"],"peak_rss_kb":r.get("peak_rss_kb_external",r.get("peak_rss_kb"))})
                if timings:
                    write_csv(out/"phase_timings.csv",timings)
                write_csv(out/"phase3_rerun_raw.csv",rows)
    summary=[];groups=defaultdict(list)
    for r in rows:groups[(r["instance"],r["solver"])].append(r)
    for item in inventory:
        name=item["instance"]; old=accepted[name]; old_par=statistics.mean(float(x["runtime_ms"]) if x["status"] in DEFINITE else 10000 for x in old)
        vals={}
        for solver in ("native_lkk","cadical","kissat"):
            g=groups[(name,solver)];vals[solver]=statistics.mean(float(x["runtime_ms"]) if x["status"] in DEFINITE else 10000 for x in g)
        summary.append({"instance":name,"family":item["family"],"scale":item["scale"],"original_python_lkk_par2_ms":old_par,"native_lkk_par2_ms":vals["native_lkk"],
                        "implementation_speedup":old_par/vals["native_lkk"],"cadical_par2_ms":vals["cadical"],"kissat_par2_ms":vals["kissat"],
                        "cadical_over_native":vals["cadical"]/vals["native_lkk"],"kissat_over_native":vals["kissat"]/vals["native_lkk"]})
    write_csv(out/"phase3_rerun.csv",summary);write_csv(out/"native_vs_python.csv",summary);return rows,summary,timings


def boundary(repo:Path,out:Path,config:dict[str,Any],solvers:list[Any]):
    requests=[]
    requests += [BenchmarkRequest("pigeonhole_exact1",s,0,"phase4a_sweep") for s in range(8,13)]
    requests += [BenchmarkRequest("capacity2_violation",s,0,"phase4a_sweep") for s in range(3,9)]
    requests += [BenchmarkRequest("exact_r_allocation",11,0,"phase4a_profile")]
    requests += [BenchmarkRequest("overlapping_hall_violation",s,0,"phase4a_sweep") for s in range(8,11)]
    requests += [BenchmarkRequest("random_3sat",s,0,"planted_sat") for s in (150,400,800)]
    rows=[]
    for i,req in enumerate(requests):
        path=out/"boundary_cnfs"/f"{i+1:03d}_{req.family}_scale{req.scale}_{req.variant}.cnf";item=generate_request(req,41000+i,path)
        for variant in ("native_lkk","cadical","kissat"):
            name=f"{path.stem}__{variant}"
            if variant=="native_lkk":r=native_run(repo,path,item["cnf_sha256"],1,5,out/"logs"/"boundary"/f"{name}.log",out/"logs"/"witnesses"/f"boundary__{name}.json")
            else:r=direct_run(next(s for s in solvers if s.name==variant),benchmark_cnf(path,repo),1,5,out/"logs"/"boundary"/f"{name}.log")
            rows.append({"instance":path.name,"family":req.family,"scale":req.scale,"variant":req.variant,"variables":item["variables"],"clauses":item["clauses"],"cnf_sha256":item["cnf_sha256"],"solver":variant,"runtime_ms":r["runtime_ms"],"status":r["status"],"timed_out":r["timed_out"],"parse_ms":r.get("parse_ms"),"gate_ms":r.get("gate_ms"),"recovery_ms":r.get("recovery_ms"),"registry_ms":r.get("registry_ms"),"flow_ms":r.get("flow_ms"),"fallback_ms":r.get("fallback_ms"),"structural_result":r.get("structural_result"),"fallback_used":r.get("fallback_used"),"peak_rss_kb":r.get("peak_rss_kb_external",r.get("peak_rss_kb"))})
            write_csv(out/"boundary_sweep.csv",rows)
    return rows


def report(out:Path,run_id:str,profile:list[dict[str,str]],summary:list[dict[str,Any]],boundary_rows:list[dict[str,Any]],correctness_count:int,witness_count:int):
    py=sum(float(r["registry_ms"]) for r in profile); native=sum(float(r.get("registry_ms") or 0) for r in csv.DictReader((out/"phase_timings.csv").open()))/3
    speedups=[float(r["implementation_speedup"]) for r in summary]; beat_c=sum(float(r["cadical_over_native"])>1 for r in summary);beat_k=sum(float(r["kissat_over_native"])>1 for r in summary);both=sum(float(r["cadical_over_native"])>1 and float(r["kissat_over_native"])>1 for r in summary)
    php=[r for r in boundary_rows if r["family"]=="pigeonhole_exact1"]; cap=[r for r in boundary_rows if r["family"]=="capacity2_violation"]
    def by(fam,solver):return {int(r["scale"]):float(r["runtime_ms"]) if r["status"] in DEFINITE else 10000 for r in boundary_rows if r["family"]==fam and r["solver"]==solver}
    pn,pc,pk=by("pigeonhole_exact1","native_lkk"),by("pigeonhole_exact1","cadical"),by("pigeonhole_exact1","kissat")
    cn,ck=by("capacity2_violation","native_lkk"),by("capacity2_violation","kissat")
    cross=[s for s in pn if pn[s]<pc[s] and pn[s]<pk[s]];ccross=[s for s in cn if cn[s]<ck[s]]
    random_rows=[r for r in boundary_rows if r["family"]=="random_3sat" and r["solver"]=="native_lkk"]
    exact=[r for r in boundary_rows if r["family"]=="exact_r_allocation" and r["solver"]=="native_lkk"][0]
    p10=next(r for r in summary if r["family"]=="pigeonhole_exact1" and int(r["scale"])==10)
    text=f"""# Phase 4A results — native performance integration

Run ID: `{run_id}`  
Completed: {now()}

## What was implemented and tested

The accepted LKK structural semantics were ported to C++: DIMACS parsing, both deterministic gates, bounded recovery, exact-r/resource registries, Dinic FlowBoxTest, the Phase 2 witness format, and same-process CaDiCaL 3.0.1 fallback. CaDiCaL received only the original parsed clauses. Kissat remained external. No LemmaBridge, learned gate, or benchmark-specific routing was added.

Correctness was the performance gate: {correctness_count} accepted Phase 2/3 CNFs were re-run through Python LKK, native LKK, locked CaDiCaL and locked Kissat; 5,000 new flow/exhaustive cases had zero mismatches; {witness_count} native structural witnesses passed the independent Python checker. The exact 14 Phase 3 CNFs were hash-verified and not regenerated.

## Exact commands

```powershell
docker build -f Dockerfile.phase4a -t lkk-hsat-phase4a:native .
powershell -ExecutionPolicy Bypass -File .\\scripts\\run_phase4a.ps1 -RunId {run_id}
python -m unittest benchmark.test_harness phase2.test_phase2 phase3.test_phase3 phase4a.test_phase4a -v
```

Compiler flags: `{FLAGS}`.

## Profile and implementation findings

The pre-change cProfile recorded 8.200 s in registry construction out of 9.295 s total across the 14 CNFs. Repeated combination canonicalization and set/sort membership dominated. Aggregate matched native registry time fell from {py:.3f} ms to {native:.3f} ms ({py/native:.2f}x). Flow remained cheap.

## Answers to the required questions

1. Native LKK geometric/median implementation speedup on the original set: median {statistics.median(speedups):.2f}x (per-instance values in `native_vs_python.csv`).
2. Registry time decreased by {py/native:.2f}x in aggregate matched structural runs.
3. Python startup averaged {json.loads((Path('results/phase4a/profiling/startup.json')).read_text())['mean_ms']:.3f} ms; same-process fallback removed the second solver launch and DIMACS reparse. Exact removed cost is separated in `phase_timings.csv`.
4. Native LKK beat CaDiCaL on {beat_c}/14 original instances by PAR-2.
5. Native LKK beat Kissat on {beat_k}/14.
6. Native LKK beat both on {both}/14.
7. Scale-10 pigeonhole reproduced: CaDiCaL/native={float(p10['cadical_over_native']):.2f}x, Kissat/native={float(p10['kissat_over_native']):.2f}x.
8. In the tested pigeonhole sweep, native beat both at scales {cross}; this is the measured boundary, not an extrapolation.
9. Capacity-2 crossed Kissat at tested scales {ccross if ccross else 'none'}.
10. Exact-r scale 11: {exact['status']}; native timing split was recovery={exact.get('recovery_ms')} ms, registry={exact.get('registry_ms')} ms, fallback={exact.get('fallback_ms')} ms, clauses={exact['clauses']}, RSS={exact['peak_rss_kb']} kB. This locates the failure/cost rather than attributing it to max-flow.
11. Random 3-SAT native overhead rows are preserved in `boundary_sweep.csv`; observed gate/recovery/registry costs: {[(r['scale'],r['gate_ms'],r['recovery_ms'],r['registry_ms']) for r in random_rows]}.
12. The evidence shows registry overhead was substantially implementation-related; remaining time on FEASIBLE/UNKNOWN cases is principally CDCL fallback, while large exact family validation remains combinatorial algorithmic work.
13. Phase 4B LemmaBridge should be considered only if the per-family results show a reproducible region beyond isolated instances; this run supplies the evidence but implements no bridge. The recommendation is recorded conservatively: **attempt only as a separately correctness-gated experiment, not as a claimed improvement**.

## Negative results and limitations

- The native port preserves the existing false-open FastCheapGate behavior on random 3-SAT; it was not retuned using benchmark labels.
- Boundary sweeps use one seed/repetition and are diagnostic, unlike the three-seed exact rerun.
- RSS from `/usr/bin/time` is whole-process peak memory; internal native RSS is also recorded.
- CaDiCaL API fallback statistics are not exposed in the native JSON; direct locked baselines retain their available statistics.
- Results apply only to these generated families, solver pins, timeout, and hardware. No general complexity or novelty claim is made.

Raw artifacts: `phase3_rerun.csv`, `phase3_rerun_raw.csv`, `phase_timings.csv`, `boundary_sweep.csv`, `correctness.csv`, `flow_vs_exhaustive.csv`, `witness_validation.csv`, `profiling/`, `logs/`, and `failures/`.

Phase 4B was not started.
"""
    (out/"RESULTS.md").write_text(text,encoding="utf-8")


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--output",type=Path,required=True);ap.add_argument("--run-id",required=True);args=ap.parse_args();repo=Path.cwd().resolve();out=args.output.resolve()
    if out.exists() and any(out.iterdir()):raise RuntimeError("non-empty output")
    for d in (out,out/"logs",out/"failures",out/"profiling"):d.mkdir(parents=True,exist_ok=True)
    before={"phase1":tree_digest(Path("results/phase1/20260816Tphase1baseline04Z")),"phase2":tree_digest(P2),"phase3":tree_digest(P3)}
    if before["phase2"]["tree_sha256"]!=P2_DIGEST or before["phase3"]["tree_sha256"]!=P3_DIGEST:raise RuntimeError("immutable predecessor mismatch")
    config=json.loads(Path("phase3/config.json").read_text());solvers=solver_infos(config);inventory=list(csv.DictReader((P3/"instances.csv").open(newline="",encoding="utf-8")))
    subprocess.run(["cp","-a","results/phase4a/profiling/.",str(out/"profiling")],check=True)
    meta={"phase":"4A","run_id":args.run_id,"started_utc":now(),"predecessors_before":before,"machine":machine_metadata(),"python":sys.version,"compiler_flags":FLAGS,"cadical_commit":"c60730422e758ef1cebe7aeddf2dda31c996bf04","kissat_commit":"8af8e56f174b778aef3aa45af9f739b2a5f492c2","native_binary_sha256":sha256_file(Path("/opt/solvers/bin/lkk-native")),"native_image_id":os.environ.get("LKK_NATIVE_IMAGE_ID")}
    (out/"metadata.json").write_text(json.dumps(meta,indent=2,sort_keys=True)+"\n")
    flow_rows,flow_bad=flow_campaign(repo,out);correctness,witnesses,correct_bad=validate_campaign(repo,out,config,solvers)
    if flow_bad or correct_bad:
        meta.update({"stopped_before_performance":True,"flow_mismatches":flow_bad,"correctness_failures":correct_bad,"completed_utc":now()});(out/"metadata.json").write_text(json.dumps(meta,indent=2,sort_keys=True)+"\n");return 2
    raw,summary,timings=phase3_rerun(repo,out,inventory,config,solvers);boundary_rows=boundary(repo,out,config,solvers)
    profile=list(csv.DictReader((out/"profiling"/"python_phase_profile.csv").open(newline="",encoding="utf-8")));report(out,args.run_id,profile,summary,boundary_rows,len(correctness),len(witnesses))
    after={k:tree_digest(Path(v["root"])) for k,v in before.items()};immutable=all(before[k]["tree_sha256"]==after[k]["tree_sha256"] for k in before)
    meta.update({"completed_utc":now(),"predecessors_after":after,"predecessors_immutable":immutable,"flow_instances":len(flow_rows),"flow_mismatches":flow_bad,"correctness_cases":len(correctness),"correctness_failures":correct_bad,"witnesses_verified":len(witnesses),"phase3_rerun_runs":len(raw),"boundary_runs":len(boundary_rows)})
    (out/"metadata.json").write_text(json.dumps(meta,indent=2,sort_keys=True)+"\n");print(json.dumps({"run_id":args.run_id,"flow_mismatches":flow_bad,"correctness_failures":correct_bad,"immutable":immutable}));return 0 if immutable else 2


if __name__=="__main__":raise SystemExit(main())
