from __future__ import annotations
import argparse,csv,json,tracemalloc
from pathlib import Path
from phase2.cnf import bounded_resolution,parse_cnf
from phase2.structure import benefit_gate,fast_cheap_gate,recover_exact_registry
from phase3.common import write_csv

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--phase3",type=Path,required=True);ap.add_argument("--config",type=Path,required=True);ap.add_argument("--output",type=Path,required=True);a=ap.parse_args();cfg=json.loads(a.config.read_text());rows=[]
    for item in csv.DictReader((a.phase3/"instances.csv").open(newline="",encoding="utf-8")):
        tracemalloc.start();cnf=parse_cnf(a.phase3/"cnfs"/item["instance"]);gate=fast_cheap_gate(cnf);rec=reg=None
        if gate.decision=="RUN_LKK":
            benefit=benefit_gate(cnf,gate,cfg["benefit_gate"])
            if benefit.decision=="RUN_LKK":rec=bounded_resolution(cnf,cfg["recovery"]);reg=recover_exact_registry(rec,cfg["registry"])
        current,peak=tracemalloc.get_traced_memory();snap=tracemalloc.take_snapshot();tracemalloc.stop();top=snap.statistics("lineno")[:10]
        rows.append({"instance":item["instance"],"current_traced_bytes":current,"peak_traced_bytes":peak,"recovery_status":getattr(rec,"status","NOT_RUN"),"registry_status":getattr(reg,"status","NOT_RUN"),"top_allocations":json.dumps([{"location":str(x.traceback[0]),"bytes":x.size,"count":x.count} for x in top],separators=(",",":"))})
    write_csv(a.output/"python_memory_allocations.csv",rows)
if __name__=="__main__":main()
