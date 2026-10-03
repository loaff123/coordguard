"""Measured end-to-end generated NetCDF audit benchmark (not a speed target)."""
import argparse
import json
import platform
import resource
import tempfile
import time
from pathlib import Path
import numpy as np
import xarray as xr
from coordguard import Contract,Label
from coordguard.netcdf import runtime_versions
from coordguard.report import audit_netcdf

def run(count,dense):
    with tempfile.TemporaryDirectory() as root:
        paths=[str(Path(root)/(side+'.nc')) for side in ('left','right')]
        left=np.arange(count,dtype='float64') if not dense else np.linspace(0,1,count)
        right=left+1e-9
        for path,values in zip(paths,(left,right)):
            xr.Dataset({'temperature':('x',np.zeros(count))},coords={'x':values}).to_netcdf(path,engine='h5netcdf')
        start=time.perf_counter()
        report=audit_netcdf(*paths,'temperature','temperature',contract=Contract(tolerances=(('x',Label.from_value(2. if dense else 1e-8)),)))
        elapsed=time.perf_counter()-start
        audit=report['audit']
        return {'mode':'dense_ambiguous' if dense else 'near_equal','labels_per_side':count,
                'wall_seconds':elapsed,'peak_process_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                'forecast_status':audit['forecast_status'],'correspondence_status':audit['axes'][0]['correspondence_status'],
                'resources':audit['resources'],'platform':platform.platform(),'python':platform.python_version(),
                'versions':runtime_versions(),'rss_scope':'whole process including imports and generated fixture creation'}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--count',type=int,default=1000);p.add_argument('--dense',action='store_true');a=p.parse_args()
    print(json.dumps(run(a.count,a.dense),sort_keys=True))
