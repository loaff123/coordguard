"""Symbolic named-dimension arithmetic over the explicitly declared core profile."""
from math import prod
from .model import ArraySnapshot, Contract, Label, ValidationError, representable_binary64
from .budget import Budget, Limits, ResourceLimit
from .matching import correspond

JOINS=('inner','outer','left','right','exact')
XARRAY_PROFILE='xarray-netcdf-2025.10.1-v1'
SPATIAL_ROLES={'spatial','latitude','longitude','projection_x_coordinate','projection_y_coordinate'}


def _diagnostic(code, dimension=None, side=None):
    result={'code':code}
    if dimension is not None:result['dimension']=dimension
    if side is not None:result['side']=side
    return result


def _axis_keys(axis, budget, runtime=False):
    if axis is None:return None,None
    if axis.index_kind!='ordinary1d':return None,'unsupported_index_kind'
    meta=axis.metadata
    decoded_time=runtime and meta.time_encoding in ('datetime64[s]','datetime64[ms]','datetime64[us]','datetime64[ns]') and (axis.labels is not None and all(x.kind=='datetime' for x in axis.labels))
    if decoded_time and meta.calendar not in (None,'standard','gregorian','proleptic_gregorian'):return None,'unsupported_calendar'
    if not decoded_time and (meta.calendar is not None or meta.time_encoding is not None or meta.role=='time' or (meta.units and ' since ' in meta.units)):
        return None,'time_semantics_not_implemented'
    if not axis.indexed:return None,None
    keys=[];families=set()
    for label in axis.labels:
        budget.tick()
        if not label.finite:return None,'nonfinite_or_null_label'
        if label.kind=='datetime' and not decoded_time:return None,'datetime_not_implemented'
        keys.append(label.key());families.add(keys[-1][0])
    if len(families)>1:return None,'mixed_string_numeric_index'
    if any(x.kind in ('float32','float64') for x in axis.labels) and any(x.kind=='int' and not representable_binary64(x) for x in axis.labels):
        return None,'unsafe_mixed_integer_float'
    return keys,None


def _declared_family(axis,keys):
    if keys:return keys[0][0]
    dtype=axis.source_dtype or ''
    if dtype.startswith('datetime64[') or (axis.metadata.time_encoding or '').startswith('datetime64['):return 'datetime'
    if dtype.startswith(('int','uint','float')):return 'number'
    if dtype=='object' or dtype.startswith(('<U','>U','U','str')):return 'str'
    return None


def _duplicates(keys, labels, budget):
    if keys is None:return []
    positions={}
    for i,key in enumerate(keys):
        budget.tick();positions.setdefault(key,[]).append(i)
    return [{'label':labels[v[0]].to_dict(),'positions':v,'multiplicity':len(v)}
            for v in positions.values() if len(v)>1]


def _reordered(indexer):
    if type(indexer) is not list:return False
    valid=[i for i in indexer if i is not None]
    return any(a>b for a,b in zip(valid,valid[1:]))


