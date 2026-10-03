"""Canonical reports must survive fresh processes with different hash seeds."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    import h5netcdf
    import xarray as xr
    OPTIONAL = xr.__version__ == '2025.10.1'
except ImportError:
    OPTIONAL = False


@unittest.skipUnless(OPTIONAL, 'requires pinned NetCDF extra')
class ReportDeterminismTests(unittest.TestCase):
    def test_auxiliary_semantic_order_and_replay_cross_hash_seeds(self):
        # Replacing the declared source order with a set intersection must fail.
        names = ['height', 'latitude', 'pressure', 'sigma', 'longitude', 'offset']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / 'left.nc', root / 'right.nc']
            for path, units in zip(paths, ('m', 'km')):
                dataset = xr.Dataset({'v': ('x', [1, 2])}, coords={'x': [0, 1]})
                for name in names:
                    dataset = dataset.assign_coords({name: ('x', [5, 6], {'units': units})})
                dataset.to_netcdf(path, engine='h5netcdf')
                # Preserve a deliberately nonalphabetic declared coordinate order.
                with h5netcdf.File(path, 'a') as source:
                    source.variables['v'].attrs['coordinates'] = ' '.join(names)
            reports = []
            for seed in ('1', '2', '3'):
                env = dict(os.environ, PYTHONHASHSEED=seed)
                result = subprocess.run(
                    [sys.executable, '-m', 'coordguard', 'audit-nc', *map(str, paths),
                     '--left-variable', 'v', '--right-variable', 'v'],
                    env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 3, result.stderr)
                reports.append(result.stdout)
            self.assertEqual(reports[0], reports[1])
            self.assertEqual(reports[0], reports[2])
            report = json.loads(reports[0])
            self.assertEqual([item['coordinate'] for item in report['audit']['semantic_blockers']], names)
            proof = root / 'report.json'
            proof.write_text(reports[0])
            for seed in ('2', '3'):
                result = subprocess.run(
                    [sys.executable, '-m', 'coordguard', 'verify-report', str(proof),
                     '--left', str(paths[0]), '--right', str(paths[1])],
                    env=dict(os.environ, PYTHONHASHSEED=seed), text=True, capture_output=True)
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertTrue(json.loads(result.stdout)['valid'], result.stdout)
