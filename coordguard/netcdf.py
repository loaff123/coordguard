"""Read-only, metadata-first NetCDF4 extraction. Scientific payloads are never read."""
import hashlib
import importlib.metadata
import math
import os
import stat
import warnings
from .model import ArraySnapshot, Axis, Label, Metadata, ValidationError, canonical_json
from .budget import Budget, Limits, ResourceLimit

PROFILE = 'xarray-netcdf-2025.10.1-v1'
PINNED = {'xarray':'2025.10.1','numpy':'2.3.5','pandas':'2.2.3','h5netcdf':'1.6.3','h5py':'3.14.0'}
SETTINGS = {'engine':'h5netcdf','mode':'r','chunks':None,'cache':False,'decode_cf':False,
            'create_default_indexes':False,'phony_dims':None,'coordinate_decode_times':'CFDatetimeCoder(use_cftime=False,time_unit=s)',
            'coordinate_mask_and_scale':False,'coordinate_decode_timedelta':False}
ENCODING_KEYS = ('units','calendar','_FillValue','missing_value','scale_factor','add_offset','_Unsigned',
                 'coordinates','bounds','grid_mapping','axis','standard_name','location','crs')

class UnsupportedInput(ValidationError):
    code = 'unsupported_input'


class UnsupportedRuntime(ValidationError):
    code = 'unsupported_runtime'


def runtime_versions():
    try: versions={name:importlib.metadata.version(name) for name in PINNED}
    except importlib.metadata.PackageNotFoundError as exc:
        raise UnsupportedRuntime('Install the pinned coordguard[netcdf] extra') from exc
    if versions != PINNED:
        raise UnsupportedRuntime('NetCDF profile requires exactly: '+canonical_json(PINNED)+'; found: '+canonical_json(versions))
    return versions


def _text(value):
    if value is None:return None
    if isinstance(value, bytes): value=value.decode('utf-8',errors='strict')
    if not isinstance(value,str):raise ValidationError('Semantic metadata must be text')
    if len(value.encode('utf-8'))>4096:raise ValidationError('Metadata text exceeds 4096 bytes')
    return value


def _encoding(attrs):
    result={}
    for key in ENCODING_KEYS:
        if key not in attrs:continue
        value=attrs[key]
        if hasattr(value,'ndim') and value.ndim!=0:raise ValidationError('Array-valued semantic attributes are unsupported')
        if hasattr(value,'item'):value=value.item()
        if isinstance(value,bytes):value=value.decode('utf-8',errors='strict')
        if type(value) is str:
            _text(value);result[key]=value
        elif type(value) in (int,float):result[key]={'numeric_text':repr(value)}
        else:raise ValidationError('Unsupported semantic attribute type')
    return result


def _metadata(attrs, time_encoding=None):
    return Metadata(units=_text(attrs.get('units')),calendar=_text(attrs.get('calendar')),
                    role=_text(attrs.get('standard_name') or ('time' if ' since ' in str(attrs.get('units','')) else attrs.get('axis'))),
                    location=_text(attrs.get('location')),crs=_text(attrs.get('crs')),
                    time_encoding=time_encoding)


def _attribute_vlen(datatype):
    import h5py
    kind=datatype.get_class()
    if kind==h5py.h5t.VLEN:return True
    if kind==h5py.h5t.STRING:return datatype.is_variable_str()
    if kind==h5py.h5t.COMPOUND:return any(_attribute_vlen(datatype.get_member_type(i)) for i in range(datatype.get_nmembers()))
    if kind==h5py.h5t.ARRAY:return _attribute_vlen(datatype.get_super())
    return False


