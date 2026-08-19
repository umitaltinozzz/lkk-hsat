#include "cadical.hpp"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <numeric>
#include <queue>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/resource.h>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

using Clock = std::chrono::steady_clock;
using Clause = std::vector<int>;
static double ms(Clock::time_point a, Clock::time_point b = Clock::now()) {
  return std::chrono::duration<double, std::milli>(b-a).count();
}
static bool lit_less(int a, int b) { return std::pair<int,bool>(std::abs(a),a<0) < std::pair<int,bool>(std::abs(b),b<0); }
static void canon(Clause &c) { std::sort(c.begin(),c.end(),lit_less); c.erase(std::unique(c.begin(),c.end()),c.end()); }
struct VHash { size_t operator()(Clause const& v) const noexcept { size_t h=1469598103934665603ULL; for(int x:v){h^=(uint32_t)x;h*=1099511628211ULL;} return h; } };

struct CNF { int variables=0; std::vector<Clause> clauses; std::string hash; };
static CNF parse_dimacs(const std::string &path) {
  std::ifstream in(path); if(!in) throw std::runtime_error("cannot open CNF");
  CNF out; std::string line; int expected=-1; Clause current;
  while(std::getline(in,line)) {
    if(line.empty() || line[0]=='c') continue;
    if(line[0]=='p') { std::istringstream s(line); std::string p,kind; s>>p>>kind>>out.variables>>expected; if(kind!="cnf") throw std::runtime_error("bad header"); continue; }
    std::istringstream s(line); int lit;
    while(s>>lit) { if(lit) current.push_back(lit); else { canon(current); out.clauses.push_back(current); current.clear(); } }
  }
  if(expected<0 || !current.empty() || (int)out.clauses.size()!=expected) throw std::runtime_error("invalid DIMACS");
  return out;
}

struct Gate { std::string decision,reason; double elapsed=0; long pos=0,neg=0,mixed=0,mirror=0,pairs=0,signal=0; };
static Gate fast_gate(const CNF &cnf) {
  auto st=Clock::now(); Gate g; std::unordered_set<Clause,VHash> db(cnf.clauses.begin(),cnf.clauses.end());
  std::vector<std::pair<int,int>> occ(cnf.variables+1);
  for(auto &c:cnf.clauses) { bool p=c.size()>=2,n=p; for(int l:c){p&=l>0;n&=l<0; auto &x=occ[std::abs(l)]; (l>0?x.first:x.second)++;} if(p)g.pos++;else if(n)g.neg++;else g.mixed++; }
  for(auto &c:cnf.clauses) for(size_t i=0;i<c.size();i++) if(c[i]>0) { Clause m=c; m[i]=-m[i]; canon(m); if(db.count(m))g.mirror++; }
  for(auto x:occ) g.pairs+=(long)x.first*x.second;
  bool structural=(g.pos&&g.neg)||g.mirror; g.decision=structural?"RUN_LKK":"SKIP_TO_CDCL";
  g.reason=g.pos&&g.neg?"monotone_cardinality_signal":(g.mirror?"mirror_recovery_signal":"no_cheap_structural_signal"); g.elapsed=ms(st); return g;
}
static Gate benefit_gate(const CNF &cnf,const Gate &in) {
  auto st=Clock::now(); Gate g; g.signal=in.pos+in.neg+2*in.mirror; std::vector<std::string> r; bool run=true;
  if(g.signal<2){run=false;r.push_back("signal_below_threshold");} if(cnf.clauses.size()>50000){run=false;r.push_back("input_clause_limit");}
  if(in.pairs>2000000){run=false;r.push_back("estimated_resolution_pair_limit");}
  if(run)r.push_back("deterministic_accept(signal_count="+std::to_string(g.signal)+",clauses="+std::to_string(cnf.clauses.size())+")");
  for(size_t i=0;i<r.size();i++){if(i)g.reason+=';';g.reason+=r[i];} g.decision=run?"RUN_LKK":"SKIP_TO_CDCL";g.elapsed=ms(st);return g;
}

