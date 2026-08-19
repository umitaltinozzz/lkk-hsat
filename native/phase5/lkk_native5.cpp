// Phase 5 engine: the frozen architecture carried out of Phase 4B.
//
// The accepted structural semantics come from native/core/lkk_core.hpp, which
// is the single definition shared by every engine. This file adds only what
// Phase 5 needs: a static structure-informed choice of fallback engine, a
// telemetry-only mode for classifying a benchmark without solving it, and a
// --stop-after switch so the publication ablation can price each component.
// LemmaBridge is deliberately absent.

#include "lkk_registry_indexed.hpp"

// The static fallback selector.
//
// Its FORM is fixed here, before any Phase 5 measurement is taken. Only the
// numeric thresholds are supplied from outside, and they may only ever be
// fitted on a calibration split that is disjoint from the evaluation set.
// Every input is a structural feature computed before the fallback solver is
// launched; the instance's file name, family label, expected answer and any
// previously measured runtime are deliberately unavailable to this function.
//
// The rule encodes the one reproducible signal Phase 4B established: when the
// front-end finds substantial cardinality structure but cannot close it, the
// residual formula is combinatorially hard in a way Kissat handled better;
// otherwise the cheaper in-process CaDiCaL path wins.
struct SelectorConfig {
  long min_boxes = 4;        // detected exact-r boxes needed to prefer Kissat
  long min_clauses = 100000; // very large formulas also prefer Kissat
  long min_signal = 0;       // minimum cheap-gate structural signal
  bool enabled = false;
};
struct SelectorChoice { std::string engine="cadical",reason="default_in_process_cadical"; double elapsed=0; };
static SelectorChoice choose_engine(const SelectorConfig &cfg,const CNF &cnf,const Gate &gate,
                                    const Recovery &rec,const Registry &reg) {
  auto st=Clock::now(); SelectorChoice out;
  long boxes=(long)reg.boxes.size();
  long signal=gate.pos+gate.neg+2*gate.mirror;
  bool structure_open = reg.status!="COMPLETE";
  if(boxes>=cfg.min_boxes && structure_open && signal>=cfg.min_signal){
    out.engine="kissat";
    out.reason="open_cardinality_structure(boxes="+std::to_string(boxes)+",registry="+reg.status+")";
  } else if((long)cnf.clauses.size()>=cfg.min_clauses){
    out.engine="kissat";
    out.reason="large_formula(clauses="+std::to_string(cnf.clauses.size())+")";
  } else {
    out.reason="default_in_process_cadical(boxes="+std::to_string(boxes)
      +",recovery="+rec.status+",registry="+reg.status+")";
  }
  out.elapsed=ms(st); return out;
}

struct Fallback { std::string status="UNKNOWN"; double total_ms=0,serialize_ms=0,solver_self_ms=0,overhead_ms=0; int exit_code=0;
  long long conflicts=-1,decisions=-1,propagations=-1,restarts=-1,learned=-1,irredundant=-1,redundant=-1,ticks=-1; };

// Identical to the accepted Phase 4A/4B in-process fallback; the external
// timeout still bounds it.
static Fallback run_cadical(const CNF &cnf,int seed) {
  auto st=Clock::now(); Fallback fb; CaDiCaL::Solver solver; solver.set("quiet",1); solver.set("seed",seed);
  for(auto &c:cnf.clauses){ for(int l:c) solver.add(l); solver.add(0); }
  int res=solver.solve(); fb.total_ms=ms(st); fb.solver_self_ms=fb.total_ms; fb.overhead_ms=0;
  fb.status=res==10?"SATISFIABLE":res==20?"UNSATISFIABLE":"UNKNOWN"; fb.exit_code=res==10?10:res==20?20:0;
  fb.conflicts=solver.get_statistic_value("conflicts"); fb.decisions=solver.get_statistic_value("decisions");
  fb.propagations=solver.get_statistic_value("propagations"); fb.ticks=solver.get_statistic_value("ticks");
  fb.irredundant=solver.get_statistic_value("irredundant"); fb.redundant=solver.get_statistic_value("redundant");
  return fb;
}