def _preflight_hdf5(stream,budget):
    """Reject external/virtual links and bound metadata before xarray decodes it.

Native HDF5 library metadata allocations are not a hard RSS/sandbox guarantee.
Only root-group NetCDF4 is admitted. No external dataset can be followed.
    """
    import h5py
    import numpy as np
    try:
        with h5py.File(stream,'r') as f:
            if len(f)>4096:raise ResourceLimit('metadata_limit','Too many root variables')
            metadata_size=0;seen_objects=set();root_names=[]
            # HDF5 exposes each name before Python can inspect its length. Do not
            # retain, encode, or inspect the target of an oversized exposed name.
            for name in f:
                budget.tick()
                if type(name) is not str:raise ValidationError('Root object names must be UTF-8 text')
                if len(name)>4096:raise ResourceLimit('metadata_limit','Root object name exceeds 4096 bytes')
                name_bytes=len(name.encode('utf-8'))
                if name_bytes>4096:raise ResourceLimit('metadata_limit','Root object name exceeds 4096 bytes')
                metadata_size+=name_bytes+128
                if metadata_size>1024*1024:raise ResourceLimit('metadata_limit','Root names exceed aggregate metadata cap')
                budget.reserve(128+32*name_bytes);root_names.append(name)
            root_identities={h5py.h5o.get_info(f[name].id).addr for name in root_names if isinstance(f.get(name,getlink=True),h5py.HardLink)}
            for name in [None]+root_names:
                budget.tick()
                if name is None:obj=f
                else:
                    if not isinstance(f.get(name,getlink=True),h5py.HardLink):raise ValidationError('External/soft HDF5 links are not admitted')
                    obj=f[name]
                    identity=h5py.h5o.get_info(obj.id).addr
                    if identity in seen_objects:raise ValidationError('Aliased HDF5 hard links are not admitted')
                    seen_objects.add(identity)
                    if not isinstance(obj,h5py.Dataset):raise ValidationError('Only root-group NetCDF4 datasets are supported')
                    if obj.shape is None:raise ValidationError('Null HDF5 dataset dataspaces are not supported')
                    if obj.dtype.hasobject or obj.dtype.kind=='T':raise UnsupportedInput('unbounded_coordinate_dtype: object/variable-length storage is rejected before xarray opening: '+name)
                    if obj.is_virtual or obj.external:raise ValidationError('Virtual/external HDF5 storage is not admitted')
                    if len(obj.shape)>32:raise ValidationError('Too many coordinate/payload dimensions')
                    if obj.ndim==0 and h5py.h5ds.is_scale(obj.id):raise ValidationError('Scalar dimension scales are not supported')
                    if obj.ndim and not h5py.h5ds.is_scale(obj.id) and 'DIMENSION_LIST' not in obj.attrs:
                        raise ValidationError('Missing NetCDF dimension scales; dimension names may not be inferred')
                if len(obj.attrs)>128:raise ResourceLimit('metadata_limit','Too many metadata attributes')
                for key in obj.attrs:
                    if type(key) is not str:raise ValidationError('Attribute names must be UTF-8 text')
                    if len(key)>4096:raise ResourceLimit('metadata_limit','Attribute name exceeds 4096 bytes')
                    key_bytes=len(key.encode('utf-8'))
                    if key_bytes>4096:raise ResourceLimit('metadata_limit','Attribute name exceeds 4096 bytes')
                    metadata_size+=key_bytes+128
                    if metadata_size>1024*1024:raise ResourceLimit('metadata_limit','Attribute names exceed aggregate metadata cap')
                    budget.reserve(128+32*key_bytes)
                    aid=obj.attrs.get_id(key)
                    if aid.shape is None:raise ValidationError('Null HDF5 attribute dataspaces are not supported')
                    count=math.prod(aid.shape)
                    if count>4096:raise ResourceLimit('metadata_limit','Attribute element count exceeds cap')
                    attr_type=aid.get_type()
                    if key=='DIMENSION_LIST' and not (name is not None and aid.shape==(len(obj.shape),)
                            and attr_type.get_class()==h5py.h5t.VLEN and attr_type.get_super().get_class()==h5py.h5t.REFERENCE):
                        raise ValidationError('Malformed NetCDF DIMENSION_LIST schema')
                    if attr_type.get_class()==h5py.h5t.STRING and not attr_type.is_variable_str() and attr_type.get_size()>4096:
                        raise ResourceLimit('metadata_limit','Fixed attribute text exceeds 4096 bytes')
                    if _attribute_vlen(attr_type):
                        if attr_type.get_class()==h5py.h5t.STRING and attr_type.is_variable_str():
                            budget.reserve(count*8192)
                            memory_type=h5py.h5t.C_S1.copy();memory_type.set_size(4097);memory_type.set_cset(attr_type.get_cset())
                            bounded=np.zeros(aid.shape,dtype='S4097');aid.read(bounded,mtype=memory_type)
                            lengths=[len(x) for x in bounded.reshape(-1)]
                            if any(n>4096 for n in lengths):raise ResourceLimit('metadata_limit','Attribute text exceeds 4096 bytes')
                            metadata_size+=sum(lengths)+count*128
                        elif key=='DIMENSION_LIST' and name is not None and aid.shape==(len(obj.shape),) and attr_type.get_class()==h5py.h5t.VLEN and attr_type.get_super().get_class()==h5py.h5t.REFERENCE:
                            if any(h5py.h5ds.get_num_scales(obj.id,i)!=1 for i in range(len(obj.shape))):
                                raise ValidationError('Exactly one dimension scale per axis is required')
                            for i in range(len(obj.shape)):
                                def check_scale(scale):
                                    if h5py.h5o.get_info(scale).addr not in root_identities:return 'Dimension scale points outside admitted root objects'
                                    if not h5py.h5ds.is_scale(scale):return 'Dimension reference does not identify a genuine dimension scale'
                                    if scale.get_space().get_simple_extent_ndims()==0:return 'Scalar dimension scales are not supported'
                                scale_error=h5py.h5ds.iterate(obj.id,i,check_scale)
                                if scale_error is not None:raise ValidationError(scale_error)
                            metadata_size+=len(obj.shape)*256
                        else:raise UnsupportedInput('Unsupported variable-length attribute: '+key)
                    else:
                        attribute_bytes=count*aid.dtype.itemsize
                        if attribute_bytes>65536:raise ResourceLimit('metadata_limit','Fixed attribute storage exceeds cap')
                        metadata_size+=attribute_bytes
                    metadata_size+=aid.get_storage_size()
                    if metadata_size>1024*1024:raise ResourceLimit('metadata_limit','HDF5 attribute storage exceeds 1 MiB')
            budget.reserve(32*metadata_size+65536*max(1,len(f)))
    except (OSError,ValueError,TypeError,IndexError,RuntimeError) as exc:
        if isinstance(exc,(ValidationError,ResourceLimit)):raise
        raise ValidationError('Not a supported local NetCDF4/HDF5 file') from exc
    stream.seek(0)