def _dimension(name, left, right, join, budget, runtime=False):
    fact={'name':name,'left_size':None if left is None else left.size,'right_size':None if right is None else right.size,
          'mode':None,'status':'complete','reason':None,'output_size':None,'labels':None,
          'left_indexer':None,'right_indexer':None,'left_dropped':None,'right_dropped':None,
          'left_duplicates':[],'right_duplicates':[],'left_reordered':None,'right_reordered':None,
          'correspondence_status':'not_requested','correspondence':None}
    lk,le=_axis_keys(left,budget,runtime);rk,re=_axis_keys(right,budget,runtime)
    if le or re:
        fact.update(status='unsupported',reason=le or re);return fact
    if lk is not None:fact['left_duplicates']=_duplicates(lk,left.labels,budget)
    if rk is not None:fact['right_duplicates']=_duplicates(rk,right.labels,budget)
    if left is None or right is None:
        own=right if left is None else left
        fact.update(mode='named_broadcast',output_size=own.size,
                    labels=None if own.labels is None else [x.to_dict() for x in own.labels])
        fact['left_indexer']={'kind':'broadcast'} if left is None else {'kind':'identity','size':own.size}
        fact['right_indexer']={'kind':'broadcast'} if right is None else {'kind':'identity','size':own.size}
        fact['left_dropped']=[];fact['right_dropped']=[]
    elif lk is None or rk is None:
        if left.size!=right.size:
            fact.update(status='operation_error',reason='positional_size_mismatch');return fact
        indexed=left if lk is not None else right if rk is not None else None
        fact.update(mode='positional_with_index' if indexed else 'positional',output_size=left.size,
                    labels=None if indexed is None else [x.to_dict() for x in indexed.labels],
                    left_indexer={'kind':'identity','size':left.size},right_indexer={'kind':'identity','size':right.size},
                    left_dropped=[],right_dropped=[])
    else:
        lf=_declared_family(left,lk);rf=_declared_family(right,rk)
        if lf is not None and rf is not None and lf!=rf:
            fact.update(status='unsupported',reason='incompatible_label_families');return fact
        if runtime and left.metadata.time_encoding and right.metadata.time_encoding and left.metadata.time_encoding!=right.metadata.time_encoding:
            fact.update(status='unsupported',reason='mixed_datetime_resolutions');return fact
        if runtime and left.source_dtype and right.source_dtype and {left.source_dtype[:1],right.source_dtype[:1]}=={'i','u'}:
            fact.update(status='unsupported',reason='unsafe_integer_dtype_promotion');return fact
        all_labels=left.labels+right.labels
        if any(x.kind in ('float32','float64') for x in all_labels) and any(x.kind=='int' and not representable_binary64(x) for x in all_labels):
            fact.update(status='unsupported',reason='unsafe_mixed_integer_float');return fact
        if join=='exact' and lk!=rk:
            fact.update(status='operation_error',reason='exact_indexes_differ');return fact
        if lk==rk:
            fact.update(mode='identical_index',output_size=left.size,labels=[x.to_dict() for x in (right.labels if join=='right' and not runtime else left.labels)],
                        left_indexer={'kind':'identity','size':left.size},right_indexer={'kind':'identity','size':right.size},
                        left_dropped=[],right_dropped=[],correspondence_status='exact')
        elif fact['left_duplicates'] or fact['right_duplicates']:
            fact.update(status='operation_error' if runtime else 'unsupported',reason='nonidentical_duplicate_indexes');return fact
        else:
            lmap={key:i for i,key in enumerate(lk)};rmap={key:i for i,key in enumerate(rk)}
            if join=='left':joined=lk
            elif join=='right':joined=rk
            elif join=='inner':joined=[key for key in lk if key in rmap]
            else:joined=(lk or rk) if runtime and (not lk or not rk) else sorted(set(lk)|set(rk))
            budget.tick(len(joined));li=[lmap.get(key) for key in joined];ri=[rmap.get(key) for key in joined]
            labels=[(right.labels[rmap[key]] if join=='right' or key not in lmap else left.labels[lmap[key]]).to_dict() for key in joined]
            if runtime and join in ('inner','outer') and (any(x.kind in ('float32','float64') for x in all_labels) or any((x.source_dtype or '').startswith('float') for x in (left,right))):
                kinds={x.kind for x in all_labels}
                kinds.update(x.source_dtype for x in (left,right) if x.source_dtype in ('float32','float64'))
                if any((x.source_dtype or '').startswith(('int','uint')) for x in (left,right)):kinds.add('int')
                small_ints=all((x.source_dtype or '') in ('int8','uint8','int16','uint16','float32') for x in (left,right))
                kind='float64' if 'float64' in kinds or ('int' in kinds and not small_ints) else 'float32'
                labels=[Label.from_value(float(Label.from_dict(x).number()),kind).to_dict() for x in labels]
            lret=set(i for i in li if i is not None);rret=set(i for i in ri if i is not None)
            fact.update(mode='indexed_join',output_size=len(joined),labels=labels,left_indexer=li,right_indexer=ri,
                        left_dropped=[{'index':i,'label':x.to_dict()} for i,x in enumerate(left.labels) if i not in lret],
                        right_dropped=[{'index':i,'label':x.to_dict()} for i,x in enumerate(right.labels) if i not in rret])
    fact['left_reordered']=_reordered(fact['left_indexer']);fact['right_reordered']=_reordered(fact['right_indexer'])
    return fact