// Kissat cannot take clauses in-process, so the hybrid pays a DIMACS write and
// a fork/exec. Both are measured and reported apart from the solver's own
// reported process time.
static Fallback run_kissat(const CNF &cnf,const std::string &kissat,const std::string &tmp,
                           const std::string &log,int seed,double timeout_s) {
  Fallback fb; auto sst=Clock::now();
  { std::ofstream o(tmp); o<<"p cnf "<<cnf.variables<<' '<<cnf.clauses.size()<<'\n';
    for(auto &c:cnf.clauses){ for(int l:c) o<<l<<' '; o<<"0\n"; } }
  fb.serialize_ms=ms(sst);
  auto st=Clock::now(); std::string seed_arg="--seed="+std::to_string(seed);
  std::string time_arg="--time="+std::to_string((int)(timeout_s>0?timeout_s:0));
  pid_t pid=fork();
  if(pid==0){ freopen(log.c_str(),"w",stdout); dup2(1,2);
    if(timeout_s>0) execl(kissat.c_str(),"kissat",seed_arg.c_str(),"--statistics",time_arg.c_str(),"-n",tmp.c_str(),(char*)nullptr);
    else execl(kissat.c_str(),"kissat",seed_arg.c_str(),"--statistics","-n",tmp.c_str(),(char*)nullptr);
    _exit(127); }
  if(pid<0) throw std::runtime_error("fork failed");
  int status=0; waitpid(pid,&status,0); fb.total_ms=ms(st);
  fb.exit_code=WIFEXITED(status)?WEXITSTATUS(status):-1;
  fb.status=fb.exit_code==10?"SATISFIABLE":fb.exit_code==20?"UNSATISFIABLE":"UNKNOWN";
  std::ifstream in(log); std::string line;
  while(std::getline(in,line)){
    auto num=[&](const char *key,long long &slot){ auto p=line.find(key); if(p==std::string::npos) return;
      std::istringstream s(line.substr(p+strlen(key))); long long v; if(s>>v) slot=v; };
    if(line.rfind("c conflicts:",0)==0) num("c conflicts:",fb.conflicts);
    else if(line.rfind("c decisions:",0)==0) num("c decisions:",fb.decisions);
    else if(line.rfind("c propagations:",0)==0) num("c propagations:",fb.propagations);
    else if(line.rfind("c restarts:",0)==0) num("c restarts:",fb.restarts);
    else if(line.rfind("c learned:",0)==0) num("c learned:",fb.learned);
    else if(line.rfind("c process-time:",0)==0){ auto p=line.find(':'); std::istringstream s(line.substr(p+1)); std::string w;
      while(s>>w){ try { fb.solver_self_ms=std::stod(w)*1000.0; break; } catch(...) {} } }
  }
  fb.overhead_ms=fb.serialize_ms+(fb.solver_self_ms>0?fb.total_ms-fb.solver_self_ms:0);
  return fb;
}