struct Record { Clause clause; int depth=0; bool original=true; int pa=-1,pb=-1,pivot=0; };
struct Recovery { std::string status="COMPLETE",reason="fixed_point"; std::vector<Record> records; std::unordered_map<Clause,int,VHash> ids; long derived=0,bytes=0; double elapsed=0; };
static Recovery recover(const CNF &cnf,long derived_budget=5000) {
  auto st=Clock::now(); Recovery r; std::unordered_map<int,std::vector<int>> index; std::vector<int> orig(cnf.variables+1); std::vector<int> agenda;
  for(auto &c:cnf.clauses){for(int l:c)orig[std::abs(l)]++; if(r.ids.count(c))continue;int id=r.records.size();r.ids[c]=id;r.records.push_back({c,0,true,-1,-1,0});agenda.push_back(id);r.bytes+=64+8*c.size();for(int l:c)index[l].push_back(id);}
  std::set<std::pair<int,int>> seen; size_t ai=0;
  auto abort=[&](const std::string &why){r.status="ABORT";r.reason=why;r.elapsed=ms(st);return r;};
  while(ai<agenda.size()) {
    if(ms(st)>250.0)return abort("time_budget"); int li=agenda[ai++]; Record left=r.records[li];
    for(int l:left.clause){if(orig[std::abs(l)]>2)continue; auto rights=index[-l]; for(int ri:rights){if(li==ri)continue;auto pair=std::minmax(li,ri);if(!seen.insert(pair).second)continue;Record right=r.records[ri];int depth=std::max(left.depth,right.depth)+1;if(depth>8)continue;
      std::set<int> ls(left.clause.begin(),left.clause.end()),rs(right.clause.begin(),right.clause.end());std::set<int> piv;
      for(int x:ls)if(rs.count(-x))piv.insert(std::abs(x));if(piv.size()!=1)continue;int pv=*piv.begin();int pivot=ls.count(pv)?pv:-pv;Clause c;
      for(int x:left.clause)if(x!=pivot)c.push_back(x);for(int x:right.clause)if(x!=-pivot)c.push_back(x);canon(c);bool taut=false;for(int x:c)if(std::binary_search(c.begin(),c.end(),-x,lit_less)){taut=true;break;}
      if(taut||c.size()>12||r.ids.count(c))continue;if(r.derived>=derived_budget)return abort("derived_clause_budget");long add=64+8*c.size();if(r.bytes+add>16777216)return abort("memory_budget");
      int id=r.records.size();r.ids[c]=id;r.records.push_back({c,depth,false,li,ri,pivot});r.derived++;r.bytes+=add;agenda.push_back(id);for(int x:c)index[x].push_back(id);
    }}
  } r.elapsed=ms(st);return r;
}

