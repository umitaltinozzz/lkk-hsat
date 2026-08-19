// Phase 4B engine. The accepted LKK-HSAT 3.0 structural semantics of
// native/phase4a/lkk_native.cpp are copied verbatim: parse_dimacs, fast_gate,
// benefit_gate, recover, registry, flow_test and write_witness are unchanged,
// and the structural answer is still produced only by registry()+flow_test().
// Phase 4B adds, strictly around that unchanged core:
//   * a selectable fallback engine (in-process CaDiCaL, external Kissat, none),
//   * CaDiCaL search statistics through the pinned 3.0.1 API,
//   * LemmaBridge candidate generation, which never changes the answer and is
//     only injected after independent verification outside this binary.
#include "cadical.hpp"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
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
#include <sys/wait.h>
#include <unistd.h>
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

// ---------------------------------------------------------------------------
// Phase 4B additions start here. Nothing above this line is reachable from the
// new code in a way that can change the accepted structural answer.
// ---------------------------------------------------------------------------

// Harvest is the unchanged registry search re-run with a Phase 4B budget that
// keeps whatever was already proven when the accepted registry gives up. Every
// retained candidate still required all of its family clauses to be present in
// the recovered database, so each one is individually implied by the CNF. The
// difference from registry() is only that a partial, variable-disjoint
// selection is returned instead of UNKNOWN.
struct Harvest { std::vector<Box> boxes; std::vector<Resource> resources; long checks=0; bool box_budget_hit=false,resource_budget_hit=false; double elapsed=0; };
static Harvest harvest(const Recovery &rec,long budget) {
  auto st=Clock::now(); Harvest out; if(rec.status!="COMPLETE"){out.elapsed=ms(st);return out;}
  std::vector<const Clause*> pos,neg;for(auto &r:rec.records){bool p=r.clause.size()>=2,n=p;for(int x:r.clause){p&=x>0;n&=x<0;}if(p)pos.push_back(&r.clause);if(n)neg.push_back(&r.clause);}
  std::map<CandidateKey,std::vector<int>> candidates;
  for(auto pc:pos){ if(out.box_budget_hit)break; std::vector<int> pv;for(int x:*pc)pv.push_back(std::abs(x));std::sort(pv.begin(),pv.end());
    for(auto nc:neg){ if(++out.checks>budget){out.box_budget_hit=true;break;}
      std::vector<int> nv;for(int x:*nc)nv.push_back(std::abs(x));std::sort(nv.begin(),nv.end());std::vector<int> inter,group;
      std::set_intersection(pv.begin(),pv.end(),nv.begin(),nv.end(),std::back_inserter(inter));if(inter.size()!=2)continue;
      std::set_union(pv.begin(),pv.end(),nv.begin(),nv.end(),std::back_inserter(group));int d=nc->size()-1;
      if(group.size()!=pc->size()+nc->size()-2||d<=0||d>=(int)group.size())continue;
      std::vector<Clause> req;combinations(group,d+1,-1,req);combinations(group,group.size()-d+1,1,req);out.checks+=req.size();
      if(out.checks>budget){out.box_budget_hit=true;break;}
      std::vector<int> ev;bool ok=true;for(auto &c:req){auto it=rec.ids.find(c);if(it==rec.ids.end()){ok=false;break;}ev.push_back(it->second);}
      if(ok)candidates[{group,d}]=ev;
  }}
  std::vector<std::pair<CandidateKey,std::vector<int>>> ordered(candidates.begin(),candidates.end());
  std::sort(ordered.begin(),ordered.end(),[](auto&a,auto&b){if(a.first.group.size()!=b.first.group.size())return a.first.group.size()>b.first.group.size();return a.first<b.first;});
  std::set<int> used;
  for(auto &x:ordered){bool overlap=false;for(int v:x.first.group)overlap|=used.count(v);if(overlap)continue;Box b{(int)out.boxes.size(),x.first.value,x.first.group,x.second};out.boxes.push_back(b);used.insert(b.vars.begin(),b.vars.end());}
  if(out.boxes.empty()){out.elapsed=ms(st);return out;}
  std::unordered_map<int,int> vbox;for(auto &b:out.boxes)for(int v:b.vars)vbox[v]=b.id;
  std::map<CandidateKey,std::vector<int>> rcands;
  for(int sz=2;sz<=4&&!out.resource_budget_hit;sz++){int cap=sz-1;std::set<std::vector<int>> edges;
    for(auto nc:neg){if((int)nc->size()!=sz)continue;std::vector<int> vs;std::set<int> bs;bool ok=true;for(int x:*nc){int v=std::abs(x);if(!vbox.count(v)){ok=false;break;}vs.push_back(v);bs.insert(vbox[v]);}std::sort(vs.begin(),vs.end());if(ok&&(int)bs.size()==sz)edges.insert(vs);}
    std::set<int> avs;for(auto&e:edges)avs.insert(e.begin(),e.end());
    for(auto edge:edges){ if(out.resource_budget_hit)break; std::set<int> group(edge.begin(),edge.end());bool changed=true;
      while(changed&&!out.resource_budget_hit){changed=false;std::set<int> occupied;for(int v:group)occupied.insert(vbox[v]);
        for(int cand:avs){if(group.count(cand)||occupied.count(vbox[cand]))continue;std::vector<int> proposed(group.begin(),group.end());proposed.push_back(cand);std::sort(proposed.begin(),proposed.end());
          std::vector<Clause> combos;combinations(proposed,sz,1,combos);bool clique=true;
          for(auto c:combos){std::vector<int> positive;for(int x:c)positive.push_back(std::abs(x));if(!edges.count(positive)){clique=false;break;}}
          if(++out.checks>budget){out.resource_budget_hit=true;break;}
          if(clique){group.insert(cand);changed=true;break;}}}
      if((int)group.size()<=cap)continue;std::vector<int> gv(group.begin(),group.end());std::vector<Clause> req;combinations(gv,sz,-1,req);
      std::vector<int> ev;bool ok=true;for(auto &c:req){auto it=rec.ids.find(c);if(it==rec.ids.end()){ok=false;break;}ev.push_back(it->second);}
      if(ok)rcands[{gv,cap}]=ev;
  }}
  std::vector<std::pair<CandidateKey,std::vector<int>>> ro(rcands.begin(),rcands.end());
  std::sort(ro.begin(),ro.end(),[](auto&a,auto&b){if(a.first.group.size()!=b.first.group.size())return a.first.group.size()>b.first.group.size();if(a.first.value!=b.first.value)return a.first.value<b.first.value;return a.first.group<b.first.group;});
  std::set<int> ru;
  for(auto &x:ro){bool overlap=false;for(int v:x.first.group)overlap|=ru.count(v);if(overlap)continue;Resource r{(int)out.resources.size(),x.first.value,x.first.group,x.second};out.resources.push_back(r);ru.insert(r.vars.begin(),r.vars.end());}
  out.elapsed=ms(st);return out;
}