def _admit(var,budget):
    count=math.prod(var.shape);size=count*var.dtype.itemsize
    chunks=var.encoding.get('chunksizes')
    if chunks:
        chunk_bytes=math.prod(chunks)*var.dtype.itemsize
        if chunk_bytes>budget.limits.max_coordinate_bytes:raise ResourceLimit('coordinate_chunk_limit','Uncompressed coordinate chunk exceeds coordinate cap')
        budget.reserve(chunk_bytes*2)
    total_labels=getattr(budget,'source_labels',0)+count
    total_bytes=getattr(budget,'source_coordinate_bytes',0)+size
    if total_labels>budget.limits.max_labels:raise ResourceLimit('label_limit','Source coordinate labels exceed cap before reading')
    if total_bytes>budget.limits.max_coordinate_bytes:raise ResourceLimit('coordinate_byte_limit','Uncompressed source coordinates exceed cap before reading')
    budget.reserve(count*(2048+16*max(8,var.dtype.itemsize))+size*4+65536)
    budget.source_labels=total_labels;budget.source_coordinate_bytes=total_bytes
    budget.check()


def _labels(array,budget):
    import numpy as np
    result=[];kind=array.dtype.kind
    for value in array.reshape(-1):
        budget.tick()
        if kind in 'iu':label=Label.from_value(int(value))
        elif kind=='f' and array.dtype.itemsize in (4,8):label=Label.from_value(float(value),'float32' if array.dtype.itemsize==4 else 'float64')
        elif kind=='O' and type(value) is str:
            # Object output is admitted only after decoding bounded fixed-width source bytes.
            label=Label.from_value(value)
        elif kind=='U':label=Label.from_value(str(value))
        elif kind=='M':
            unit=np.datetime_data(array.dtype)[0]
            if unit not in ('s','ms','us','ns') or np.isnat(value):raise ValidationError('Unsupported datetime resolution or NaT')
            label=Label('datetime',unit+':'+str(int(value.astype('int64'))))
        else:raise ValidationError('Unsupported decoded coordinate dtype')
        if not label.finite:raise ValidationError('Invalid nonfinite coordinate label')
        result.append(label)
    return tuple(result)