def _semantics(name,left,right,requested,declared):
    if left is None or right is None:
        return ([_diagnostic('correspondence_needs_shared_dimension',name)] if requested else []),requested
    a=left.metadata;b=right.metadata;blockers=[]
    for key in ('units','location','crs','calendar','role'):
        x=getattr(a,key);y=getattr(b,key)
        if key=='calendar' and a.time_encoding and b.time_encoding:
            x=x or 'standard';y=y or 'standard'
        if key=='units' and a.time_encoding and b.time_encoding and a.time_encoding.startswith('datetime64[') and b.time_encoding.startswith('datetime64['):continue
        if x is not None and y is not None and x!=y:blockers.append(_diagnostic(key+'_mismatch',name))
    if requested and (a.role in SPATIAL_ROLES or b.role in SPATIAL_ROLES):
        if (a.units is None or b.units is None or a.location is None or b.location is None) and not declared:
            blockers.append(_diagnostic('spatial_same_axis_declaration_required',name))
    return blockers,bool(blockers)


def _impact(snapshot, axes, output_total, side):
    own={axis.name:axis for axis in snapshot.axes};retained=[];valid=[];broadcast=[]
    for fact in axes:
        name=fact['name'];size=fact['output_size'];idx=fact[side+'_indexer']
        if name not in own:
            broadcast.append(name);valid.append(size)
        elif type(idx) is list:
            positions={i for i in idx if i is not None};retained.append(len(positions));valid.append(sum(i is not None for i in idx))
        else:
            retained.append(own[name].size);valid.append(size)
    input_total=prod(axis.size for axis in snapshot.axes)
    kept=prod(retained)
    if any(fact['name'] in broadcast and fact['output_size']==0 for fact in axes):kept=0
    contributing=prod(valid)
    return {'input_positions':input_total,'retained_source_positions':kept,'excluded_source_positions':input_total-kept,
            'contributing_output_positions':contributing,'fill_output_positions':output_total-contributing,
            'broadcast_dimensions':broadcast,
            'excluded_fraction':None if input_total==0 else {'numerator':input_total-kept,'denominator':input_total}}


