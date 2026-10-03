import unittest
from coordguard import Label,Axis,ArraySnapshot,Metadata
from coordguard.planner import audit
PROFILE='xarray-netcdf-2025.10.1-v1'
class DatetimeProfileTests(unittest.TestCase):
    def test_precise_resolution_keys_and_time_origins(self):
        a=Label('datetime','s:946684800');b=Label('datetime','ms:946684800000')
        self.assertEqual(a.key(),b.key())
        la=Axis('time',1,(a,),Metadata(units='days since 2000-01-01',calendar='standard',role='time',time_encoding='datetime64[s]'))
        ra=Axis('time',1,(a,),Metadata(units='days since 1999-12-31',calendar='standard',role='time',time_encoding='datetime64[s]'))
        r=audit(ArraySnapshot('v',(la,),PROFILE),ArraySnapshot('v',(ra,),PROFILE),join='exact')
        self.assertEqual(r['contract_status'],'pass')
        self.assertTrue(r['claims']['xarray_runtime_validated'])
    def test_invalid_datetime_and_nat_rejected(self):
        for value in ['2000-01-01','s:NaT','ns:-9223372036854775808','ps:1','s:01']:
            with self.assertRaises(ValueError):Label('datetime',value)
    def test_dtype_roundtrip_optional(self):
        a=Axis('x',1,(Label.from_value(1),),source_dtype='uint64')
        self.assertEqual(Axis.from_dict(a.to_dict()),a)
    def test_datetime_calendar_mismatch_unverified(self):
        a=Axis('time',1,(Label('datetime','s:0'),),Metadata(calendar='standard',time_encoding='datetime64[s]'))
        b=Axis('time',1,(Label('datetime','s:0'),),Metadata(calendar='proleptic_gregorian',time_encoding='datetime64[s]'))
        r=audit(ArraySnapshot('v',(a,),PROFILE),ArraySnapshot('v',(b,),PROFILE))
        self.assertEqual(r['contract_status'],'unverified')