def _extract_dataset(ds,variable,budget,versions):
    import numpy as np
    import xarray as xr
    if variable not in ds.variables:raise ValidationError('Selected variable is absent')
    field=ds.variables[variable]  # metadata only, not DataArray.values/data
    if len(field.dims)>32:raise ValidationError('At most 32 scientific dimensions are supported')
    start_labels=getattr(budget,'source_labels',0);start_bytes=getattr(budget,'source_coordinate_bytes',0)
    blockers=[];names=list(field.dims)
    for attrs in (ds.attrs,field.attrs):
        refs=_text(attrs.get('coordinates'))
        if refs:names.extend(refs.split())
    names=list(dict.fromkeys(names))
    if len(names)>64:raise ResourceLimit('coordinate_count_limit','At most 64 coordinate references are admitted')
    for key in ('bounds','grid_mapping'):
        if key in field.attrs:blockers.append('unsupported_'+key+':'+str(field.attrs[key]))
    coordinates=[];decoded={};plan=[]
    for name in names:
        budget.tick()
        if name==variable:
            blockers.append('selected_payload_used_as_coordinate:'+name);continue
        if name not in ds.variables:
            if name not in field.dims:blockers.append('missing_coordinate_reference:'+name)
            continue
        var=ds.variables[name];attrs=var.attrs
        enc=_encoding(attrs)
        rec={'name':name,'dimensions':list(var.dims),'shape':list(var.shape),'source_dtype':str(var.dtype),'encoding':enc}
        coordinates.append(rec)
        for key in ('bounds','grid_mapping'):
            if key in attrs:blockers.append('unsupported_'+key+':'+name+':'+str(attrs[key]))
        if any(d not in field.dims for d in var.dims) or var.ndim>1:
            # A fixed-width CF character axis adds one trailing string dimension.
            char_axis=var.dtype.kind=='S' and var.dtype.itemsize==1 and var.ndim==2 and var.dims[0] in field.dims
            if not char_axis:
                blockers.append('multidimensional_coordinate:'+name);continue
        if var.dtype.kind not in 'iufSU' or var.dtype.hasobject:
            blockers.append('unbounded_coordinate_dtype:'+name);continue
        if var.dtype.kind=='f' and var.dtype.itemsize not in (4,8):
            blockers.append('unsupported_float_precision:'+name);continue
        if any(k in attrs for k in ('scale_factor','add_offset','_Unsigned')):
            blockers.append('unsupported_packed_coordinate:'+name);continue
        calendar=_text(attrs.get('calendar'))
        if calendar and calendar not in ('standard','gregorian','proleptic_gregorian'):
            blockers.append('unsupported_calendar:'+name+':'+calendar);continue
        _admit(var,budget);plan.append((name,var,rec))
    # All allowed arrays pass admission before the first coordinate data access.
    for name,var,rec in plan:
        budget.check()
        raw=var.values  # only a previously admitted coordinate variable
        try:
            if any(k in var.attrs and np.any(raw==var.attrs[k]) for k in ('_FillValue','missing_value')):
                raise ValidationError('Coordinate contains a declared fill value')
            # Build a coordinate-only object from bounded arrays. Never decode the source Dataset.
            coordinate_only=xr.Dataset({name:xr.Variable(var.dims,raw,attrs=dict(var.attrs))})
            with warnings.catch_warnings():
                warnings.simplefilter('error')
                cooked=xr.decode_cf(coordinate_only,mask_and_scale=False,decode_times=xr.coders.CFDatetimeCoder(use_cftime=False,time_unit='s'),
                                    decode_timedelta=False,decode_coords=False,concat_characters=True)
            cv=cooked.variables[name];array=cv.values
            if array.dtype.kind=='S':
                blockers.append('unsupported_byte_string_coordinate:'+name);continue
            labels=_labels(array,budget)
            time_encoding=str(array.dtype) if array.dtype.kind=='M' else None
            meta=_metadata(var.attrs,time_encoding)
            rec.update(dimensions=list(cv.dims),labels=[x.to_dict() for x in labels],metadata=meta.to_dict(),decoded_dtype=str(array.dtype))
            decoded[name]=(tuple(cv.dims),labels,meta,str(array.dtype))
        except (ValueError,TypeError,OverflowError,Warning) as exc:
            blockers.append('invalid_coordinate_label_or_decode:'+name+':'+type(exc).__name__)
    axes=[]
    for name in field.dims:
        item=decoded.get(name)
        if item is not None and item[0]==(name,):
            axes.append(Axis(name,field.sizes[name],item[1],item[2],source_dtype=item[3]))
        else:
            if name in ds.variables and name not in decoded:blockers.append('unavailable_dimension_coordinate:'+name)
            elif item is not None:blockers.append('nonordinary_dimension_coordinate:'+name)
            axes.append(Axis(name,field.sizes[name]))
    auxiliary=[dict(name=name,dimensions=list(dims),labels=[x.to_dict() for x in labels],metadata=meta.to_dict(),source_dtype=dtype)
               for name,(dims,labels,meta,dtype) in decoded.items() if name not in field.dims]
    snapshot=ArraySnapshot(variable,tuple(axes),PROFILE,tuple(dict.fromkeys(blockers)))
    result={'snapshot':snapshot.to_dict(),'coordinates':coordinates,'auxiliary':auxiliary,
            'selected_metadata':_encoding(field.attrs),'settings':dict(SETTINGS),'versions':versions,
            'source_coordinate_bytes':getattr(budget,'source_coordinate_bytes',0)-start_bytes,'source_labels':getattr(budget,'source_labels',0)-start_labels}
    budget.reserve(65536+16*sum(len(canonical_json(c)) for c in coordinates))
    result['digest']=hashlib.sha256(canonical_json(result).encode()).hexdigest()
    return result