static int solve_main(int argc,char**argv){
  std::string path,witness,sha,engine="cadical",kissat="/opt/solvers/bin/kissat",tmp,kissat_log;
  // stop_after selects how much of the structural pipeline runs, so the
  // publication ablation can price each component separately. "flow" is the
  // full accepted pipeline; "none" disables LKK entirely.
  std::string stop_after="flow";
  int seed=1; long db=5000,family_budget=300000; bool telemetry_only=false,atleast_boxes=false; double timeout_s=0; SelectorConfig sel;
  for(int i=1;i<argc;i++){ std::string a=argv[i];
    if(a=="--cnf")path=argv[++i]; else if(a=="--witness")witness=argv[++i]; else if(a=="--sha256")sha=argv[++i];
    else if(a=="--seed")seed=std::stoi(argv[++i]); else if(a=="--derived-budget")db=std::stol(argv[++i]);
    else if(a=="--fallback")engine=argv[++i]; else if(a=="--kissat")kissat=argv[++i];
    else if(a=="--kissat-tmp")tmp=argv[++i]; else if(a=="--kissat-log")kissat_log=argv[++i];
    else if(a=="--telemetry-only")telemetry_only=true; else if(a=="--fallback-timeout")timeout_s=std::stod(argv[++i]);
    else if(a=="--selector-min-boxes")sel.min_boxes=std::stol(argv[++i]);
    else if(a=="--selector-min-clauses")sel.min_clauses=std::stol(argv[++i]);
    else if(a=="--selector-min-signal")sel.min_signal=std::stol(argv[++i]);
    else if(a=="--stop-after")stop_after=argv[++i];
    else if(a=="--family-check-budget")family_budget=std::stol(argv[++i]);
    else if(a=="--atleast-boxes")atleast_boxes=true;
    else throw std::runtime_error("unknown argument: "+a); }
  if(stop_after!="none"&&stop_after!="gate"&&stop_after!="recovery"&&stop_after!="registry"&&stop_after!="flow")
    throw std::runtime_error("unknown --stop-after stage: "+stop_after);
  if(tmp.empty()) tmp="/tmp/lkk5_"+std::to_string(getpid())+".cnf";
  if(kissat_log.empty()) kissat_log="/tmp/lkk5_"+std::to_string(getpid())+".kissat.log";
  sel.enabled=(engine=="selector");

  auto total=Clock::now(),st=Clock::now(); CNF cnf=parse_dimacs(path); double parse=ms(st);
  Gate gate,benefit;
  double recovery_ms=0,registry_ms=0,flow_ms=0,witness_ms=0;
  Recovery rec; Registry reg; FlowResult flow; SelectorChoice choice;
  std::string structural="UNKNOWN",final="UNKNOWN",abort_reason=""; bool fallback_used=true,witness_written=false;
  if(stop_after!="none"){ gate=fast_gate(cnf);
    if(gate.decision=="RUN_LKK"&&stop_after!="gate"){ benefit=benefit_gate(cnf,gate);
      if(benefit.decision=="RUN_LKK"){ rec=recover(cnf,db); recovery_ms=rec.elapsed; abort_reason=rec.status=="ABORT"?rec.reason:"";
        if(stop_after!="recovery"){ reg=registry_indexed(rec,family_budget,atleast_boxes); registry_ms=reg.elapsed;
          if(reg.status=="COMPLETE"&&stop_after!="registry"){ flow=flow_test(reg); flow_ms=flow.elapsed; structural=flow.result;
            if(flow.result=="UNSAT"){ st=Clock::now(); if(!witness.empty()) write_witness(witness,sha,cnf,rec,reg,flow); witness_ms=ms(st);
              witness_written=!witness.empty(); final="UNSATISFIABLE"; fallback_used=false; } } } } } }

  std::string resolved=engine;
  if(sel.enabled){ choice=choose_engine(sel,cnf,gate,rec,reg); resolved=choice.engine; }

  Fallback fb; int code=0;
  if(fallback_used&&!telemetry_only){
    if(resolved=="cadical") fb=run_cadical(cnf,seed);
    else if(resolved=="kissat") fb=run_kissat(cnf,kissat,tmp,kissat_log,seed,timeout_s);
    else if(resolved=="none"){ fb.status="UNKNOWN"; }
    else throw std::runtime_error("unknown fallback engine: "+resolved);
    final=fb.status; code=fb.status=="SATISFIABLE"?10:fb.status=="UNSATISFIABLE"?20:0;
  } else if(!fallback_used) code=20;

  struct rusage u{}; getrusage(RUSAGE_SELF,&u);
  std::cout<<"{\"implementation\":\"native_phase5\",\"instance\":"<<q(path)
    <<",\"fallback_mode\":"<<q(engine)<<",\"fallback_engine\":"<<q(resolved)
    <<",\"selector_choice\":"<<q(sel.enabled?choice.engine:"")
    <<",\"selector_reason\":"<<q(sel.enabled?choice.reason:"")
    <<",\"selector_ms\":"<<choice.elapsed<<",\"telemetry_only\":"<<(telemetry_only?"true":"false")
    <<",\"stop_after\":"<<q(stop_after)
    <<",\"variables\":"<<cnf.variables<<",\"clauses\":"<<cnf.clauses.size()
    <<",\"parse_ms\":"<<parse<<",\"gate_ms\":"<<gate.elapsed<<",\"gate_decision\":"<<q(gate.decision)
    <<",\"gate_reason\":"<<q(gate.reason)<<",\"gate_monotone_positive\":"<<gate.pos
    <<",\"gate_monotone_negative\":"<<gate.neg<<",\"gate_mirror_pairs\":"<<gate.mirror
    <<",\"gate_estimated_pairs\":"<<gate.pairs
    <<",\"benefit_gate_ms\":"<<benefit.elapsed
    <<",\"benefit_gate_decision\":"<<q(benefit.decision.empty()?"NOT_RUN":benefit.decision)
    <<",\"benefit_gate_reason\":"<<q(benefit.reason)
    <<",\"recovery_ms\":"<<recovery_ms<<",\"recovery_status\":"<<q(rec.status)<<",\"recovery_abort_reason\":"<<q(abort_reason)
    <<",\"recovery_derived\":"<<rec.derived
    <<",\"registry_ms\":"<<registry_ms<<",\"registry_status\":"<<q(reg.status)<<",\"registry_reason\":"<<q(reg.reason)
    <<",\"atleast_boxes\":"<<(atleast_boxes?"true":"false")
    <<",\"family_check_budget\":"<<family_budget<<",\"family_checks\":"<<reg.checks<<",\"flow_ms\":"<<flow_ms<<",\"witness_ms\":"<<witness_ms
    <<",\"fallback_ms\":"<<fb.total_ms<<",\"fallback_serialize_ms\":"<<fb.serialize_ms
    <<",\"fallback_solver_self_ms\":"<<fb.solver_self_ms<<",\"fallback_overhead_ms\":"<<fb.overhead_ms
    <<",\"fallback_conflicts\":"<<fb.conflicts<<",\"fallback_decisions\":"<<fb.decisions
    <<",\"fallback_propagations\":"<<fb.propagations<<",\"fallback_restarts\":"<<fb.restarts
    <<",\"fallback_learned\":"<<fb.learned<<",\"fallback_irredundant\":"<<fb.irredundant
    <<",\"fallback_redundant\":"<<fb.redundant<<",\"fallback_ticks\":"<<fb.ticks
    <<",\"total_end_to_end_ms\":"<<ms(total)<<",\"peak_rss_kb\":"<<u.ru_maxrss
    <<",\"boxes_found\":"<<reg.boxes.size()<<",\"capacity_groups\":"<<reg.resources.size()
    <<",\"flow_nodes\":"<<flow.nodes<<",\"flow_edges\":"<<flow.edges
    <<",\"flow_total_demand\":"<<flow.demand<<",\"flow_maximum\":"<<flow.maximum
    <<",\"structural_result\":"<<q(structural)<<",\"final_result\":"<<q(final)
    <<",\"fallback_used\":"<<(fallback_used?"true":"false")<<",\"witness_written\":"<<(witness_written?"true":"false")<<"}\n";
  if(resolved=="kissat"&&fallback_used&&!telemetry_only) std::remove(tmp.c_str());
  return code;
}
int main(int argc,char**argv){try{for(int i=1;i+1<argc;i++)if(std::string(argv[i])=="--flow-batch")return flow_batch(argv[i+1]);return solve_main(argc,argv);}catch(std::exception&e){std::cerr<<e.what()<<'\n';return 2;}}