static void combinations(const std::vector<int>& g,int k,int sign,std::vector<Clause>& out) {
  if(k<0||k>(int)g.size())return; std::vector<int> idx(k);std::iota(idx.begin(),idx.end(),0);
  if(k==0){out.push_back({});return;} while(true){Clause c;for(int i:idx)c.push_back(sign*g[i]);canon(c);out.push_back(std::move(c));int p=k-1;while(p>=0&&idx[p]==(int)g.size()-k+p)p--;if(p<0)break;idx[p]++;for(int j=p+1;j<k;j++)idx[j]=idx[j-1]+1;}
}
struct Box { int id,demand; std::vector<int> vars,evidence; };
struct Resource { int id,capacity; std::vector<int> vars,evidence; };
struct Registry { std::string status="UNKNOWN",reason;std::vector<Box> boxes;std::vector<Resource> resources;long checks=0;double elapsed=0; };
struct CandidateKey { std::vector<int> group;int value; bool operator<(CandidateKey const&o)const{return group<o.group||(group==o.group&&value<o.value);} };
static Registry registry(const Recovery &rec) {
  auto st=Clock::now(); Registry out;if(rec.status!="COMPLETE"){out.reason="recovery_"+rec.reason;out.elapsed=ms(st);return out;}const long budget=200000;
  std::vector<const Clause*> pos,neg;for(auto &r:rec.records){bool p=r.clause.size()>=2,n=p;for(int x:r.clause){p&=x>0;n&=x<0;}if(p)pos.push_back(&r.clause);if(n)neg.push_back(&r.clause);}
  std::map<CandidateKey,std::vector<int>> candidates;
  for(auto pc:pos){std::vector<int> pv;for(int x:*pc)pv.push_back(std::abs(x));std::sort(pv.begin(),pv.end());for(auto nc:neg){if(++out.checks>budget){out.reason="family_check_budget";out.elapsed=ms(st);return out;}std::vector<int> nv;for(int x:*nc)nv.push_back(std::abs(x));std::sort(nv.begin(),nv.end());std::vector<int> inter,group;std::set_intersection(pv.begin(),pv.end(),nv.begin(),nv.end(),std::back_inserter(inter));if(inter.size()!=2)continue;std::set_union(pv.begin(),pv.end(),nv.begin(),nv.end(),std::back_inserter(group));int d=nc->size()-1;if(group.size()!=pc->size()+nc->size()-2||d<=0||d>=(int)group.size())continue;
      std::vector<Clause> req;combinations(group,d+1,-1,req);combinations(group,group.size()-d+1,1,req);out.checks+=req.size();if(out.checks>budget){out.reason="family_check_budget";out.elapsed=ms(st);return out;}std::vector<int> ev;bool ok=true;for(auto &c:req){auto it=rec.ids.find(c);if(it==rec.ids.end()){ok=false;break;}ev.push_back(it->second);}if(ok)candidates[{group,d}]=ev;
  }}
  std::vector<std::pair<CandidateKey,std::vector<int>>> ordered(candidates.begin(),candidates.end());std::sort(ordered.begin(),ordered.end(),[](auto&a,auto&b){if(a.first.group.size()!=b.first.group.size())return a.first.group.size()>b.first.group.size();return a.first<b.first;});std::set<int> used;
  for(auto &x:ordered){bool overlap=false;for(int v:x.first.group)overlap|=used.count(v);if(overlap)continue;Box b{(int)out.boxes.size(),x.first.value,x.first.group,x.second};out.boxes.push_back(b);used.insert(b.vars.begin(),b.vars.end());}
  if(out.boxes.empty()){out.reason="no_sound_exact_r_boxes";out.elapsed=ms(st);return out;}std::unordered_map<int,int> vbox;for(auto &b:out.boxes)for(int v:b.vars)vbox[v]=b.id;
  std::map<CandidateKey,std::vector<int>> rcands;
  for(int sz=2;sz<=4;sz++){int cap=sz-1;std::set<std::vector<int>> edges;for(auto nc:neg){if((int)nc->size()!=sz)continue;std::vector<int> vs;std::set<int> bs;bool ok=true;for(int x:*nc){int v=std::abs(x);if(!vbox.count(v)){ok=false;break;}vs.push_back(v);bs.insert(vbox[v]);}std::sort(vs.begin(),vs.end());if(ok&&(int)bs.size()==sz)edges.insert(vs);}std::set<int> avs;for(auto&e:edges)avs.insert(e.begin(),e.end());
    for(auto edge:edges){std::set<int> group(edge.begin(),edge.end());bool changed=true;while(changed){changed=false;std::set<int> occupied;for(int v:group)occupied.insert(vbox[v]);for(int cand:avs){if(group.count(cand)||occupied.count(vbox[cand]))continue;std::vector<int> proposed(group.begin(),group.end());proposed.push_back(cand);std::sort(proposed.begin(),proposed.end());std::vector<Clause> combos;combinations(proposed,sz,1,combos);bool clique=true;for(auto c:combos){std::vector<int> positive;for(int x:c)positive.push_back(std::abs(x));if(!edges.count(positive)){clique=false;break;}}if(++out.checks>budget){out.reason="family_check_budget";out.elapsed=ms(st);return out;}if(clique){group.insert(cand);changed=true;break;}}}
      if((int)group.size()<=cap)continue;std::vector<int> gv(group.begin(),group.end());std::vector<Clause> req;combinations(gv,sz,-1,req);std::vector<int> ev;bool ok=true;for(auto &c:req){auto it=rec.ids.find(c);if(it==rec.ids.end()){ok=false;break;}ev.push_back(it->second);}if(ok)rcands[{gv,cap}]=ev;
  }}
  std::vector<std::pair<CandidateKey,std::vector<int>>> ro(rcands.begin(),rcands.end());std::sort(ro.begin(),ro.end(),[](auto&a,auto&b){if(a.first.group.size()!=b.first.group.size())return a.first.group.size()>b.first.group.size();if(a.first.value!=b.first.value)return a.first.value<b.first.value;return a.first.group<b.first.group;});std::set<int> ru;
  for(auto &x:ro){bool overlap=false;for(int v:x.first.group)overlap|=ru.count(v);if(overlap)continue;Resource r{(int)out.resources.size(),x.first.value,x.first.group,x.second};out.resources.push_back(r);ru.insert(r.vars.begin(),r.vars.end());}
  if(used!=ru){std::ostringstream s;s<<"incomplete_or_ambiguous_resource_mapping:[";bool first=true;for(int v:used)if(!ru.count(v)){if(!first)s<<", ";first=false;s<<v;}s<<"]";out.reason=s.str();out.elapsed=ms(st);return out;}
  out.status="COMPLETE";out.reason="sound_exact_r_and_capacity_families";out.elapsed=ms(st);return out;
}

