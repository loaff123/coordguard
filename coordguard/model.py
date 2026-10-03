"""Immutable, strictly versioned coordinate-only inputs. No payload values."""
from dataclasses import dataclass, fields
from fractions import Fraction
import hashlib
import json
import math
import re
import struct
from .errors import ValidationError

SCHEMA_VERSION = 1
MAX_TEXT_BYTES = 4096
MAX_AXIS_SIZE = 2**63 - 1


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _fields(value, required):
    required=tuple(required)
    if type(value) is not dict or len(value)!=len(required) or any(key not in value for key in required):
        raise ValidationError('Object must contain exactly: ' + ', '.join(sorted(required)))


def _version(value):
    if type(value) is not int or value != SCHEMA_VERSION:
        raise ValidationError('Unsupported schema_version')


def _text(value, name, optional=False):
    if value is None and optional:
        return
    if type(value) is not str or len(value) > MAX_TEXT_BYTES:
        raise ValidationError(name + ' must be a bounded string')
    try:
        if len(value.encode('utf-8')) > MAX_TEXT_BYTES:
            raise ValidationError(name + ' exceeds 4096 UTF-8 bytes')
    except UnicodeEncodeError as exc:
        raise ValidationError(name + ' contains an unpaired surrogate') from exc


def _tuple(value, name):
    if type(value) is not tuple:
        raise ValidationError(name + ' must be an immutable tuple')


def _list(value, name):
    if type(value) is not list:
        raise ValidationError(name + ' must be a JSON array')


