import itertools
import unittest
from coordguard import Axis, ArraySnapshot, Contract, Label, Metadata, Limits
from coordguard.planner import audit


def snap(*axes):return ArraySnapshot('v',tuple(axes))
def axis(name,values,**kw):return Axis.from_values(name,values,**kw)


class PlannerTests(unittest.TestCase):
    def test_near_equal_all_joins(self):
        a=snap(axis('x',[0.,1.,2.]));b=snap(axis('x',[0.,1.000000001,2.]))
        for join,size,fill,drop in [('inner',2,0,1),('outer',4,1,0),('left',3,0,0),('right',3,1,1)]:
            r=audit(a,b,join=join)
            self.assertEqual(r['forecast_status'],'complete')
            self.assertEqual(r['output']['total_positions'],size)
            self.assertEqual(r['impact']['left']['fill_output_positions'],fill)
            self.assertEqual(r['impact']['left']['excluded_source_positions'],drop)
            self.assertEqual(r['contract_status'],'fail')
        self.assertEqual(audit(a,b,join='exact')['forecast_status'],'operation_error')
        r=audit(a,b,contract=Contract(tolerances=(('x',Label.from_value(1e-8)),)))
        self.assertEqual(r['axes'][0]['correspondence_status'],'unique_within_tolerance')
        self.assertEqual(r['contract_status'],'fail') # Correspondence does not authorize drops.

    def test_broadcast_and_zero_scalar(self):
        r=audit(snap(axis('x',[0,1,2])),snap(axis('longitude',[0,1,2])))
        self.assertEqual(r['output']['dimensions'],['x','longitude'])
        self.assertEqual(r['output']['total_positions'],9)
        self.assertEqual(r['impact']['left']['broadcast_dimensions'],['longitude'])
        self.assertEqual(r['impact']['left']['retained_source_positions'],3)
        self.assertEqual(audit(snap(),snap())['output']['total_positions'],1)
        r=audit(snap(axis('x',[0,1])),snap(axis('y',[])))
        self.assertEqual(r['output']['total_positions'],0)
        self.assertEqual(r['impact']['left']['retained_source_positions'],0)

    def test_unindexed_symbolic_huge_and_conflict(self):
        r=audit(snap(Axis('x',2**40)),snap(Axis('x',2**40)))
        self.assertEqual(r['output']['total_positions'],2**40)
        self.assertEqual(r['axes'][0]['left_indexer'],{'kind':'identity','size':2**40})
        self.assertEqual(r['contract_status'],'pass')
        self.assertEqual(audit(snap(Axis('x',2)),snap(Axis('x',3)))['forecast_status'],'operation_error')
        r=audit(snap(Axis('x',2)),snap(axis('x',[2,1])))
        self.assertEqual(r['axes'][0]['mode'],'positional_with_index')
        self.assertEqual(r['output']['sizes'],[2])

    def test_duplicates_reordering_and_defaults(self):
        a=snap(axis('x',[1,1,2]));b=snap(axis('x',[1,1,2]))
        self.assertEqual(audit(a,b)['forecast_status'],'complete')
        self.assertEqual(audit(a,b)['contract_status'],'fail')
        self.assertEqual(audit(a,b,contract=Contract(allow_duplicates=True))['contract_status'],'pass')
        self.assertEqual(audit(a,snap(axis('x',[1,2,3])),join='right')['forecast_status'],'unsupported')
        self.assertEqual(audit(a,snap(axis('x',[1,2,3])),join='left')['forecast_status'],'unsupported')
        r=audit(snap(axis('x',[2,1])),snap(axis('x',[1,2])))
        self.assertEqual(r['axes'][0]['right_indexer'],[1,0])
        self.assertTrue(r['axes'][0]['right_reordered'])

    def test_semantic_unknowns_do_not_pass(self):
        for bad in [axis('x',[float('nan')]),axis('x',[1],metadata=Metadata(calendar='360_day')),
                    axis('x',[1],metadata=Metadata(units='days since 2000-01-01')),
                    Axis('x',1,(Label.from_value(1),),index_kind='curvilinear')]:
            r=audit(snap(bad),snap(axis('x',[1])))
            self.assertEqual(r['forecast_status'],'unsupported')
            self.assertEqual(r['contract_status'],'unverified')
            self.assertIsNone(r['output'])
        a=snap(axis('x',[1.],metadata=Metadata(units='m')))
        b=snap(axis('x',[1.],metadata=Metadata(units='degrees')))
        r=audit(a,b,contract=Contract(tolerances=(('x',Label.from_value(1.)),),same_axis_declarations=('x',)))
        self.assertEqual(r['forecast_status'],'complete')
        self.assertEqual(r['contract_status'],'unverified')
        self.assertEqual(r['axes'][0]['correspondence_status'],'unsupported')
        a=snap(axis('x',[1.],metadata=Metadata(role='spatial')))
        self.assertEqual(audit(a,a,contract=Contract(tolerances=(('x',Label.from_value(1.)),)))['contract_status'],'unverified')
        self.assertEqual(audit(a,a,contract=Contract(tolerances=(('x',Label.from_value(1.)),),same_axis_declarations=('x',)))['contract_status'],'pass')

    def test_unsafe_mixed_integer_and_manifest_blocker(self):
        r=audit(snap(axis('x',[2**53+1])),snap(axis('x',[float(2**53)])))
        self.assertEqual(r['forecast_status'],'unsupported')
        a=ArraySnapshot('v',(axis('x',[1]),),semantic_blockers=('auxiliary_coordinates_not_modeled',))
        self.assertEqual(audit(a,a)['forecast_status'],'unsupported')
        r=audit(snap(axis('x',[1])),snap(axis('x',[1])),limits=Limits(max_labels=1))
        self.assertEqual((r['forecast_status'],r['contract_status']),('resource_limited','unverified'))
        self.assertIsNone(r['output'])

    def test_string_joins_preserve_exact_unicode_and_types(self):
        a=snap(axis('x',['z','a','é']));b=snap(axis('x',['a','é','<script>']))
        expectations={'inner':['a'],'outer':['<script>','a','é','z','é'],'left':['z','a','é'],'right':['a','é','<script>']}
        for join,expected in expectations.items():
            result=audit(a,b,join=join)
            self.assertEqual(result['forecast_status'],'complete')
            self.assertEqual(result['axes'][0]['labels'],[Label.from_value(x).to_dict() for x in expected])
        self.assertEqual(audit(a,b,join='exact')['forecast_status'],'operation_error')

    def test_large_pure_integer_joins_never_cast(self):
        n=2**200
        a=snap(axis('x',[n,n+1]));b=snap(axis('x',[n+1,n+2]))
        result=audit(a,b,join='outer')
        self.assertEqual(result['forecast_status'],'complete')
        self.assertEqual(result['axes'][0]['labels'],[Label.from_value(x).to_dict() for x in (n,n+1,n+2)])
        self.assertEqual(result['axes'][0]['left_indexer'],[0,1,None])
        self.assertEqual(result['axes'][0]['right_indexer'],[None,0,1])

    def test_shared_budget_and_invalid_limits(self):
        from coordguard.budget import Budget
        from coordguard.model import ValidationError
        a=snap(axis('x',[1]))
        r=audit(a,a,budget=Budget(Limits(deadline_seconds=0)))
        self.assertEqual(r['forecast_status'],'resource_limited')
        with self.assertRaises(ValidationError):audit(a,a,limits='bad')
        with self.assertRaises(ValidationError):audit(a,a,budget='bad')
        with self.assertRaises(ValidationError):audit(a,a,limits=Limits(),budget=Budget())

    def test_tiny_independent_cartesian_oracle(self):
        # Enumerate real tuples. No production key/indexer/planner helper is used.
        count=0
        options=[[],[0],[1],[0,1],[1,0]]
        shapes=[(('x',),('x',)),(('x',),('y',)),(('x','y'),('y','z'))]
        for ldims,rdims in shapes:
            for lval,rval,join in itertools.product(options,options,('inner','outer','left','right','exact')):
                lm={d:list(lval) for d in ldims};rm={d:list(rval) for d in rdims}
                left=snap(*(axis(d,lm[d]) for d in ldims));right=snap(*(axis(d,rm[d]) for d in rdims))
                common=[d for d in ldims if d in rdims]
                should_error=join=='exact' and any(lm[d]!=rm[d] for d in common)
                r=audit(left,right,join=join)
                if should_error:
                    self.assertEqual(r['forecast_status'],'operation_error');count+=1;continue
                dims=list(ldims)+[d for d in rdims if d not in ldims]
                choices=[]
                for d in dims:
                    if d not in lm:c=rm[d]
                    elif d not in rm:c=lm[d]
                    elif join in ('left','exact'):c=lm[d]
                    elif join=='right':c=rm[d]
                    elif join=='inner':c=[x for x in lm[d] if x in rm[d]]
                    else:c=list(dict.fromkeys(lm[d]+rm[d]))
                    choices.append(c)
                cells=[dict(zip(dims,t)) for t in itertools.product(*choices)]
                self.assertEqual(r['output']['total_positions'],len(cells))
                for side,own in [('left',lm),('right',rm)]:
                    contributing=[c for c in cells if all(c[d] in vals for d,vals in own.items())]
                    source=set(tuple(c[d] for d in own) for c in contributing)
                    impact=r['impact'][side]
                    self.assertEqual(impact['retained_source_positions'],len(source),(lm,rm,join,side))
                    self.assertEqual(impact['fill_output_positions'],len(cells)-len(contributing))
                count+=1
        self.assertEqual(count,375)

if __name__=='__main__':unittest.main()
