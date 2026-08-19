from __future__ import annotations
import argparse,csv,json,pstats,statistics
from pathlib import Path
from phase3.common import write_csv

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--profiling",type=Path,required=True);ap.add_argument("--phase3",type=Path,required=True);a=ap.parse_args();rows=list(csv.DictReader((a.profiling/"python_phase_profile.csv").open(newline="",encoding="utf-8")));startup=json.loads((a.profiling/"startup.json").read_text())
    phases={"process_startup":[startup["mean_ms"]],"cnf_parsing":[float(x["parse_ms"]) for x in rows],"fast_cheap_gate":[float(x["gate_ms"]) for x in rows],"benefit_gate":[float(x["benefit_gate_ms"]) for x in rows],"bounded_recovery":[float(x["recovery_ms"]) for x in rows],"exact_and_resource_registry":[float(x["registry_ms"]) for x in rows],"flow_box_test":[float(x["flow_ms"]) for x in rows],"witness_generation":[float(x["witness_ms"]) for x in rows],"witness_verification":[float(x["witness_check_ms"]) for x in rows]}
    old=list(csv.DictReader((a.phase3/"lkk_phase_summary.csv").open(newline="",encoding="utf-8")));phases["fallback_invocation"]=[float(x["median_fallback_cdcl_ms"]) for x in old if x["median_fallback_cdcl_ms"]]
    summary=[{"category":k,"samples":len(v),"total_ms":sum(v),"mean_ms":statistics.mean(v),"median_ms":statistics.median(v),"measurement":"instrumented accepted Python calls" if k!="fallback_invocation" else "accepted Phase 3 median per instance"} for k,v in phases.items()]
    summary += [{"category":"serialization_between_components","samples":0,"total_ms":"","mean_ms":"","median_ms":"","measurement":"No LKK/fallback structural serialization: external fallback reparses original DIMACS; witness JSON write is included in witness_generation."},{"category":"memory_allocation","samples":14,"total_ms":"","mean_ms":"","median_ms":"","measurement":"tracemalloc details in python_memory_allocations.csv"},{"category":"hashing_and_index_construction","samples":1,"total_ms":"","mean_ms":"","median_ms":"","measurement":"cProfile operations below; clause hashing/index construction is included in registry/recovery"}]
    write_csv(a.profiling/"profile_categories.csv",summary)
    stats=pstats.Stats(str(a.profiling/"python_structural.prof"));ops=[]
    for (file,line,name),(cc,nc,tt,ct,callers) in stats.stats.items():
        if any(x in name or x in file for x in ("canonical_clause","combinations_as_clauses","recover_exact_registry","setcomp","hash")):
            ops.append({"file":Path(file).name,"line":line,"operation":name,"primitive_calls":cc,"total_calls":nc,"self_seconds":tt,"cumulative_seconds":ct})
    write_csv(a.profiling/"registry_dominant_operations.csv",sorted(ops,key=lambda x:-x["cumulative_seconds"]))
if __name__=="__main__":main()