// Restrict the harvest to a sub-model the flow argument can be run on soundly.
// A box asserts that at least `demand` of its variables are true, so a box may
// only be kept when *every* one of its variables is covered by a retained
// resource group and those groups are distinct: dropping a variable would
// strengthen the at-least-r side into something the CNF does not imply.
// Resource groups may keep variables belonging to no retained box, since
// at-most-c over a superset still bounds the retained subset.
struct SubModel { Registry reg; std::unordered_map<int,int> var_box,var_res; bool usable=false; };
static SubModel restrict_to_covered(const Harvest &h) {
  SubModel s; std::unordered_map<int,int> cover; for(auto &r:h.resources) for(int v:r.vars) cover[v]=r.id;
  std::set<int> kept_res;
  for(auto &b:h.boxes){ if(b.demand<=0) continue; std::set<int> res; bool ok=true;
    for(int v:b.vars){ auto it=cover.find(v); if(it==cover.end()||!res.insert(it->second).second){ ok=false; break; } }
    if(!ok) continue;
    Box nb{(int)s.reg.boxes.size(),b.demand,b.vars,b.evidence}; s.reg.boxes.push_back(nb); kept_res.insert(res.begin(),res.end());
  }
  if(s.reg.boxes.empty()) return s;
  std::unordered_map<int,int> remap;
  for(auto &r:h.resources) if(kept_res.count(r.id)){ int id=s.reg.resources.size(); remap[r.id]=id; s.reg.resources.push_back({id,r.capacity,r.vars,r.evidence}); }
  for(auto &b:s.reg.boxes) for(int v:b.vars) s.var_box[v]=b.id;
  for(auto &r:s.reg.resources) for(int v:r.vars) s.var_res[v]=r.id;
  s.reg.status="COMPLETE"; s.usable=true; return s;
}

