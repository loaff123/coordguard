#!/usr/bin/env python3
"""Fixed small reproducible subprocess benchmarks (Linux peak RSS in KiB)."""
import argparse
import json
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from coordguard import Axis, Label, Limits, correspond

CASES=('near','dense','edge_cap')


def run_case(name):
    n={'near':2000,'dense':400,'edge_cap':1000}[name]
    left=Axis.from_values('x',[float(i) for i in range(n)])
    right=Axis.from_values('x',[float(i)+1e-8 for i in range(n)])
    tolerance=Label.from_value(1e-6 if name=='near' else 2000.)
    limits=Limits(max_edges=10000) if name=='edge_cap' else Limits()
    started=time.perf_counter();result=correspond(left,right,tolerance,limits=limits);elapsed=time.perf_counter()-started
    return {'case':name,'labels_per_operand':n,'status':result['status'],'edge_count':result.get('edge_count'),
            'limit_reason':result.get('reason'),'seconds':elapsed,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'python':platform.python_version(),'system':platform.system(),'machine':platform.machine()}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--case',choices=CASES);args=parser.parse_args()
    if args.case:print(json.dumps(run_case(args.case),sort_keys=True))
    else:
        for name in CASES:
            subprocess.run([sys.executable,__file__,'--case',name],check=True)

if __name__=='__main__':main()
