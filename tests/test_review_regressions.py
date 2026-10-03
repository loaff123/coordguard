"""Review findings: each regression observed failing before repair."""
import unittest
from coordguard import ArraySnapshot, Axis, Contract, Label, Metadata, audit, ValidationError


class ReviewRegressionTests(unittest.TestCase):
    def test_explicit_incompatible_roles_cannot_pass(self):
        left=ArraySnapshot('v',(Axis.from_values('x',[1.],metadata=Metadata(role='latitude',units='degree',location='center')),))
        right=ArraySnapshot('v',(Axis.from_values('x',[1.],metadata=Metadata(role='longitude',units='degree',location='center')),))
        contract=Contract(tolerances=(('x',Label.from_value(0.)),),same_axis_declarations=('x',))
        result=audit(left,right,contract=contract)
        self.assertEqual(result['contract_status'],'unverified')
        self.assertEqual(result['axes'][0]['correspondence_status'],'unsupported')
        self.assertIn({'code':'role_mismatch','dimension':'x'},result['semantic_blockers'])

    def test_explicit_float_kind_must_not_silently_lose_integer_bits(self):
        for value,kind in ((2**53+1,'float64'),(2**24+1,'float32')):
            with self.assertRaises(ValidationError):Label.from_value(value,kind)
        self.assertEqual(Label.from_value(2**100,'float64').number(),2**100)

    def test_escaped_metadata_is_charged_before_digest(self):
        from coordguard import Limits
        value='\x00'*4096
        metadata=Metadata(units=value,role=value,location=value,crs=value)
        source=ArraySnapshot('v',tuple(Axis('x'+str(i),1,metadata=metadata) for i in range(32)))
        report=audit(source,source,limits=Limits(max_working_bytes=5_500_000))
        self.assertEqual(report['forecast_status'],'resource_limited')
        self.assertEqual(report['contract_status'],'unverified')

    def test_many_tiny_json_objects_are_charged_before_parse(self):
        import tempfile
        from pathlib import Path
        from coordguard.budget import Budget, Limits, ResourceLimit
        from coordguard.cli import read_json
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'many.json'
            path.write_text('['+','.join('{}' for _ in range(200000))+']')
            with self.assertRaises(ResourceLimit):
                read_json(str(path),Budget(Limits(max_working_bytes=12*1024*1024)))

    def test_invalid_model_cardinalities_reject_before_copies(self):
        import tracemalloc
        malformed_snapshot={str(i):0 for i in range(100000)}
        malformed_contract=Contract().to_dict()
        malformed_contract['tolerances']={str(i):Label.from_value(0.).to_dict() for i in range(100000)}
        malformed_blockers=ArraySnapshot('v',()).to_dict()
        malformed_blockers['semantic_blockers']=['x']*1000000
        for cls,value in [(ArraySnapshot,malformed_snapshot),(Contract,malformed_contract),(ArraySnapshot,malformed_blockers)]:
            tracemalloc.start()
            try:
                with self.assertRaises(ValidationError):cls.from_dict(value)
                _,peak=tracemalloc.get_traced_memory()
            finally:tracemalloc.stop()
            self.assertLess(peak,1024*1024,(cls.__name__,peak))

    def test_axis_shape_is_rejected_before_materializing_labels(self):
        from unittest.mock import patch
        malformed=Axis.from_values('x',[1]).to_dict();malformed['size']=2
        with patch.object(Label,'from_dict',side_effect=AssertionError('labels materialized before shape check')):
            with self.assertRaises(ValidationError):Axis.from_dict(malformed)

    def test_invalid_configuration_errors_are_stable(self):
        from coordguard import Limits, correspond
        with self.assertRaises(ValidationError):Limits(deadline_seconds=10**1000)
        axis=Axis.from_values('x',[1.])
        with self.assertRaises(ValidationError):correspond(axis,axis,Label.from_value(0.),budget={})

    def test_right_join_preserves_selected_representation_when_equal(self):
        for a,b in ((1,1.),(0.,-0.)):
            left=ArraySnapshot('v',(Axis.from_values('x',[a]),))
            right=ArraySnapshot('v',(Axis.from_values('x',[b]),))
            result=audit(left,right,join='right')
            self.assertEqual(result['axes'][0]['labels'],[Label.from_value(b).to_dict()])

    def test_unsafe_mixed_index_is_not_admitted_positionally(self):
        left=ArraySnapshot('v',(Axis.from_values('x',[2**53+1,0.]),))
        right=ArraySnapshot('v',(Axis('x',2),))
        self.assertEqual(audit(left,right)['forecast_status'],'unsupported')
        self.assertEqual(audit(left,ArraySnapshot('v',()))['forecast_status'],'unsupported')

if __name__=='__main__':unittest.main()
