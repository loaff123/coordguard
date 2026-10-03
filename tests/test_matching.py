import itertools
import math
import unittest
from fractions import Fraction
from coordguard import Axis, Label, Limits
from coordguard.matching import correspond
from coordguard.verify import verify_correspondence


def oracle(left, right, tolerance):
    a = [Fraction(x) for x in left]; b = [Fraction(x) for x in right]
    t = Fraction(tolerance)
    valid = []
    if len(a) != len(b): return valid
    for permutation in itertools.permutations(range(len(b))):
        if all(abs(a[u]-b[v]) <= t for u,v in enumerate(permutation)):
            valid.append(permutation)
    return valid


def status(valid):
    return 'impossible' if not valid else 'unique_within_tolerance' if len(valid)==1 else 'ambiguous'


class MatchingTests(unittest.TestCase):
    def solve(self,a,b,t,limits=None):
        return correspond(Axis.from_values('x',a),Axis.from_values('x',b),Label.from_value(t),limits=limits)

    def test_examples(self):
        r=self.solve([0.,1.,2.],[0.,1.000000001,2.],1e-8)
        self.assertEqual(r['status'],'unique_within_tolerance')
        self.assertEqual(r['witness']['matching'],[0,1,2])
        r=self.solve([0.,.1],[.04,.06],.1)
        self.assertEqual(r['status'],'ambiguous')
        self.assertNotEqual(r['witness']['matching'],r['witness']['alternative'])
        r=self.solve([0.,1.,2.],[0.,.1,.2],.3)
        self.assertEqual(r['status'],'impossible')
        self.assertLess(len(r['witness']['neighbors']),len(r['witness']['left_set']))
        self.assertEqual(self.solve([0.],[0.,1.],1.)['witness']['kind'],'cardinality')

    def test_exhaustive_permutation_oracle(self):
        count=0
        for n in range(6):
            sets=list(itertools.combinations(range(-2,3),n))
            for a,b,t,reverse in itertools.product(sets,sets,(0.,.5,1.,2.),(False,True)):
                b=b[::-1] if reverse else b
                r=self.solve(a,b,t); candidates=oracle(a,b,t)
                self.assertEqual(r['status'],status(candidates),(a,b,t,r))
                verified=verify_correspondence(Axis.from_values('x',a),Axis.from_values('x',b),r)
                self.assertTrue(verified['valid'],(a,b,t,r,verified))
                if candidates:
                    self.assertIn(tuple(r['witness']['matching']),candidates)
                    if len(candidates)>1:self.assertIn(tuple(r['witness']['alternative']),candidates)
                count+=1
        self.assertEqual(count,2016)

    def test_n_through_seven_and_extreme_values(self):
        for n in (6,7):
            for t in (0.,1.,10.):
                a=list(range(n)); b=a[::-1]
                self.assertEqual(self.solve(a,b,t)['status'],status(oracle(a,b,t)))
        cases=[
            ([0.,5e-324],[5e-324,1e-323],5e-324),
            ([-0.,1.],[0.,1.],0.),
            ([-1.7976931348623157e308,1.7976931348623157e308],[0.,1.7976931348623157e308],1.7976931348623157e308),
            ([1.,2.],[math.nextafter(1.,2.),2.],2**-52),
            ([1.,2.],[math.nextafter(1.,2.),2.],math.nextafter(2**-52,0.)),
        ]
        for a,b,t in cases:self.assertEqual(self.solve(a,b,t)['status'],status(oracle(a,b,t)))

    def test_duplicates_nonfinite_unsafe_integer_unsupported(self):
        for a,b in [([0.,-0.],[0.,1.]),([float('nan')],[0.]),([float('inf')],[1.]),([2**53+1],[2**53+1]),(['a'],['a'])]:
            self.assertEqual(self.solve(a,b,1.)['status'],'unsupported')

    def test_resource_limits_and_determinism(self):
        self.assertEqual(self.solve(list(range(4)),list(range(4)),5.,Limits(max_edges=3))['status'],'resource_limited')
        self.assertEqual(self.solve([0.],[0.],0.,Limits(max_labels=1))['status'],'resource_limited')
        self.assertEqual(self.solve([0.],[0.],0.,Limits(deadline_seconds=0))['status'],'resource_limited')
        self.assertEqual(self.solve([0.],[0.],0.,Limits(max_steps=0))['status'],'resource_limited')
        a=[3.,1.,2.];b=[1.,2.,3.]
        self.assertEqual(self.solve(a,b,10.),self.solve(a,b,10.))

if __name__=='__main__':unittest.main()
