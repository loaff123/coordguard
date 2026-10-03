"""Independently authored seeded stress oracles; no production graph helpers."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import itertools,random,struct,math,copy
from fractions import Fraction
from coordguard import *
from coordguard.matching import correspond
from coordguard.verify import verify_correspondence
rng=random.Random(402731)
values=[0.0,-0.0,5e-324,-5e-324,1e-323,-1e-323,1.0,math.nextafter(1.,2.),math.nextafter(1.,0.),-1.,1.7976931348623157e308,-1.7976931348623157e308,2**53,float(2**-1022)]
for _ in range(250):
 v=struct.unpack('>d',rng.getrandbits(64).to_bytes(8,'big'))[0]
 if math.isfinite(v):values.append(v)
values=list(dict.fromkeys(values))
checked=0
for _ in range(700):
 n=rng.randrange(7); a=rng.sample(values,n);b=rng.sample(values,n)
 if rng.random()<.4:b=a.copy();rng.shuffle(b)
 tol=abs(rng.choice(values));aax=Axis.from_values('x',a);bax=Axis.from_values('x',b)
 af=list(map(Fraction,a));bf=list(map(Fraction,b));tf=Fraction(tol)
 edges={(i,j) for i in range(n) for j in range(n) if abs(af[i]-bf[j])<=tf}
 count=0
 for perm in itertools.permutations(range(n)):
  if all((i,j) in edges for i,j in enumerate(perm)):count+=1
 result=correspond(aax,bax,Label.from_value(tol))
 expect='impossible' if count==0 else 'unique_within_tolerance' if count==1 else 'ambiguous'
 assert result['status']==expect,(a,b,tol,result,count)
 assert result['edge_count']==len(edges)
 check=verify_correspondence(aax,bax,result)
 assert check['valid'],(result,check)
 checked+=1
print('random bit-exact permutation + solver→verifier cases:',checked)
# Shape/type mutation fuzz for verifier.
a=Axis.from_values('x',[0,1]);p=correspond(a,a,Label.from_value(0));paths=[]
def walk(x,path=()):
 if isinstance(x,dict):
  for k,v in x.items():paths.append(path+(k,));walk(v,path+(k,))
 elif isinstance(x,list):
  for k,v in enumerate(x):paths.append(path+(k,));walk(v,path+(k,))
walk(p)
values=[None,True,False,0,-1,4,0.5,'',[],{},[1],{'x':1},'\ud800']
mutations=0
for path,v in itertools.product(paths,values):
 q=copy.deepcopy(p);dst=q
 for k in path[:-1]:dst=dst[k]
 dst[path[-1]]=v
 try:r=verify_correspondence(a,a,q)
 except Exception as e:raise AssertionError((path,v,e))
 mutations+=1
print('verifier shape/type mutations without unhandled exceptions:',mutations)
# Heterogeneous tiny dimensional cell projections, independent of planner indexers.
from math import prod
options=[[],[0],[1],[0,1],[1,0]]
for _ in range(1200):
 ld=rng.sample(['x','y','z'],rng.randrange(4));rd=rng.sample(['x','y','z'],rng.randrange(4))
 lm={d:rng.choice(options) for d in ld};rm={d:rng.choice(options) for d in rd}
 join=rng.choice(['inner','outer','left','right','exact'])
 la=ArraySnapshot('v',tuple(Axis.from_values(d,lm[d]) for d in ld));ra=ArraySnapshot('v',tuple(Axis.from_values(d,rm[d]) for d in rd))
 report=audit(la,ra,join=join)
 if join=='exact' and any(lm[d]!=rm[d] for d in ld if d in rm):
  assert report['forecast_status']=='operation_error';continue
 dims=ld+[d for d in rd if d not in lm];out=[]
 for d in dims:
  if d not in lm:v=rm[d]
  elif d not in rm:v=lm[d]
  elif lm[d]==rm[d] or join in ('left','exact'):v=lm[d]
  elif join=='right':v=rm[d]
  elif join=='inner':v=[v for v in lm[d] if v in rm[d]]
  else:v=sorted(set(lm[d]+rm[d]))
  out.append(v)
 cells=[dict(zip(dims,c)) for c in itertools.product(*out)]
 assert report['output']['total_positions']==len(cells)
 for side,source in [('left',lm),('right',rm)]:
  kept=[c for c in cells if all(c[d] in v for d,v in source.items())]
  projections=set(tuple(c[d] for d in source) for c in kept)
  impact=report['impact'][side]
  assert impact['retained_source_positions']==len(projections),(lm,rm,join,side,impact,projections)
  assert impact['fill_output_positions']==len(cells)-len(kept)
print('heterogeneous random Cartesian cases:',1200)
