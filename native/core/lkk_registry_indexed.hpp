#ifndef LKK_REGISTRY_INDEXED_HPP
#define LKK_REGISTRY_INDEXED_HPP
// The Phase 4C exact-r search, in one place.
//
// It differs from lkk_core.hpp's registry() only in how the budget is spent:
// candidates come from an inverted index instead of a cross product, families
// are verified one clause at a time with each decided group remembered, and
// every lookup is charged rather than every candidate. The acceptance test, the
// candidate key and the selection order are untouched, so with an unbounded
// budget the two agree exactly -- phase4c/test_phase4c.py checks that against an
// independent Python implementation, and native/core/test_core_drift.py checks
// that this engine never decides differently from the sealed Phase 4A binary.

#include "lkk_core.hpp"

// The budget is a parameter rather than a constant because Phase 4C changed
// what a check costs: previously unbilled work is now charged, so the value
// calibrated against the old accounting no longer means the same thing. It is
// fitted by phase5/tune.py on a calibration split, never on the benchmark.
// `atleast_boxes` admits one-sided demands as well as exact-r ones. It defaults
// to off so the sealed Phase 4A comparison in native/core/test_core_drift.py
// keeps comparing like with like; Phase 6 turns it on.
static Registry registry_indexed(const Recovery &rec,long budget,bool atleast_boxes=false) {
  auto st=Clock::now(); Registry out;if(rec.status!="COMPLETE"){out.reason="recovery_"+rec.reason;out.elapsed=ms(st);return out;}
  std::vector<const Clause*> pos,neg;for(auto &r:rec.records){bool p=r.clause.size()>=2,n=p;for(int x:r.clause){p&=x>0;n&=x<0;}if(p)pos.push_back(&r.clause);if(n)neg.push_back(&r.clause);}
  std::map<CandidateKey,std::vector<int>> candidates;
  std::set<CandidateKey> rejected;
  // Phase 4C: the accepted search paired every positive with every negative and
  // discarded the pair unless it shared exactly two variables, spending the
  // family-check budget almost entirely on rejections. An inverted index from a
  // variable pair to the negatives containing it yields exactly the partners
  // that can qualify. The acceptance test, candidate key, check accounting and
  // selection order are unchanged, so with an unbounded budget the result is
  // identical; with a finite one it can only reach more candidates.
  std::vector<std::vector<int>> nvars(neg.size());
  std::unordered_map<long long,std::vector<int>> pair_index;
  for(size_t ni=0;ni<neg.size();ni++){
    auto &nv=nvars[ni]; for(int x:*neg[ni]) nv.push_back(std::abs(x));
    std::sort(nv.begin(),nv.end()); nv.erase(std::unique(nv.begin(),nv.end()),nv.end());
    for(size_t i=0;i<nv.size();i++) for(size_t j=i+1;j<nv.size();j++)
      pair_index[(long long)nv[i]*4000000000LL+nv[j]].push_back((int)ni);
  }
  std::vector<int> stamp(neg.size(),-1);
  for(size_t pi=0;pi<pos.size();pi++){
    auto pc=pos[pi];
    std::vector<int> pv; for(int x:*pc) pv.push_back(std::abs(x));
    std::sort(pv.begin(),pv.end()); pv.erase(std::unique(pv.begin(),pv.end()),pv.end());
    for(size_t a=0;a<pv.size();a++) for(size_t b=a+1;b<pv.size();b++){
      auto it=pair_index.find((long long)pv[a]*4000000000LL+pv[b]);
      if(it==pair_index.end()) continue;
      for(int ni:it->second){
        // A negative sharing three or more variables sits in several buckets;
        // examining it once keeps the check count faithful to the scan.
        if(stamp[ni]==(int)pi) continue; stamp[ni]=(int)pi;
        if(++out.checks>budget){out.reason="family_check_budget";out.elapsed=ms(st);return out;}
        auto nc=neg[ni]; const std::vector<int> &nv=nvars[ni];
        std::vector<int> inter,group;
        std::set_intersection(pv.begin(),pv.end(),nv.begin(),nv.end(),std::back_inserter(inter));
        if(inter.size()!=2)continue;
        std::set_union(pv.begin(),pv.end(),nv.begin(),nv.end(),std::back_inserter(group));
        int d=nc->size()-1;
        if(group.size()!=pc->size()+nc->size()-2||d<=0||d>=(int)group.size())continue;
        CandidateKey key{group,d};
        // The same group is reachable from many pairs; deciding it once, and
        // remembering refusals too, stops the budget being spent re-deciding it.
        if(candidates.count(key)||rejected.count(key)) continue;
        // The accepted search charged the whole family before testing any of
        // it, so a candidate rejected on its first clause still cost all 176
        // checks at group size 11. Charging per membership test, in the same
        // order, gives the same verdict for far less budget.
        std::vector<int> ev; bool ok=true,exhausted=false;
        for(int sign_pass=0; sign_pass<2 && ok && !exhausted; sign_pass++){
          std::vector<Clause> req;
          if(sign_pass==0) combinations(group,d+1,-1,req);
          else combinations(group,(int)group.size()-d+1,1,req);
          for(auto &c:req){
            if(++out.checks>budget){exhausted=true;break;}
            auto f=rec.ids.find(c); if(f==rec.ids.end()){ok=false;break;}
            ev.push_back(f->second);
          }
        }
        if(exhausted){out.reason="family_check_budget";out.elapsed=ms(st);return out;}
        if(ok) candidates[key]=ev; else rejected.insert(key);
      }
    }
  }
  std::vector<std::pair<CandidateKey,std::vector<int>>> ordered(candidates.begin(),candidates.end());std::sort(ordered.begin(),ordered.end(),[](auto&a,auto&b){if(a.first.group.size()!=b.first.group.size())return a.first.group.size()>b.first.group.size();return a.first<b.first;});std::set<int> used;
  for(auto &x:ordered){bool overlap=false;for(int v:x.first.group)overlap|=used.count(v);if(overlap)continue;Box b{(int)out.boxes.size(),x.first.value,x.first.group,x.second};out.boxes.push_back(b);used.insert(b.vars.begin(),b.vars.end());}
  // The min cut consumes only the lower half of an exact-r box: it needs "at
  // least r of these hold" and never the matching upper bound, which the flow
  // network has no edge for. Demanding the unused half as evidence is what hid
  // the archetypal family - the standard pigeonhole encoding states only that
  // each pigeon occupies some hole, and states nothing about at most one - so a
  // positive clause on its own is admitted here as an AtLeast_1 demand.
  //
  // Exact-r boxes are already placed, so these only fill variables no stronger
  // box claimed. Record order fixes which of two overlapping positives wins.
  int atleast_added=0;
  if(atleast_boxes){
    for(auto pc:pos){
      std::vector<int> gv;for(int x:*pc)gv.push_back(std::abs(x));
      std::sort(gv.begin(),gv.end());gv.erase(std::unique(gv.begin(),gv.end()),gv.end());
      bool overlap=false;for(int v:gv)overlap|=used.count(v);
      if(overlap)continue;
      auto it=rec.ids.find(*pc);if(it==rec.ids.end())continue;
      out.boxes.push_back(Box{(int)out.boxes.size(),1,gv,{it->second},true});
      used.insert(gv.begin(),gv.end());atleast_added++;
    }
  }
  if(out.boxes.empty()){out.reason="no_sound_exact_r_boxes";out.elapsed=ms(st);return out;}std::unordered_map<int,int> vbox;for(auto &b:out.boxes)for(int v:b.vars)vbox[v]=b.id;
  std::map<CandidateKey,std::vector<int>> rcands;
  for(int sz=2;sz<=4;sz++){int cap=sz-1;std::set<std::vector<int>> edges;for(auto nc:neg){if((int)nc->size()!=sz)continue;std::vector<int> vs;std::set<int> bs;bool ok=true;for(int x:*nc){int v=std::abs(x);if(!vbox.count(v)){ok=false;break;}vs.push_back(v);bs.insert(vbox[v]);}std::sort(vs.begin(),vs.end());if(ok&&(int)bs.size()==sz)edges.insert(vs);}std::set<int> avs;for(auto&e:edges)avs.insert(e.begin(),e.end());
    // Membership is asked millions of times while growing cliques, but the
    // ordered iteration below fixes which candidate is accepted first, so the
    // ordered container has to stay. A hash index alongside it keeps every
    // decision identical while turning each lookup from a tree walk with vector
    // comparisons into a single probe.
    std::unordered_set<std::vector<int>,VHash> edge_lookup(edges.begin(),edges.end());
    // For pair edges, a vertex can only extend a clique if it is adjacent to
    // every member already in it. Sweeping that neighbourhood instead of every
    // active variable reaches the same first accepted candidate, in the same
    // order, without paying a check for those that cannot qualify.
    std::map<int,std::set<int>> nbr;
    if(sz==2) for(auto &e:edges){ nbr[e[0]].insert(e[1]); nbr[e[1]].insert(e[0]); }
    auto intersect=[&](std::set<int> &pool,int v){ auto it=nbr.find(v); std::set<int> keep;
      if(it!=nbr.end()) for(int x:pool) if(it->second.count(x)) keep.insert(x); pool.swap(keep); };
    for(auto edge:edges){
      std::set<int> group(edge.begin(),edge.end());
      // The candidate pool and the occupied-box set are properties of the group,
      // and adding one vertex changes each by one intersection or one insertion.
      // Rebuilding them on every growth step made a k-vertex clique cost O(k^2)
      // sweeps instead of O(k); the sweep contents and their order are
      // unchanged, so the same vertex is accepted first.
      std::set<int> occupied; for(int v:group) occupied.insert(vbox[v]);
      const bool use_pool=(sz==2);
      std::set<int> pool;
      if(use_pool){ bool first=true;
        for(int v:group){ if(first){ auto it=nbr.find(v); pool=(it==nbr.end()?std::set<int>():it->second); first=false; }
          else intersect(pool,v);
          if(pool.empty()) break; } }
      bool changed=true;
      while(changed){ changed=false;
        std::vector<int> sweep(use_pool?pool.begin():avs.begin(),use_pool?pool.end():avs.end());
        // `group` is already a clique, so every subset that omits the candidate
        // is known to be an edge and only the subsets containing it can fail:
        // C(|group|, sz-1) lookups instead of C(|group|+1, sz), computed once
        // per growth step rather than per candidate.
        std::vector<int> members(group.begin(),group.end());
        std::vector<Clause> rests; combinations(members,sz-1,1,rests);
        // Each lookup is charged, not each candidate. Charging one check for a
        // candidate that costs |group| lookups is what let the registry spend
        // four seconds while the budget still looked untouched.
        std::vector<int> probe;
        for(int cand:sweep){if(group.count(cand)||occupied.count(vbox[cand]))continue;
          bool clique=true,exhausted=false;
          for(auto &r:rests){
            if(++out.checks>budget){exhausted=true;break;}
            probe.clear();for(int x:r)probe.push_back(std::abs(x));
            probe.push_back(cand);std::sort(probe.begin(),probe.end());
            if(!edge_lookup.count(probe)){clique=false;break;}}
          if(exhausted){out.reason="family_check_budget";out.elapsed=ms(st);return out;}
          if(clique){ group.insert(cand); occupied.insert(vbox[cand]);
            if(use_pool) intersect(pool,cand);
            changed=true; break; }}}
      if((int)group.size()<=cap)continue;
      std::vector<int> gv(group.begin(),group.end());
      std::vector<Clause> req;combinations(gv,sz,-1,req);
      // These lookups used to be free of charge, which is the same defect as the
      // two above: work the budget cannot see.
      std::vector<int> ev;bool ok=true,exhausted=false;
      for(auto &c:req){ if(++out.checks>budget){exhausted=true;break;}
        auto it=rec.ids.find(c);if(it==rec.ids.end()){ok=false;break;}ev.push_back(it->second);}
      if(exhausted){out.reason="family_check_budget";out.elapsed=ms(st);return out;}
      if(ok)rcands[{gv,cap}]=ev;
  }}
  std::vector<std::pair<CandidateKey,std::vector<int>>> ro(rcands.begin(),rcands.end());std::sort(ro.begin(),ro.end(),[](auto&a,auto&b){if(a.first.group.size()!=b.first.group.size())return a.first.group.size()>b.first.group.size();if(a.first.value!=b.first.value)return a.first.value<b.first.value;return a.first.group<b.first.group;});std::set<int> ru;
  for(auto &x:ro){bool overlap=false;for(int v:x.first.group)overlap|=ru.count(v);if(overlap)continue;Resource r{(int)out.resources.size(),x.first.value,x.first.group,x.second};out.resources.push_back(r);ru.insert(r.vars.begin(),r.vars.end());}
  if(used!=ru){std::ostringstream s;s<<"incomplete_or_ambiguous_resource_mapping:[";bool first=true;for(int v:used)if(!ru.count(v)){if(!first)s<<", ";first=false;s<<v;}s<<"]";out.reason=s.str();out.elapsed=ms(st);return out;}
  out.status="COMPLETE";
  out.reason=atleast_added?"sound_exact_r_atleast_and_capacity_families"
                          :"sound_exact_r_and_capacity_families";
  out.elapsed=ms(st);return out;
}

#endif  // LKK_REGISTRY_INDEXED_HPP
