import copy
import math
import unittest
from coordguard.model import Label, Axis, ArraySnapshot, Metadata, Contract, ValidationError, canonical_json
from coordguard.budget import Limits, Budget, ResourceLimit


class ModelTests(unittest.TestCase):
    def test_float_bits_roundtrip(self):
        for x in (0.0, -0.0, 5e-324, 1e308, math.nextafter(1.0, 2.0)):
            a = Label.from_value(x)
            self.assertEqual(a, Label.from_dict(a.to_dict()))
            self.assertEqual(a.number(), x.as_integer_ratio()[0] / x.as_integer_ratio()[1] if abs(x) < 1e300 else x)
        self.assertNotEqual(Label.from_value(0.0).value, Label.from_value(-0.0).value)
        self.assertEqual(Label.from_value(0.0).key(), Label.from_value(-0.0).key())
        self.assertEqual(Label.from_value(0.1, 'float32').value, '3dcccccd')

    def test_ints_remain_exact(self):
        n = 2**100 + 1
        self.assertEqual(Label.from_value(n).number(), n)
        self.assertEqual(Label.from_dict({'kind':'int','value':str(n)}).number(), n)
        with self.assertRaises(ValidationError): Label.from_value(2**256)
        with self.assertRaises(ValidationError): Label.from_value(True)
        with self.assertRaises(ValidationError): Label.from_dict({'kind':'int','value':'01'})

    def test_strict_model_and_digest(self):
        a = ArraySnapshot('v', (Axis('x', 2, (Label.from_value(2), Label.from_value(1))),))
        self.assertEqual(a, ArraySnapshot.from_dict(a.to_dict()))
        self.assertEqual(a.digest(), ArraySnapshot.from_dict(a.to_dict()).digest())
        b = copy.deepcopy(a.to_dict()); b['payload'] = [3,4]
        with self.assertRaises(ValidationError): ArraySnapshot.from_dict(b)
        b = copy.deepcopy(a.to_dict()); b['schema_version'] = True
        with self.assertRaises(ValidationError): ArraySnapshot.from_dict(b)
        with self.assertRaises(ValidationError): Axis('x', 2, (Label.from_value(1),))
        with self.assertRaises(ValidationError): ArraySnapshot('v', (Axis('x',2), Axis('x',2)))
        with self.assertRaises(ValidationError): Axis('x', True)
        with self.assertRaises(ValidationError): Metadata(units=1)
        with self.assertRaises(ValidationError): Label.from_value('x'*4097)
        with self.assertRaises(ValidationError): Axis('x',2,[Label.from_value(1),Label.from_value(2)])
        self.assertEqual(canonical_json({'b':2,'a':1}), '{"a":1,"b":2}')

    def test_nonfinite_preserved(self):
        for x in (float('nan'),float('inf'),float('-inf')):
            label = Label.from_value(x)
            self.assertFalse(label.finite)
            self.assertEqual(label, Label.from_dict(label.to_dict()))
        self.assertFalse(Label('null','null').finite)

    def test_contract_strict(self):
        c = Contract(tolerances=(('x', Label.from_value(1e-8)),))
        self.assertEqual(c, Contract.from_dict(c.to_dict()))
        with self.assertRaises(ValidationError): Contract(allow_drops=1)
        with self.assertRaises(ValidationError): Contract(tolerances=(('x',Label.from_value(-1.0)),))
        with self.assertRaises(ValidationError): Contract(tolerances=(('x',Label.from_value(1.0)),('x',Label.from_value(1.0))))

    def test_budget_caps_incrementally(self):
        budget = Budget(Limits(max_labels=1))
        budget.add_label(Label.from_value(1))
        with self.assertRaises(ResourceLimit) as ctx: budget.add_label(Label.from_value(2))
        self.assertEqual(ctx.exception.code, 'label_limit')
        budget = Budget(Limits(max_edges=1))
        budget.add_edge()
        with self.assertRaises(ResourceLimit): budget.add_edge()
        with self.assertRaises(ResourceLimit): Budget(Limits(max_working_bytes=1))
        with self.assertRaises(ResourceLimit): Budget(Limits(deadline_seconds=0)).check()
        with self.assertRaises(ValidationError): Limits(max_edges=True)
        with self.assertRaises(ValidationError): Limits(deadline_seconds=float('nan'))
        with self.assertRaises(ResourceLimit): Budget(Limits(max_coordinate_bytes=2)).add_label(Label.from_value(1))
        budget = Budget(Limits(max_steps=1)); budget.tick()
        with self.assertRaises(ResourceLimit): budget.tick()

if __name__ == '__main__': unittest.main()
