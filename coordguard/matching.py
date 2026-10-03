"""Deterministic exact numeric correspondence and constructive witnesses.

No xarray, nearest-neighbor heuristic, relabeling or payload operations.
"""
from bisect import bisect_left, bisect_right
from collections import deque
import hashlib
import heapq
from .model import Axis, Label, ValidationError, representable_binary64
from .budget import Budget, ResourceLimit


def _eligible(axis, budget, side):
    if type(axis) is not Axis:
        raise ValidationError('Correspondence requires typed Axis inputs')
    budget.inspect_axis(axis, (side, axis.name))
    if not axis.indexed or axis.index_kind != 'ordinary1d':
        return 'index_profile'
    seen=set()
    for label in axis.labels:
        budget.tick()
        if not representable_binary64(label): return 'nonfinite_or_nonbinary64_numeric'
        key=label.number()
        if key in seen:return 'duplicate_labels'
        seen.add(key)
    return None


def _graph(left, right, tolerance, budget):
    """Exact rational windows cannot lose boundary edges to floating rounding."""
    ordered=sorted((label.number(),v) for v,label in enumerate(right.labels))
    budget.check()
    values=[x for x,v in ordered]
    adjacency=[]; digest=hashlib.sha256(); count=0
    for u,label in enumerate(left.labels):
        budget.tick(); x=label.number()
        lo=bisect_left(values,x-tolerance); hi=bisect_right(values,x+tolerance)
        neighbors=[]
        for position in range(lo,hi):
            budget.add_edge()
            value,v=ordered[position]
            if abs(x-value) <= tolerance:
                neighbors.append(v)
        neighbors.sort(); budget.check()
        for v in neighbors:digest.update(f'{u}:{v};'.encode('ascii'))
        count+=len(neighbors);adjacency.append(neighbors)
    return adjacency,count,digest.hexdigest()


def _maximum_matching(adjacency, nright, budget):
    """Iterative shortest augmenting paths, deterministic u/v traversal.

Worst-case O(VE); max_steps/deadline explicitly bound adversarial graphs.
    """
    left=[None]*len(adjacency); right=[None]*nright
    for root in range(len(adjacency)):
        budget.tick()
        queue=deque([root]);seen={root}; predecessor={}
        endpoint=None
        while queue and endpoint is None:
            u=queue.popleft()
            for v in adjacency[u]:
                budget.tick()
                if v in predecessor:continue
                predecessor[v]=u
                if right[v] is None:
                    endpoint=v;break
                partner=right[v]
                if partner not in seen:seen.add(partner);queue.append(partner)
        if endpoint is not None:
            v=endpoint
            while v is not None:
                budget.tick();u=predecessor[v];old=left[u]
                left[u]=v;right[v]=u;v=old
    return left,right


def _hall(adjacency, matching, inverse, budget):
    unmatched=[u for u,v in enumerate(matching) if v is None]
    reached=set(unmatched);neighbors=set();queue=deque(unmatched)
    while queue:
        u=queue.popleft()
        for v in adjacency[u]:
            budget.tick();neighbors.add(v)
            partner=inverse[v]
            if partner is not None and partner not in reached:
                reached.add(partner);queue.append(partner)
    return {'kind':'hall','matching':matching,'left_set':sorted(reached),'neighbors':sorted(neighbors),
            'unmatched_left':unmatched,'unmatched_right':[v for v,u in enumerate(inverse) if u is None]}


def _uniqueness(adjacency, matching, inverse, budget):
    n=len(matching);arcs=[];indegree=[0]*n
    for u,neighbors in enumerate(adjacency):
        out=[]
        for v in neighbors:
            budget.tick()
            if v != matching[u]:
                w=inverse[v];out.append(w);indegree[w]+=1
        out.sort();arcs.append(out)
    ready=[u for u in range(n) if indegree[u]==0];heapq.heapify(ready);order=[]
    while ready:
        u=heapq.heappop(ready);order.append(u)
        for w in arcs[u]:
            budget.tick();indegree[w]-=1
            if indegree[w]==0:heapq.heappush(ready,w)
    if len(order)==n:
        return 'unique_within_tolerance',{'kind':'unique','matching':matching,'topological_order':order}
    # Iterative DFS finds one directed cycle among the remaining vertices.
    color=[0]*n;parent=[None]*n
    for root in range(n):
        if color[root] or indegree[root]==0:continue
        color[root]=1;stack=[(root,0)]
        while stack:
            u,index=stack[-1];budget.tick()
            if index==len(arcs[u]):color[u]=2;stack.pop();continue
            w=arcs[u][index];stack[-1]=(u,index+1)
            if not color[w]:
                color[w]=1;parent[w]=u;stack.append((w,0))
            elif color[w]==1:
                cycle=[u]
                while cycle[-1]!=w:
                    budget.tick();cycle.append(parent[cycle[-1]])
                cycle.reverse();alternative=matching.copy()
                for i,node in enumerate(cycle):alternative[node]=matching[cycle[(i+1)%len(cycle)]]
                return 'ambiguous',{'kind':'ambiguous','matching':matching,'alternative':alternative}
    raise AssertionError('A finite non-DAG must contain a directed cycle')


def correspond(left_axis, right_axis, atol, *, limits=None, budget=None):
    """Prove a discrete numeric relation only; audit gates scientific metadata.

A failed admission or cap returns non-certifying status with certificate:null.
    """
    if type(atol) is not Label or not representable_binary64(atol) or atol.number()<0:
        raise ValidationError('atol must be a finite nonnegative binary64-representable typed number')
    if budget is not None and type(budget) is not Budget:raise ValidationError('Expected Budget')
    if budget is not None and limits is not None:raise ValidationError('Pass budget or limits, not both')
    try:
        budget=Budget(limits) if budget is None else budget
        for side,axis in (('left',left_axis),('right',right_axis)):
            reason=_eligible(axis,budget,side)
            if reason:return {'schema_version':1,'status':'unsupported','reason':reason,'certificate':None}
        n=left_axis.size;m=right_axis.size
        base={'schema_version':1,'profile':'numeric-absolute-v1','left_digest':left_axis.digest(),
              'right_digest':right_axis.digest(),'atol':atol.to_dict(),'n_left':n,'n_right':m,
              'edge_count':None,'edge_digest':None}
        budget.check()
        if n!=m:
            return dict(base,status='impossible',witness={'kind':'cardinality','left_size':n,'right_size':m})
        adjacency,count,digest=_graph(left_axis,right_axis,atol.number(),budget)
        base.update(edge_count=count,edge_digest=digest)
        matching,inverse=_maximum_matching(adjacency,m,budget)
        if any(v is None for v in matching):
            return dict(base,status='impossible',witness=_hall(adjacency,matching,inverse,budget))
        status,witness=_uniqueness(adjacency,matching,inverse,budget)
        budget.check()
        return dict(base,status=status,witness=witness)
    except ResourceLimit as exc:
        return {'schema_version':1,'status':'resource_limited','reason':exc.code,'certificate':None}