// A conditioning is a set of positive literals assumed true. It consumes one
// unit of demand in the owning box and one unit of capacity at the owning
// resource. If the residual model has no feasible flow, no total assignment can
// satisfy the CNF together with that conditioning, so the negated conditioning
// is implied.
// OK: run the flow test on `out`. OVERCOMMITTED: the conditioning already
// exceeds a proven demand or capacity, which refutes it outright.
// INAPPLICABLE: the conditioning does not belong to this sub-model.
enum class Residual { OK, OVERCOMMITTED, INAPPLICABLE };
static Residual residual(const SubModel &s,const std::vector<int> &assume,Registry &out) {
  out=s.reg; std::vector<int> used(out.resources.size(),0);
  for(int v:assume){ auto b=s.var_box.find(v),r=s.var_res.find(v);
    if(b==s.var_box.end()||r==s.var_res.end()) return Residual::INAPPLICABLE;
    Box &box=out.boxes[b->second]; auto it=std::find(box.vars.begin(),box.vars.end(),v);
    if(it==box.vars.end()) return Residual::INAPPLICABLE;
    box.vars.erase(it); box.demand--; used[r->second]++;
  }
  for(size_t i=0;i<out.resources.size();i++) out.resources[i].capacity-=used[i];
  for(auto &b:out.boxes) if(b.demand<0) return Residual::OVERCOMMITTED;
  for(auto &r:out.resources) if(r.capacity<0) return Residual::OVERCOMMITTED;
  std::vector<Box> keep; for(auto &b:out.boxes) if(b.demand>0){ b.id=keep.size(); keep.push_back(b); }
  out.boxes=keep; return Residual::OK;
}

struct Lemma { Clause clause; std::string origin; std::vector<int> support; int parent_a=-1,parent_b=-1,pivot=0,depth=0; };
struct LemmaSet {
  std::vector<Lemma> lemmas; double elapsed=0; long flow_calls=0;
  bool structural_unsat=false; std::string structural_unsat_reason;
  int sub_boxes=0,sub_resources=0; std::string note;
};

// Index of the original formula, used to drop any candidate the CNF already
// contains or already subsumes: such a clause is not new information.
struct OriginalIndex {
  std::unordered_set<Clause,VHash> exact; std::unordered_map<int,std::vector<int>> occ; const std::vector<Clause> *all=nullptr;
  void build(const std::vector<Clause> &cs){ all=&cs; for(size_t i=0;i<cs.size();i++){ exact.insert(cs[i]); for(int l:cs[i]) occ[l].push_back((int)i); } }
  bool redundant(const Clause &c,long &work) const {
    if(exact.count(c)) return true;
    const std::vector<int> *best=nullptr;
    for(int l:c){ auto it=occ.find(l); if(it==occ.end()) return false; if(!best||it->second.size()<best->size()) best=&it->second; }
    if(!best) return false;
    std::unordered_set<int> lits(c.begin(),c.end());
    for(int id:*best){ const Clause &o=(*all)[id]; work++; if(o.size()>c.size()) continue;
      bool sub=true; for(int l:o) if(!lits.count(l)){ sub=false; break; }
      if(sub) return true; }
    return false;
  }
};