struct Edge{int to,rev,cap,original;};
struct FlowResult {std::string result="FEASIBLE";int maximum=0,demand=0,nodes=0,edges=0;double elapsed=0;std::vector<int> rb,rr;int source_cut=0,edge_cut=0,resource_cut=0,cut=0;};
static FlowResult flow_test(const Registry &reg) {
  auto st=Clock::now();FlowResult out;int B=reg.boxes.size(),R=reg.resources.size(),src=0,bo=1,ro=bo+B,sink=ro+R;out.nodes=sink+1;std::vector<std::vector<Edge>> g(out.nodes);int forward=0;
  auto add=[&](int a,int b,int c){g[a].push_back({b,(int)g[b].size(),c,c});g[b].push_back({a,(int)g[a].size()-1,0,0});forward++;};std::unordered_map<int,int> vr;for(auto&r:reg.resources)for(int v:r.vars)vr[v]=r.id;
  for(auto&b:reg.boxes){add(src,bo+b.id,b.demand);out.demand+=b.demand;for(int v:b.vars)add(bo+b.id,ro+vr[v],1);}for(auto&r:reg.resources)add(ro+r.id,sink,r.capacity);out.edges=forward;
  while(true){std::vector<int> lev(out.nodes,-1);std::queue<int>q;q.push(src);lev[src]=0;while(!q.empty()){int v=q.front();q.pop();for(auto&e:g[v])if(e.cap&&lev[e.to]<0){lev[e.to]=lev[v]+1;q.push(e.to);}}if(lev[sink]<0)break;std::vector<int>it(out.nodes);std::function<int(int,int)> dfs=[&](int v,int f){if(v==sink)return f;for(int&i=it[v];i<(int)g[v].size();i++){Edge&e=g[v][i];if(e.cap&&lev[e.to]==lev[v]+1){int x=dfs(e.to,std::min(f,e.cap));if(x){e.cap-=x;g[e.to][e.rev].cap+=x;return x;}}}return 0;};while(int x=dfs(src,1<<28))out.maximum+=x;}
  if(out.maximum<out.demand){out.result="UNSAT";std::vector<int>seen(out.nodes);std::queue<int>q;q.push(src);seen[src]=1;while(!q.empty()){int v=q.front();q.pop();for(auto&e:g[v])if(e.cap&&!seen[e.to])seen[e.to]=1,q.push(e.to);}for(int i=0;i<B;i++)if(seen[bo+i])out.rb.push_back(i);for(int i=0;i<R;i++)if(seen[ro+i])out.rr.push_back(i);std::set<int>RB(out.rb.begin(),out.rb.end()),RR(out.rr.begin(),out.rr.end());for(auto&b:reg.boxes)if(!RB.count(b.id))out.source_cut+=b.demand;for(auto&b:reg.boxes)if(RB.count(b.id))for(int v:b.vars)if(!RR.count(vr[v]))out.edge_cut++;for(auto&r:reg.resources)if(RR.count(r.id))out.resource_cut+=r.capacity;out.cut=out.source_cut+out.edge_cut+out.resource_cut;}
  out.elapsed=ms(st);return out;
}