@dataclass(frozen=True)
class Label:
    kind: str
    value: str

    def __post_init__(self):
        _text(self.value, 'label.value')
        if self.kind == 'int':
            if len(self.value) > 79 or not re.fullmatch(r'0|-?[1-9][0-9]*', self.value):
                raise ValidationError('Integer labels require canonical decimal strings')
            if abs(int(self.value)).bit_length() > 256:
                raise ValidationError('Integer labels exceed the 256-bit profile')
        elif self.kind in ('float32', 'float64'):
            digits = 8 if self.kind == 'float32' else 16
            if not re.fullmatch('[0-9a-f]{' + str(digits) + '}', self.value):
                raise ValidationError('Float labels require lowercase big-endian IEEE bits')
        elif self.kind == 'null':
            if self.value != 'null':
                raise ValidationError('Null label value must be null text')
        elif self.kind == 'datetime':
            if not re.fullmatch(r'(s|ms|us|ns):(0|-?[1-9][0-9]*)', self.value):
                raise ValidationError('Datetime requires an exact s/ms/us/ns epoch tick')
            tick=int(self.value.split(':')[1])
            if not -(2**63)<tick<2**63:
                raise ValidationError('Datetime tick is outside int64 or is NaT')
        elif self.kind != 'str':
            raise ValidationError('Unsupported label kind')

    @classmethod
    def from_value(cls, value, kind=None):
        if kind is None:
            kind = {int:'int', float:'float64', str:'str', type(None):'null'}.get(type(value))
        if kind == 'int' and type(value) is int:
            if abs(value).bit_length() > 256:
                raise ValidationError('Integer labels exceed the 256-bit profile')
            return cls(kind, str(value))
        if kind in ('float32','float64') and type(value) in (float,int):
            try:
                raw = struct.pack('>f' if kind == 'float32' else '>d', value).hex()
            except (OverflowError, struct.error) as exc:
                raise ValidationError('Value cannot be represented by declared float kind') from exc
            result=cls(kind,raw)
            if type(value) is int and (not result.finite or result.number()!=value):
                raise ValidationError('Explicit float conversion would lose integer bits')
            return result
        if kind in ('str','datetime') and type(value) is str:
            return cls(kind, value)
        if kind == 'null' and value is None:
            return cls('null', 'null')
        raise ValidationError('Invalid label type; booleans and coercions are not admitted')

    @classmethod
    def from_dict(cls, value):
        _fields(value, ('kind','value'))
        return cls(value['kind'], value['value'])

    def to_dict(self):
        return {'kind':self.kind, 'value':self.value}

    def float_value(self):
        return struct.unpack('>f' if self.kind == 'float32' else '>d', bytes.fromhex(self.value))[0]

    @property
    def finite(self):
        return self.kind != 'null' and (self.kind not in ('float32','float64') or math.isfinite(self.float_value()))

    @property
    def numeric(self):
        return self.kind in ('int','float32','float64')

    def number(self):
        if self.kind == 'int':
            return Fraction(int(self.value))
        if self.kind in ('float32','float64') and self.finite:
            return Fraction.from_float(self.float_value())
        raise ValidationError('Label is not a finite numeric value')

    def key(self):
        if self.numeric and self.finite:
            return ('number', self.number())
        if self.kind == 'str':
            return ('str', self.value)
        if self.kind == 'datetime':
            unit,tick=self.value.split(':')
            return ('datetime', Fraction(int(tick), {'s':1,'ms':1000,'us':1000000,'ns':1000000000}[unit]))
        raise ValidationError('Unsupported exact label semantics')

    @property
    def storage_bytes(self):
        if self.kind == 'int':
            return max(8, (abs(int(self.value)).bit_length()+7)//8)
        if self.kind in ('float32','float64'):
            return 4 if self.kind == 'float32' else 8
        return len(self.value.encode('utf-8'))

    @property
    def display(self):
        return repr(self.float_value()) if self.kind in ('float32','float64') else self.value


@dataclass(frozen=True)
class Metadata:
    units: str | None = None
    calendar: str | None = None
    role: str | None = None
    location: str | None = None
    crs: str | None = None
    time_encoding: str | None = None

    def __post_init__(self):
        for field in fields(self):
            _text(getattr(self, field.name), 'metadata.' + field.name, optional=True)

    def to_dict(self):
        return {field.name:getattr(self,field.name) for field in fields(self)}

    @classmethod
    def from_dict(cls, value):
        _fields(value, (field.name for field in fields(cls)))
        return cls(**value)


@dataclass(frozen=True)
class Axis:
    name: str
    size: int
    labels: tuple[Label,...] | None = None
    metadata: Metadata = Metadata()
    index_kind: str = 'ordinary1d'
    source_dtype: str | None = None

    def __post_init__(self):
        _text(self.name, 'axis.name')
        _text(self.source_dtype, 'axis.source_dtype', optional=True)
        if not self.name or type(self.size) is not int or not 0 <= self.size <= MAX_AXIS_SIZE:
            raise ValidationError('Axis needs a nonempty name and bounded nonnegative integer size')
        if type(self.metadata) is not Metadata or self.index_kind not in ('ordinary1d','custom','curvilinear'):
            raise ValidationError('Invalid axis metadata/index kind')
        if self.labels is not None:
            _tuple(self.labels, 'axis.labels')
            if len(self.labels) != self.size or len(self.labels) > 1_000_000 or any(type(x) is not Label for x in self.labels):
                raise ValidationError('Axis labels must have declared size and contain only typed labels')

    @property
    def indexed(self):
        return self.labels is not None

    @classmethod
    def from_values(cls, name, values, *, kind=None, metadata=Metadata()):
        if type(values) not in (tuple,list) or len(values) > 1_000_000:
            raise ValidationError('Values must be a bounded list or tuple')
        return cls(name, len(values), tuple(Label.from_value(x,kind) for x in values), metadata)

    def to_dict(self):
        result={'name':self.name,'size':self.size,'indexed':self.indexed,'index_kind':self.index_kind,
                'labels':None if self.labels is None else [x.to_dict() for x in self.labels],
                'metadata':self.metadata.to_dict()}
        if self.source_dtype is not None:result['source_dtype']=self.source_dtype
        return result

    @classmethod
    def from_dict(cls, value):
        _fields(value, ('name','size','indexed','index_kind','labels','metadata') + (('source_dtype',) if type(value) is dict and 'source_dtype' in value else ()))
        if type(value['indexed']) is not bool or value['indexed'] != (value['labels'] is not None):
            raise ValidationError('indexed must agree with label presence')
        size=value['size']
        if type(size) is not int or not 0<=size<=MAX_AXIS_SIZE:
            raise ValidationError('Invalid declared axis size')
        labels = value['labels']
        if labels is not None:
            _list(labels, 'labels')
            if len(labels)!=size or len(labels)>1_000_000:
                raise ValidationError('Label count differs from size or exceeds maximum labels')
            labels = tuple(Label.from_dict(x) for x in labels)
        return cls(value['name'],value['size'],labels,Metadata.from_dict(value['metadata']),value['index_kind'],value.get('source_dtype'))

    def digest(self):
        return hashlib.sha256(canonical_json(self.to_dict()).encode()).hexdigest()


@dataclass(frozen=True)
class ArraySnapshot:
    name: str
    axes: tuple[Axis,...]
    decoding_profile: str = 'typed-manifest-v1'
    semantic_blockers: tuple[str,...] = ()

    def __post_init__(self):
        _text(self.name,'snapshot.name'); _text(self.decoding_profile,'decoding_profile')
        _tuple(self.axes,'axes'); _tuple(self.semantic_blockers,'semantic_blockers')
        if len(self.axes) > 32 or any(type(x) is not Axis for x in self.axes):
            raise ValidationError('Snapshots admit at most 32 typed dimensions')
        names = [x.name for x in self.axes]
        if len(set(names)) != len(names):
            raise ValidationError('Dimension names must be unique')
        if len(self.semantic_blockers) > 64:
            raise ValidationError('Too many semantic blockers')
        for value in self.semantic_blockers: _text(value,'semantic blocker')

    def to_dict(self):
        return {'schema_version':SCHEMA_VERSION,'name':self.name,'axes':[x.to_dict() for x in self.axes],
                'decoding_profile':self.decoding_profile,'semantic_blockers':list(self.semantic_blockers)}

    @classmethod
    def from_dict(cls, value):
        _fields(value, ('schema_version','name','axes','decoding_profile','semantic_blockers'))
        _version(value['schema_version']); _list(value['axes'],'axes'); _list(value['semantic_blockers'],'semantic_blockers')
        if len(value['axes']) > 32: raise ValidationError('Too many dimensions')
        if len(value['semantic_blockers'])>64:raise ValidationError('Too many semantic blockers')
        return cls(value['name'],tuple(Axis.from_dict(x) for x in value['axes']),value['decoding_profile'],tuple(value['semantic_blockers']))

    def digest(self):
        return hashlib.sha256(canonical_json(self.to_dict()).encode()).hexdigest()


@dataclass(frozen=True)
class Contract:
    allow_drops: bool = False
    allow_fills: bool = False
    allow_broadcast: bool = False
    allow_duplicates: bool = False
    tolerances: tuple[tuple[str,Label],...] = ()
    same_axis_declarations: tuple[str,...] = ()

    def __post_init__(self):
        for name in ('allow_drops','allow_fills','allow_broadcast','allow_duplicates'):
            if type(getattr(self,name)) is not bool: raise ValidationError(name + ' must be boolean')
        _tuple(self.tolerances,'tolerances'); _tuple(self.same_axis_declarations,'same_axis_declarations')
        if len(self.tolerances) > 32 or len(self.same_axis_declarations) > 32:
            raise ValidationError('Too many contract axes')
        names = []
        for pair in self.tolerances:
            if type(pair) is not tuple or len(pair) != 2 or type(pair[1]) is not Label:
                raise ValidationError('Tolerances need (dimension, typed tolerance) pairs')
            name, tol = pair; _text(name,'tolerance dimension'); names.append(name)
            if not tol.numeric or not tol.finite or tol.number() < 0 or not representable_binary64(tol):
                raise ValidationError('Tolerance must be nonnegative finite binary32/64-representable')
        if len(names) != len(set(names)): raise ValidationError('Duplicate tolerance dimension')
        for name in self.same_axis_declarations: _text(name,'same-axis declaration')
        if len(set(self.same_axis_declarations)) != len(self.same_axis_declarations):
            raise ValidationError('Duplicate same-axis declaration')

    def to_dict(self):
        return {'schema_version':1,'allow_drops':self.allow_drops,'allow_fills':self.allow_fills,
                'allow_broadcast':self.allow_broadcast,'allow_duplicates':self.allow_duplicates,
                'tolerances':{k:v.to_dict() for k,v in self.tolerances},'same_axis_declarations':list(self.same_axis_declarations)}

    @classmethod
    def from_dict(cls, value):
        _fields(value, ('schema_version','allow_drops','allow_fills','allow_broadcast','allow_duplicates','tolerances','same_axis_declarations'))
        _version(value['schema_version'])
        if type(value['tolerances']) is not dict: raise ValidationError('Tolerances must be an object')
        _list(value['same_axis_declarations'],'same_axis_declarations')
        if len(value['tolerances'])>32 or len(value['same_axis_declarations'])>32:
            raise ValidationError('Too many contract axes')
        for name in value['tolerances']:_text(name,'tolerance dimension')
        return cls(*(value[k] for k in ('allow_drops','allow_fills','allow_broadcast','allow_duplicates')),
                   tuple((k,Label.from_dict(v)) for k,v in sorted(value['tolerances'].items())),tuple(value['same_axis_declarations']))


def representable_binary64(label):
    if not label.numeric or not label.finite: return False
    if label.kind != 'int': return True
    value = int(label.value)
    return math.isfinite(float(value)) and Fraction.from_float(float(value)) == value