static LemmaSet generate_lemmas(const CNF &cnf,const Recovery &rec,const Harvest &h,
                                int max_lemmas,int max_len,long flow_budget) {
  auto st=Clock::now(); LemmaSet out; OriginalIndex orig; orig.build(cnf.clauses);
  long work=0; std::unordered_set<Clause,VHash> emitted;
  auto push=[&](Clause c,const std::string &origin,const std::vector<int> &support,int pa,int pb,int pivot,int depth){
    if(c.empty()||(int)c.size()>max_len) return false;
    if((int)out.lemmas.size()>=max_lemmas) return false;
    if(!emitted.insert(c).second) return false;
    if(orig.redundant(c,work)){ emitted.erase(c); return false; }
    out.lemmas.push_back({c,origin,support,pa,pb,pivot,depth}); return true;
  };

  // Source 1: family clauses of harvested exact-r boxes and capacity groups.
  // Each is present in the recovered database by the harvest acceptance test,
  // so it is either an original clause (dropped as redundant) or a resolvent.
  for(auto &b:h.boxes){
    std::vector<Clause> req; combinations(b.vars,b.demand+1,-1,req); combinations(b.vars,(int)b.vars.size()-b.demand+1,1,req);
    for(auto &c:req){ auto it=rec.ids.find(c); if(it==rec.ids.end()) continue; auto &r=rec.records[it->second];
      push(c,"exact_r_family",b.vars,r.pa,r.pb,r.pivot,r.depth); }
  }
  for(auto &r:h.resources){
    std::vector<Clause> req; combinations(r.vars,r.capacity+1,-1,req);
    for(auto &c:req){ auto it=rec.ids.find(c); if(it==rec.ids.end()) continue; auto &rr=rec.records[it->second];
      push(c,"capacity_family",r.vars,rr.pa,rr.pb,rr.pivot,rr.depth); }
  }

  // Source 2: bounded-resolution resolvents. Sound by construction; the
  // resolution parents are exported so the transfer can be replayed offline.
  for(size_t i=0;i<rec.records.size();i++){ auto &r=rec.records[i]; if(r.original) continue;
    push(r.clause,"bounded_resolution_resolvent",{},r.pa,r.pb,r.pivot,r.depth); }

  // Source 3: conditional capacity conflicts over the harvested sub-model.
  SubModel sub=restrict_to_covered(h);
  out.sub_boxes=sub.reg.boxes.size(); out.sub_resources=sub.reg.resources.size();
  if(!sub.usable){ out.note="no_covered_sub_model"; out.elapsed=ms(st); return out; }
  FlowResult base=flow_test(sub.reg); out.flow_calls++;
  if(base.result=="UNSAT"){
    // The harvested structure alone already refutes the formula. Expressing
    // that as ordinary clauses is degenerate (it entails every unit clause), so
    // no conditional lemma is emitted; the finding is reported instead.
    out.structural_unsat=true;
    out.structural_unsat_reason="harvested_sub_model_max_flow_"+std::to_string(base.maximum)+"_below_demand_"+std::to_string(base.demand);
    out.note="structural_unsat_no_conditional_lemmas"; out.elapsed=ms(st); return out;
  }
  std::vector<int> vars; for(auto &b:sub.reg.boxes) for(int v:b.vars) vars.push_back(v);
  std::sort(vars.begin(),vars.end());
  Registry res;
  auto conflicts=[&](const std::vector<int> &assume)->bool{
    Residual state=residual(sub,assume,res);
    if(state==Residual::INAPPLICABLE) return false;
    if(state==Residual::OVERCOMMITTED) return true;
    out.flow_calls++; return flow_test(res).result=="UNSAT"; };
  for(int v:vars){ if(out.flow_calls>=flow_budget) break;
    if(conflicts({v})) push({-v},"conditional_capacity_conflict",{v},-1,-1,0,0); }
  for(size_t i=0;i<vars.size()&&out.flow_calls<flow_budget;i++)
    for(size_t j=i+1;j<vars.size()&&out.flow_calls<flow_budget;j++){
      int a=vars[i],b=vars[j];
      auto ra=sub.var_res.find(a),rb=sub.var_res.find(b); auto ba=sub.var_box.find(a),bb=sub.var_box.find(b);
      // Only pairs that interact locally can produce a conflict the unit pass
      // missed: same resource group, or same box.
      if(!(ra->second==rb->second||ba->second==bb->second)) continue;
      if(conflicts({a,b})){ Clause c{-a,-b}; canon(c); push(c,"conditional_capacity_conflict",{a,b},-1,-1,0,0); }
    }
  out.note="conditional_pass_complete"; out.elapsed=ms(st); return out;
}

