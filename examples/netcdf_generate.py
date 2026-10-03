"""Generate tiny public NetCDF4 examples locally; no downloads or private inputs."""
import argparse
from pathlib import Path
import numpy as np
import xarray as xr
from coordguard import Contract,Label
from coordguard.model import canonical_json

def generate(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    fixtures=[('near-left','x',[0.,1.,2.]),('near-right','x',[0.,1.000000001,2.]),
              ('broadcast-right','longitude',[0.,1.,2.]),('ambiguous-left','x',[0.,.1]),
              ('ambiguous-right','x',[.04,.06])]
    for filename,dim,values in fixtures:
        path=destination/(filename+'.nc')
        if path.exists():raise FileExistsError('Refusing to overwrite '+str(path))
        ds=xr.Dataset({'temperature':(dim,np.arange(len(values),dtype=np.float64),{'units':'K'})},
                      coords={dim:(dim,np.array(values),{'units':'m'})})
        ds.to_netcdf(path,engine='h5netcdf',mode='w')
    for name,contract in [('near-contract',Contract(tolerances=(('x',Label.from_value(1e-8)),))),
                          ('ambiguous-contract',Contract(tolerances=(('x',Label.from_value(.1)),)))]:
        with (destination/(name+'.json')).open('x') as f:f.write(canonical_json(contract.to_dict())+'\n')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('destination');args=parser.parse_args()
    generate(args.destination)