static void ints(std::ostream&o,const std::vector<int>&v){o<<'[';for(size_t i=0;i<v.size();i++){if(i)o<<',';o<<v[i];}o<<']';}
static void write_witness(const std::string &path,const std::string &sha,const CNF&cnf,const Recovery&rec,const Registry&reg,const FlowResult&f){std::ofstream o(path);o<<"{\"format\":\"lkk-hsat-capacity-witness-v1\",\"cnf_sha256\":\""<<sha<<"\",\"cnf_variables\":"<<cnf.variables<<",\"cnf_clause_count\":"<<cnf.clauses.size()<<",\"clause_database\":[";for(size_t i=0;i<rec.records.size();i++){if(i)o<<',';auto&r=rec.records[i];o<<"{\"id\":"<<i<<",\"clause\":";ints(o,r.clause);o<<",\"depth\":"<<r.depth<<",\"original\":"<<(r.original?"true":"false")<<",\"parent_a\":"<<(r.pa<0?"null":std::to_string(r.pa))<<",\"parent_b\":"<<(r.pb<0?"null":std::to_string(r.pb))<<",\"pivot\":"<<(r.original?"null":std::to_string(r.pivot))<<'}';}o<<"],\"boxes\":[";for(size_t i=0;i<reg.boxes.size();i++){if(i)o<<',';auto&b=reg.boxes[i];o<<"{\"id\":"<<b.id<<",\"variables\":";ints(o,b.vars);o<<",\"demand\":"<<b.demand<<",\"evidence_clause_ids\":";ints(o,b.evidence);o<<'}';}o<<"],\"resources\":[";for(size_t i=0;i<reg.resources.size();i++){if(i)o<<',';auto&r=reg.resources[i];o<<"{\"id\":"<<r.id<<",\"variables\":";ints(o,r.vars);o<<",\"capacity\":"<<r.capacity<<",\"evidence_clause_ids\":";ints(o,r.evidence);o<<'}';}o<<"],\"variable_to_resource\":{";bool first=true;for(auto&r:reg.resources)for(int v:r.vars){if(!first)o<<',';first=false;o<<'\"'<<v<<"\":"<<r.id;}o<<"},\"flow\":{\"total_demand\":"<<f.demand<<",\"maximum_flow\":"<<f.maximum<<",\"nodes\":"<<f.nodes<<",\"edges\":"<<f.edges<<",\"reachable_boxes\":";ints(o,f.rb);o<<",\"reachable_resources\":";ints(o,f.rr);o<<",\"source_cut_capacity\":"<<f.source_cut<<",\"box_resource_cut_capacity\":"<<f.edge_cut<<",\"resource_cut_capacity\":"<<f.resource_cut<<",\"cut_capacity\":"<<f.cut<<"}}\n";}
static std::string q(const std::string&s){std::string r="\"";for(char c:s){if(c=='\"'||c=='\\')r+='\\';r+=c;}return r+'\"';}

static std::vector<int> split_ints(const std::string&s,char delim=','){std::vector<int>v;std::stringstream ss(s);std::string x;while(std::getline(ss,x,delim))if(!x.empty())v.push_back(std::stoi(x));return v;}
static int flow_batch(const std::string &path){std::ifstream in(path);if(!in)throw std::runtime_error("cannot open flow batch");std::cout<<"seed,flow_result,maximum_flow,total_demand,flow_ms\n";std::string line;while(std::getline(in,line)){std::stringstream ss(line);std::string seed,ds,cs,rs;std::getline(ss,seed,'|');std::getline(ss,ds,'|');std::getline(ss,cs,'|');std::getline(ss,rs);auto d=split_ints(ds),c=split_ints(cs);std::vector<std::vector<int>> reach;std::stringstream rr(rs);std::string row;while(std::getline(rr,row,';'))reach.push_back(split_ints(row));Registry reg;reg.status="COMPLETE";int var=1;for(size_t i=0;i<d.size();i++){Box b{(int)i,d[i],{}, {}};for(int resource:reach[i]){b.vars.push_back(var);if(resource<0||resource>=(int)c.size())throw std::runtime_error("flow resource out of range");while(reg.resources.size()<c.size())reg.resources.push_back({(int)reg.resources.size(),c[reg.resources.size()],{}, {}});reg.resources[resource].vars.push_back(var++);}reg.boxes.push_back(b);}while(reg.resources.size()<c.size())reg.resources.push_back({(int)reg.resources.size(),c[reg.resources.size()],{}, {}});auto f=flow_test(reg);std::cout<<seed<<','<<f.result<<','<<f.maximum<<','<<f.demand<<','<<f.elapsed<<'\n';}return 0;}