static void write_lemmas(const std::string &path,const std::string &sha,const CNF &cnf,const LemmaSet &ls,
                         const Harvest &h,const Recovery &rec) {
  std::ofstream o(path);
  o<<"{\"format\":\"lkk-hsat-lemmabridge-v1\",\"cnf_sha256\":\""<<sha<<"\",\"cnf_variables\":"<<cnf.variables
   <<",\"cnf_clause_count\":"<<cnf.clauses.size()<<",\"generation_ms\":"<<ls.elapsed<<",\"flow_calls\":"<<ls.flow_calls
   <<",\"harvest_boxes\":"<<h.boxes.size()<<",\"harvest_resources\":"<<h.resources.size()
   <<",\"harvest_checks\":"<<h.checks<<",\"harvest_ms\":"<<h.elapsed
   <<",\"sub_model_boxes\":"<<ls.sub_boxes<<",\"sub_model_resources\":"<<ls.sub_resources
   <<",\"structural_unsat_detected\":"<<(ls.structural_unsat?"true":"false")
   <<",\"structural_unsat_reason\":"<<q(ls.structural_unsat_reason)<<",\"note\":"<<q(ls.note);
  // The full resolution database and the harvested families travel with the
  // lemmas so an offline checker can replay every derivation from the CNF
  // alone, independently of this binary.
  o<<",\"clause_database\":[";
  for(size_t i=0;i<rec.records.size();i++){ if(i)o<<','; auto &r=rec.records[i];
    o<<"{\"id\":"<<i<<",\"clause\":"; ints(o,r.clause);
    o<<",\"depth\":"<<r.depth<<",\"original\":"<<(r.original?"true":"false")
     <<",\"parent_a\":"<<(r.pa<0?"null":std::to_string(r.pa))
     <<",\"parent_b\":"<<(r.pb<0?"null":std::to_string(r.pb))
     <<",\"pivot\":"<<(r.original?"null":std::to_string(r.pivot))<<'}'; }
  o<<"],\"harvested_boxes\":[";
  for(size_t i=0;i<h.boxes.size();i++){ if(i)o<<','; auto &b=h.boxes[i];
    o<<"{\"id\":"<<b.id<<",\"variables\":"; ints(o,b.vars); o<<",\"demand\":"<<b.demand
     <<",\"evidence_clause_ids\":"; ints(o,b.evidence); o<<'}'; }
  o<<"],\"harvested_resources\":[";
  for(size_t i=0;i<h.resources.size();i++){ if(i)o<<','; auto &r=h.resources[i];
    o<<"{\"id\":"<<r.id<<",\"variables\":"; ints(o,r.vars); o<<",\"capacity\":"<<r.capacity
     <<",\"evidence_clause_ids\":"; ints(o,r.evidence); o<<'}'; }
  o<<"],\"lemmas\":[";
  for(size_t i=0;i<ls.lemmas.size();i++){ if(i)o<<','; auto &l=ls.lemmas[i];
    o<<"{\"id\":"<<i<<",\"clause\":"; ints(o,l.clause);
    o<<",\"length\":"<<l.clause.size()<<",\"origin\":"<<q(l.origin)<<",\"support\":"; ints(o,l.support);
    o<<",\"parent_a\":"<<(l.parent_a<0?"null":std::to_string(l.parent_a))
     <<",\"parent_b\":"<<(l.parent_b<0?"null":std::to_string(l.parent_b))
     <<",\"pivot\":"<<(l.parent_a<0?"null":std::to_string(l.pivot))<<",\"depth\":"<<l.depth<<'}';
  }
  o<<"]}\n";
}

// Verified lemmas come back as one canonical clause per line. Anything not in
// this file is never given to the solver.
static std::unordered_set<Clause,VHash> read_accepted(const std::string &path) {
  std::unordered_set<Clause,VHash> out; std::ifstream in(path);
  if(!in) throw std::runtime_error("cannot open accepted lemma file");
  std::string line; while(std::getline(in,line)){ if(line.empty()) continue; std::istringstream s(line); Clause c; int l;
    while(s>>l) if(l) c.push_back(l); canon(c); if(!c.empty()) out.insert(c); }
  return out;
}