def audit(left_snapshot,right_snapshot,operation='binary',join='inner',contract=None,limits=None,*,budget=None):
    """Forecast structural slots, not actual nonmissing observations or RAM.

Manifest profile ordering remains explicit. The NetCDF snapshot profile
is separately validated against the exact pinned xarray runtime.
    """
    if type(left_snapshot) is not ArraySnapshot or type(right_snapshot) is not ArraySnapshot:
        raise ValidationError('audit requires typed ArraySnapshot inputs')
    if operation!='binary' or join not in JOINS:raise ValidationError('Only binary operation and five declared joins are supported')
    if limits is not None and type(limits) is not Limits:raise ValidationError('Expected Limits')
    if budget is not None and type(budget) is not Budget:raise ValidationError('Expected Budget')
    if budget is not None and limits is not None:raise ValidationError('Pass budget or limits, not both')
    contract=Contract() if contract is None else contract
    if type(contract) is not Contract:raise ValidationError('Expected Contract')
    runtime=left_snapshot.decoding_profile==right_snapshot.decoding_profile==XARRAY_PROFILE
    left={x.name:x for x in left_snapshot.axes};right={x.name:x for x in right_snapshot.axes}
    names=list(left)+[name for name in right if name not in left]
    tolerances=dict(contract.tolerances)
    if any(name not in names for name in tolerances) or any(name not in names for name in contract.same_axis_declarations):
        raise ValidationError('Contract references a dimension absent from both inputs')
    report={'schema_version':1,'profile':'coordguard-core-v1','operation':operation,'join':join,
            'forecast_status':'complete','contract_status':'unverified','left_digest':None,'right_digest':None,
            'contract':contract.to_dict(),'axes':[],'output':None,'impact':None,'violations':[],
            'semantic_blockers':[],'diagnostics':[],'limits':(limits.to_dict() if limits is not None else None),'resources':None,
            'claims':{'payload_inspected':False,'physical_equivalence':False,'xarray_runtime_validated':runtime}}
    try:
        budget=Budget(limits) if budget is None else budget;report['limits']=budget.limits.to_dict()
        for side,snapshot in (('left',left_snapshot),('right',right_snapshot)):
            texts=(snapshot.name,snapshot.decoding_profile)+snapshot.semantic_blockers
            budget.reserve(65536+32*sum(len(value.encode('utf-8')) for value in texts))
            for axis in snapshot.axes:budget.inspect_axis(axis,(side,axis.name))
            report[side+'_digest']=snapshot.digest();budget.check()
            if snapshot.decoding_profile not in ('typed-manifest-v1',XARRAY_PROFILE):
                report['diagnostics'].append(_diagnostic('unsupported_decoding_profile',side=side));report['forecast_status']='unsupported'
            for blocker in snapshot.semantic_blockers:
                report['semantic_blockers'].append({'code':'declared_unmodeled_semantics','detail':blocker,'side':side})
                report['forecast_status']='unsupported'
        for name in names:
            budget.tick();a=left.get(name);b=right.get(name)
            fact=_dimension(name,a,b,join,budget,runtime);report['axes'].append(fact)
            blockers,blocked=_semantics(name,a,b,name in tolerances,name in contract.same_axis_declarations)
            report['semantic_blockers'].extend(blockers)
            if a is not None and b is not None and (a.metadata.crs!=b.metadata.crs) and (a.metadata.crs is not None or b.metadata.crs is not None):
                fact.update(status='unsupported',reason='crs_compatibility_not_implemented')
            if fact['status']!='complete':
                report['diagnostics'].append(_diagnostic(fact['reason'],name))
                if report['forecast_status']!='unsupported':report['forecast_status']=fact['status']
            if name in tolerances:
                if blocked or a is None or b is None or fact['status']=='unsupported':
                    fact['correspondence_status']='unsupported'
                else:
                    evidence=correspond(a,b,tolerances[name],budget=budget)
                    fact['correspondence_status']=evidence['status'];fact['correspondence']=evidence
                    if evidence['status']=='resource_limited':raise ResourceLimit(evidence['reason'],'Correspondence resource limit')
                    if evidence['status']=='unsupported':
                        report['semantic_blockers'].append(_diagnostic('correspondence_'+evidence['reason'],name))
                    elif evidence['status'] in ('ambiguous','impossible'):
                        report['violations'].append(_diagnostic('correspondence_'+evidence['status'],name))
            for side in ('left','right'):
                if fact[side+'_duplicates'] and not contract.allow_duplicates:
                    report['violations'].append(_diagnostic('duplicate_labels',name,side))
        if report['forecast_status']=='complete':
            sizes=[x['output_size'] for x in report['axes']];total=prod(sizes)
            report['output']={'dimensions':names,'sizes':sizes,'total_positions':total}
            report['impact']={side:_impact(snapshot,report['axes'],total,side) for side,snapshot in [('left',left_snapshot),('right',right_snapshot)]}
            for side,impact in report['impact'].items():
                for field,allow,code in [('excluded_source_positions',contract.allow_drops,'dropped_positions'),
                                         ('fill_output_positions',contract.allow_fills,'fill_positions'),
                                         ('broadcast_dimensions',contract.allow_broadcast,'named_dimension_broadcast')]:
                    if impact[field] and not allow:report['violations'].append(_diagnostic(code,side=side))
            unsupported_correspondence=any(x['correspondence_status']=='unsupported' for x in report['axes'])
            report['contract_status']='unverified' if report['semantic_blockers'] or unsupported_correspondence else 'fail' if report['violations'] else 'pass'
        elif report['forecast_status']=='operation_error':report['contract_status']='fail'
        budget.check()
    except ResourceLimit as exc:
        report.update(forecast_status='resource_limited',contract_status='unverified',output=None,impact=None)
        report['diagnostics'].append(_diagnostic(exc.code))
    if budget is not None:report['resources']=budget.facts()
    return report


def exit_code(report):
    if report['forecast_status'] in ('unsupported','resource_limited') or report['contract_status']=='unverified':return 3
    return 0 if report['forecast_status']=='complete' and report['contract_status']=='pass' else 1