static int solve_main(int argc,char**argv){std::string path,witness,sha;int seed=1;long db=5000;for(int i=1;i<argc;i++){std::string a=argv[i];if(a=="--cnf")path=argv[++i];else if(a=="--witness")witness=argv[++i];else if(a=="--sha256")sha=argv[++i];else if(a=="--seed")seed=std::stoi(argv[++i]);else if(a=="--derived-budget")db=std::stol(argv[++i]);}
  auto total=Clock::now(),st=Clock::now();CNF cnf=parse_dimacs(path);double parse=ms(st);st=Clock::now();Gate gate=fast_gate(cnf);Gate benefit;double recovery_ms=0,registry_ms=0,flow_ms=0,witness_ms=0,fallback=0;Recovery rec;Registry reg;FlowResult flow;std::string structural="UNKNOWN",final="UNKNOWN",abort="";bool fallback_used=true,witness_written=false;
  if(gate.decision=="RUN_LKK"){benefit=benefit_gate(cnf,gate);if(benefit.decision=="RUN_LKK"){rec=recover(cnf,db);recovery_ms=rec.elapsed;abort=rec.status=="ABORT"?rec.reason:"";reg=registry(rec);registry_ms=reg.elapsed;if(reg.status=="COMPLETE"){flow=flow_test(reg);flow_ms=flow.elapsed;structural=flow.result;if(flow.result=="UNSAT"){st=Clock::now();write_witness(witness,sha,cnf,rec,reg,flow);witness_ms=ms(st);witness_written=true;final="UNSATISFIABLE";fallback_used=false;}}}}
  int code=0;if(fallback_used){st=Clock::now();CaDiCaL::Solver solver;solver.set("quiet",1);solver.set("seed",seed);for(auto&c:cnf.clauses){for(int l:c)solver.add(l);solver.add(0);}int res=solver.solve();fallback=ms(st);final=res==10?"SATISFIABLE":res==20?"UNSATISFIABLE":"UNKNOWN";code=res==10?10:res==20?20:0;}
  struct rusage u{};getrusage(RUSAGE_SELF,&u);std::cout<<"{\"implementation\":\"native_phase4a\",\"instance\":"<<q(path)<<",\"parse_ms\":"<<parse<<",\"gate_ms\":"<<gate.elapsed<<",\"gate_decision\":"<<q(gate.decision)<<",\"gate_reason\":"<<q(gate.reason)<<",\"benefit_gate_ms\":"<<benefit.elapsed<<",\"benefit_gate_decision\":"<<q(benefit.decision.empty()?"NOT_RUN":benefit.decision)<<",\"recovery_ms\":"<<recovery_ms<<",\"recovery_status\":"<<q(rec.status)<<",\"recovery_abort_reason\":"<<q(abort)<<",\"registry_ms\":"<<registry_ms<<",\"registry_status\":"<<q(reg.status)<<",\"registry_reason\":"<<q(reg.reason)<<",\"family_checks\":"<<reg.checks<<",\"flow_ms\":"<<flow_ms<<",\"witness_ms\":"<<witness_ms<<",\"fallback_ms\":"<<fallback<<",\"total_end_to_end_ms\":"<<ms(total)<<",\"peak_rss_kb\":"<<u.ru_maxrss<<",\"boxes_found\":"<<reg.boxes.size()<<",\"capacity_groups\":"<<reg.resources.size()<<",\"flow_nodes\":"<<flow.nodes<<",\"flow_edges\":"<<flow.edges<<",\"structural_result\":"<<q(structural)<<",\"final_result\":"<<q(final)<<",\"fallback_used\":"<<(fallback_used?"true":"false")<<",\"witness_written\":"<<(witness_written?"true":"false")<<"}\n";return code;}
int main(int argc,char**argv){try{for(int i=1;i+1<argc;i++)if(std::string(argv[i])=="--flow-batch")return flow_batch(argv[i+1]);return solve_main(argc,argv);}catch(std::exception&e){std::cerr<<e.what()<<'\n';return 2;}}