struct Fallback { std::string status="UNKNOWN"; double total_ms=0,serialize_ms=0,solver_self_ms=0,overhead_ms=0; int exit_code=0;
  long long conflicts=-1,decisions=-1,propagations=-1,restarts=-1,learned=-1,irredundant=-1,redundant=-1,ticks=-1; };

// Identical to the accepted Phase 4A fallback except for the extra verified
// clauses and the statistics read-out; the external timeout still bounds it.
static Fallback run_cadical(const CNF &cnf,const std::vector<Clause> &extra,int seed) {
  auto st=Clock::now(); Fallback fb; CaDiCaL::Solver solver; solver.set("quiet",1); solver.set("seed",seed);
  for(auto &c:cnf.clauses){ for(int l:c) solver.add(l); solver.add(0); }
  for(auto &c:extra){ for(int l:c) solver.add(l); solver.add(0); }
  int res=solver.solve(); fb.total_ms=ms(st); fb.solver_self_ms=fb.total_ms; fb.overhead_ms=0;
  fb.status=res==10?"SATISFIABLE":res==20?"UNSATISFIABLE":"UNKNOWN"; fb.exit_code=res==10?10:res==20?20:0;
  fb.conflicts=solver.get_statistic_value("conflicts"); fb.decisions=solver.get_statistic_value("decisions");
  fb.propagations=solver.get_statistic_value("propagations"); fb.ticks=solver.get_statistic_value("ticks");
  fb.irredundant=solver.get_statistic_value("irredundant"); fb.redundant=solver.get_statistic_value("redundant");
  return fb;
}