def extract_netcdf(path,variable,*,budget=None,limits=None):
    """Extract only bounded coordinate arrays from a local regular root-group NetCDF4 file.

Paths and payload bytes are deliberately excluded from deterministic digests.
"""
    if type(path) is not str or '://' in path or type(variable) is not str or not variable:raise ValidationError('Local file path and selected variable name are required')
    if budget is not None and limits is not None:raise ValidationError('Pass budget or limits, not both')
    if budget is not None and type(budget) is not Budget:raise ValidationError('Expected Budget')
    budget=Budget(limits) if budget is None else budget
    versions=runtime_versions()
    import xarray as xr
    try:
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        with os.fdopen(fd,'rb') as stream:
            before=os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):raise ValidationError('Input is not a local regular file')
            _preflight_hdf5(stream,budget)
            with xr.open_dataset(stream,engine='h5netcdf',chunks=None,cache=False,decode_cf=False,create_default_indexes=False,backend_kwargs={'phony_dims':None}) as ds:
                result=_extract_dataset(ds,variable,budget,versions)
            after=os.fstat(stream.fileno())
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValidationError('Input changed during extraction')
            return result
    except (OSError,UnicodeError,ValueError,TypeError,IndexError,RuntimeError) as exc:
        if isinstance(exc,(ValidationError,ResourceLimit)):raise
        raise ValidationError('Cannot read supported local NetCDF4 input: '+str(exc)) from exc
