# Third-party dependency notices

CoordGuard's own source is MIT-licensed. No dependency code, binaries, wheels, or upstream algorithms are vendored in this source distribution. Installing the NetCDF extra obtains separate upstream distributions with their own license files and notices; retain those notices when redistributing an installed environment or binary bundle.

Pinned and tested optional runtime:

- xarray 2025.10.1: Apache-2.0. https://github.com/pydata/xarray/blob/v2025.10.1/LICENSE
- NumPy 2.3.5: BSD-3-Clause; binary distributions may contain additional notices. https://github.com/numpy/numpy/blob/v2.3.5/LICENSE.txt
- pandas 2.2.3: BSD-3-Clause. https://github.com/pandas-dev/pandas/blob/v2.2.3/LICENSE
- h5netcdf 1.6.3: BSD-3-Clause. https://github.com/h5netcdf/h5netcdf/blob/v1.6.3/LICENSE
- h5py 3.14.0: BSD-3-Clause; its binary wheel includes HDF5 components and corresponding HDF5 notices. https://github.com/h5py/h5py/blob/3.14.0/LICENSE

HDF5 is separately licensed by The HDF Group and contributors; consult the installed h5py distribution's bundled licenses before redistributing binary environments. https://www.hdfgroup.org/licenses/

Transitive dependencies (packaging, python-dateutil, pytz, tzdata, and six) retain their upstream distribution metadata and notices. The source archive does not bundle them. Actual tested versions are recorded in validation evidence; only the five behavior-critical packages are pinned by the optional extra.