// Kissat cannot take clauses in-process, so the hybrid pays a DIMACS write and
// a fork/exec. Both are measured here and reported separately from the solver's
// own reported process time.
static Fallback run_kissat(const CNF &cnf,const std::vector<Clause> &extra,const std::string &kissat,
                           const std::string &tmp,const std::string &log,int seed,double timeout_s) {
  Fallback fb; auto sst=Clock::now();
  { std::ofstream o(tmp); o<<"p cnf "<<cnf.variables<<' '<<(cnf.clauses.size()+extra.size())<<'\n';
    for(auto &c:cnf.clauses){ for(int l:c) o<<l<<' '; o<<"0\n"; }
    for(auto &c:extra){ for(int l:c) o<<l<<' '; o<<"0\n"; } }
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

// Unchanged Phase 4A flow/exhaustive cross-check driver, retained so the
// correctness campaign exercises this binary's own max-flow code.
static std::vector<int> split_ints(const std::string&s,char delim=','){std::vector<int>v;std::stringstream ss(s);std::string x;while(std::getline(ss,x,delim))if(!x.empty())v.push_back(std::stoi(x));return v;}
static int flow_batch(const std::string &path){std::ifstream in(path);if(!in)throw std::runtime_error("cannot open flow batch");std::cout<<"seed,flow_result,maximum_flow,total_demand,flow_ms\n";std::string line;while(std::getline(in,line)){std::stringstream ss(line);std::string seed,ds,cs,rs;std::getline(ss,seed,'|');std::getline(ss,ds,'|');std::getline(ss,cs,'|');std::getline(ss,rs);auto d=split_ints(ds),c=split_ints(cs);std::vector<std::vector<int>> reach;std::stringstream rr(rs);std::string row;while(std::getline(rr,row,';'))reach.push_back(split_ints(row));Registry reg;reg.status="COMPLETE";int var=1;for(size_t i=0;i<d.size();i++){Box b{(int)i,d[i],{}, {}};for(int resource:reach[i]){b.vars.push_back(var);if(resource<0||resource>=(int)c.size())throw std::runtime_error("flow resource out of range");while(reg.resources.size()<c.size())reg.resources.push_back({(int)reg.resources.size(),c[reg.resources.size()],{}, {}});reg.resources[resource].vars.push_back(var++);}reg.boxes.push_back(b);}while(reg.resources.size()<c.size())reg.resources.push_back({(int)reg.resources.size(),c[reg.resources.size()],{}, {}});auto f=flow_test(reg);std::cout<<seed<<','<<f.result<<','<<f.maximum<<','<<f.demand<<','<<f.elapsed<<'\n';}return 0;}

static int solve_main(int argc,char**argv){
  std::string path,witness,sha,lemma_out,accepted_path,engine="cadical",kissat="/opt/solvers/bin/kissat",tmp,kissat_log;
  int seed=1,max_lemmas=200000,max_len=12; long db=5000,harvest_budget=2000000,flow_budget=200000;
  bool lemmas_on=false,generate_only=false; double timeout_s=0;
  for(int i=1;i<argc;i++){ std::string a=argv[i];
    if(a=="--cnf")path=argv[++i]; else if(a=="--witness")witness=argv[++i]; else if(a=="--sha256")sha=argv[++i];
    else if(a=="--seed")seed=std::stoi(argv[++i]); else if(a=="--derived-budget")db=std::stol(argv[++i]);
    else if(a=="--fallback")engine=argv[++i]; else if(a=="--kissat")kissat=argv[++i];
    else if(a=="--kissat-tmp")tmp=argv[++i]; else if(a=="--kissat-log")kissat_log=argv[++i];
    else if(a=="--lemmas")lemmas_on=(std::string(argv[++i])=="on");
    else if(a=="--lemma-out")lemma_out=argv[++i]; else if(a=="--accepted-lemmas")accepted_path=argv[++i];
    else if(a=="--max-lemmas")max_lemmas=std::stoi(argv[++i]); else if(a=="--max-lemma-length")max_len=std::stoi(argv[++i]);
    else if(a=="--harvest-budget")harvest_budget=std::stol(argv[++i]); else if(a=="--flow-budget")flow_budget=std::stol(argv[++i]);
    else if(a=="--generate-only")generate_only=true; else if(a=="--fallback-timeout")timeout_s=std::stod(argv[++i]);
    else throw std::runtime_error("unknown argument: "+a); }
  if(tmp.empty()) tmp="/tmp/lkk4b_"+std::to_string(getpid())+".cnf";
  if(kissat_log.empty()) kissat_log="/tmp/lkk4b_"+std::to_string(getpid())+".kissat.log";

  auto total=Clock::now(),st=Clock::now(); CNF cnf=parse_dimacs(path); double parse=ms(st);
  st=Clock::now(); Gate gate=fast_gate(cnf); Gate benefit;
  double recovery_ms=0,registry_ms=0,flow_ms=0,witness_ms=0,lemma_ms=0;
  Recovery rec; Registry reg; FlowResult flow; Harvest hv; LemmaSet ls;
  std::string structural="UNKNOWN",final="UNKNOWN",abort_reason=""; bool fallback_used=true,witness_written=false;
  if(gate.decision=="RUN_LKK"){ benefit=benefit_gate(cnf,gate);
    if(benefit.decision=="RUN_LKK"){ rec=recover(cnf,db); recovery_ms=rec.elapsed; abort_reason=rec.status=="ABORT"?rec.reason:"";
      reg=registry(rec); registry_ms=reg.elapsed;
      if(reg.status=="COMPLETE"){ flow=flow_test(reg); flow_ms=flow.elapsed; structural=flow.result;
        if(flow.result=="UNSAT"){ st=Clock::now(); if(!witness.empty()) write_witness(witness,sha,cnf,rec,reg,flow); witness_ms=ms(st);
          witness_written=!witness.empty(); final="UNSATISFIABLE"; fallback_used=false; } } } }

  std::vector<Clause> injected;
  if(lemmas_on&&fallback_used){ auto lst=Clock::now(); hv=harvest(rec,harvest_budget);
    ls=generate_lemmas(cnf,rec,hv,max_lemmas,max_len,flow_budget); lemma_ms=ms(lst);
    if(!lemma_out.empty()) write_lemmas(lemma_out,sha,cnf,ls,hv,rec);
    if(!accepted_path.empty()){ auto accepted=read_accepted(accepted_path);
      for(auto &l:ls.lemmas) if(accepted.count(l.clause)) injected.push_back(l.clause); } }

  Fallback fb; int code=0;
  if(fallback_used&&!generate_only){
    if(engine=="cadical") fb=run_cadical(cnf,injected,seed);
    else if(engine=="kissat") fb=run_kissat(cnf,injected,kissat,tmp,kissat_log,seed,timeout_s);
    else if(engine=="none"){ fb.status="UNKNOWN"; }
    else throw std::runtime_error("unknown fallback engine: "+engine);
    final=fb.status; code=fb.status=="SATISFIABLE"?10:fb.status=="UNSATISFIABLE"?20:0;
  } else if(!fallback_used) code=20;

  long injected_literals=0; for(auto &c:injected) injected_literals+=c.size();
  struct rusage u{}; getrusage(RUSAGE_SELF,&u);
  std::cout<<"{\"implementation\":\"native_phase4b\",\"instance\":"<<q(path)<<",\"fallback_engine\":"<<q(engine)
    <<",\"lemmas_enabled\":"<<(lemmas_on?"true":"false")<<",\"generate_only\":"<<(generate_only?"true":"false")
    <<",\"parse_ms\":"<<parse<<",\"gate_ms\":"<<gate.elapsed<<",\"gate_decision\":"<<q(gate.decision)
    <<",\"gate_reason\":"<<q(gate.reason)<<",\"benefit_gate_ms\":"<<benefit.elapsed
    <<",\"benefit_gate_decision\":"<<q(benefit.decision.empty()?"NOT_RUN":benefit.decision)
    <<",\"recovery_ms\":"<<recovery_ms<<",\"recovery_status\":"<<q(rec.status)<<",\"recovery_abort_reason\":"<<q(abort_reason)
    <<",\"registry_ms\":"<<registry_ms<<",\"registry_status\":"<<q(reg.status)<<",\"registry_reason\":"<<q(reg.reason)
    <<",\"family_checks\":"<<reg.checks<<",\"flow_ms\":"<<flow_ms<<",\"witness_ms\":"<<witness_ms
    <<",\"lemma_generation_ms\":"<<lemma_ms<<",\"lemma_candidates\":"<<ls.lemmas.size()
    <<",\"lemmas_injected\":"<<injected.size()<<",\"lemma_literals_injected\":"<<injected_literals
    <<",\"harvest_boxes\":"<<hv.boxes.size()<<",\"harvest_resources\":"<<hv.resources.size()
    <<",\"sub_model_boxes\":"<<ls.sub_boxes<<",\"sub_model_resources\":"<<ls.sub_resources
    <<",\"structural_unsat_detected\":"<<(ls.structural_unsat?"true":"false")
    <<",\"structural_unsat_reason\":"<<q(ls.structural_unsat_reason)<<",\"lemma_note\":"<<q(ls.note)
    <<",\"fallback_ms\":"<<fb.total_ms<<",\"fallback_serialize_ms\":"<<fb.serialize_ms
    <<",\"fallback_solver_self_ms\":"<<fb.solver_self_ms<<",\"fallback_overhead_ms\":"<<fb.overhead_ms
    <<",\"fallback_conflicts\":"<<fb.conflicts<<",\"fallback_decisions\":"<<fb.decisions
    <<",\"fallback_propagations\":"<<fb.propagations<<",\"fallback_restarts\":"<<fb.restarts
    <<",\"fallback_learned\":"<<fb.learned<<",\"fallback_irredundant\":"<<fb.irredundant
    <<",\"fallback_redundant\":"<<fb.redundant<<",\"fallback_ticks\":"<<fb.ticks
    <<",\"total_end_to_end_ms\":"<<ms(total)<<",\"peak_rss_kb\":"<<u.ru_maxrss
    <<",\"boxes_found\":"<<reg.boxes.size()<<",\"capacity_groups\":"<<reg.resources.size()
    <<",\"flow_nodes\":"<<flow.nodes<<",\"flow_edges\":"<<flow.edges
    <<",\"structural_result\":"<<q(structural)<<",\"final_result\":"<<q(final)
    <<",\"fallback_used\":"<<(fallback_used?"true":"false")<<",\"witness_written\":"<<(witness_written?"true":"false")<<"}\n";
  if(engine=="kissat"&&fallback_used&&!generate_only) std::remove(tmp.c_str());
  return code;
}
int main(int argc,char**argv){try{for(int i=1;i+1<argc;i++)if(std::string(argv[i])=="--flow-batch")return flow_batch(argv[i+1]);return solve_main(argc,argv);}catch(std::exception&e){std::cerr<<e.what()<<'\n';return 2;}}
