"""Complete coordinate audit envelopes and source-bound offline replay."""
from dataclasses import fields
import hashlib
from .budget import Budget, Limits, ResourceLimit
from .errors import ValidationError
from .model import ArraySnapshot, Contract, Label, canonical_json, representable_binary64
from .netcdf import extract_netcdf, UnsupportedInput, UnsupportedRuntime
from .planner import audit, exit_code
from .verify import verify_correspondence


def _key(value):
    return ('fill',) if value is None else Label.from_dict(value).key()


def _auxiliary_family(aux):
    dtype=aux['source_dtype']
    if dtype.startswith('datetime64['):return 'datetime'
    if dtype.startswith(('int','uint','float')):return 'number'
    # Object coordinates are admitted only from bounded CF text decoding.
    if dtype=='object' or dtype.startswith(('<U','>U','U','str')):return 'str'
    return None


def _align_aux(aux,facts,budget):
    dims=aux['dimensions'];labels=aux['labels'];dtype=aux['source_dtype']
    if not dims:return {'dimensions':[],'labels':labels,'source_dtype':dtype}
    dimension=dims[0];fact=facts[dimension]
    indexer=fact['_indexer']
    if isinstance(indexer,dict):aligned=list(labels)
    else:
        budget.reserve(len(indexer)*256)
        aligned=[None if i is None else labels[i] for i in indexer]
    if any(x is None for x in aligned) and dtype.startswith(('int','uint')):
        for value in aligned:
            budget.tick()
            if value is not None and not representable_binary64(Label.from_dict(value)):
                return {'status':'unsupported','reason':'unsafe_integer_fill_promotion'}
        dtype='float32' if dtype in ('int8','int16','uint8','uint16') else 'float64'
        aligned=[None if x is None else Label.from_value(float(Label.from_dict(x).number()),dtype).to_dict() for x in aligned]
    return {'dimensions':dims,'labels':aligned,'source_dtype':dtype}


def auxiliary_facts(left,right,report,budget):
    """Compare scalar/1D coordinates after alignment, without field allocation."""
    if report['forecast_status']!='complete':return []
    sources={side:{x['name']:x for x in source['auxiliary']} for side,source in [('left',left),('right',right)]}
    indexed_names={x['name'] for x in report['axes'] if x['labels'] is not None};result=[]
    for name in dict.fromkeys(list(sources['left'])+list(sources['right'])):
        budget.tick()
        if name in indexed_names:
            result.append({'name':name,'status':'shadowed_by_dimension_index'});continue
        items=[]
        for side in ('left','right'):
            aux=sources[side].get(name)
            if aux is None:items.append(None);continue
            facts={x['name']:dict(x,_indexer=x[side+'_indexer']) for x in report['axes']}
            items.append(_align_aux(aux,facts,budget))
        a,b=items
        unsupported=next((x for x in items if x is not None and x.get('status')=='unsupported'),None)
        if unsupported is not None:
            result.append(dict(name=name,**unsupported));continue
        if a is not None and b is not None:
            dtype_a=a['source_dtype'];dtype_b=b['source_dtype'];reason=None
            family_a=_auxiliary_family(a);family_b=_auxiliary_family(b)
            if family_a is not None and family_b is not None and family_a!=family_b:
                reason='incompatible_label_families'
            elif dtype_a.startswith('datetime64[') and dtype_b.startswith('datetime64[') and dtype_a!=dtype_b:
                reason='mixed_datetime_resolutions'
            # Empty aligned/broadcast arrays compare vacuously; no numeric
            # coercion is needed. Inspect only labels surviving alignment.
            elif a['labels'] and b['labels']:
                if {dtype_a[:1],dtype_b[:1]}=={'i','u'}:
                    reason='unsafe_integer_dtype_promotion'
                elif dtype_a.startswith('float') or dtype_b.startswith('float'):
                    for item in (a,b):
                        for value in item['labels']:
                            budget.tick()
                            if value is not None and value['kind']=='int' and not representable_binary64(Label.from_dict(value)):
                                reason='unsafe_mixed_integer_float'
            if reason:
                result.append({'name':name,'status':'unsupported','reason':reason});continue
        if a is None or b is None:
            keep=a or b
        elif a['dimensions']==b['dimensions']:
            if len(a['labels'])!=len(b['labels']) or any(_key(x)!=_key(y) for x,y in zip(a['labels'],b['labels'])):
                result.append({'name':name,'status':'dropped_conflict','reason':'unequal_aligned_values'});continue
            keep=a
        elif not a['dimensions'] or not b['dimensions']:
            scalar=a if not a['dimensions'] else b;vector=b if scalar is a else a
            if any(_key(scalar['labels'][0])!=_key(x) for x in vector['labels']):
                result.append({'name':name,'status':'dropped_conflict','reason':'unequal_broadcast_values'});continue
            # xarray broadcasts and retains its first variable's storage.
            if a is scalar:
                budget.reserve(len(vector['labels'])*256)
                keep=dict(a,dimensions=vector['dimensions'],labels=a['labels']*len(vector['labels']))
            else:keep=a
        else:
            # Disjoint coordinate dimensions would need a multidimensional result.
            # Check uniform values symbolically, never allocate a Cartesian field.
            budget.reserve((len(a['labels'])+len(b['labels']))*16);all_values=a['labels']+b['labels']
            equal=not a['labels'] or not b['labels'] or all(_key(x)==_key(all_values[0]) for x in all_values)
            if not equal:
                result.append({'name':name,'status':'dropped_conflict','reason':'unequal_broadcast_values'});continue
            result.append({'name':name,'status':'unsupported','reason':'multidimensional_auxiliary_result'});continue
        result.append(dict(name=name,status='preserved',**keep))
    return result


def audit_netcdf(left_path,right_path,left_variable,right_variable,*,join='inner',contract=None,limits=None,budget=None):
    if limits is not None and budget is not None:raise ValidationError('Pass budget or limits, not both')
    budget=Budget(limits) if budget is None else budget
    if type(budget) is not Budget:raise ValidationError('Expected Budget')
    contract=Contract() if contract is None else contract
    left=extract_netcdf(left_path,left_variable,budget=budget)
    right=extract_netcdf(right_path,right_variable,budget=budget)
    report=audit(ArraySnapshot.from_dict(left['snapshot']),ArraySnapshot.from_dict(right['snapshot']),join=join,contract=contract,budget=budget)
    original_blockers=len(report['semantic_blockers'])
    for key in ('units','standard_name','location','crs'):
        a=left['selected_metadata'].get(key);b=right['selected_metadata'].get(key)
        if a is not None and b is not None and a!=b:
            report['semantic_blockers'].append({'code':'selected_'+key+'_mismatch'})
    la={x['name']:x for x in left['auxiliary']};ra={x['name']:x for x in right['auxiliary']}
    for name in la:
        if name not in ra:continue
        left_meta=la[name]['metadata'];right_meta=ra[name]['metadata']
        decoded_times=all((meta.get('time_encoding') or '').startswith('datetime64[') for meta in (left_meta,right_meta))
        for key in ('units','role','location','crs','calendar'):
            a=left_meta.get(key);b=right_meta.get(key)
            if decoded_times and key=='units':continue
            if decoded_times and key=='calendar':a=a or 'standard';b=b or 'standard'
            if a is not None and b is not None and a!=b:
                report['semantic_blockers'].append({'code':'auxiliary_'+key+'_mismatch','coordinate':name})
    if len(report['semantic_blockers'])>original_blockers and report['forecast_status']=='complete':report['contract_status']='unverified'
    aux=auxiliary_facts(left,right,report,budget)
    if any(x['status']=='unsupported' for x in aux):
        report['forecast_status']='unsupported';report['contract_status']='unverified';report['output']=None;report['impact']=None
        for reason in dict.fromkeys(x['reason'] for x in aux if x['status']=='unsupported'):
            if reason=='multidimensional_auxiliary_result':
                report['semantic_blockers'].append({'code':reason})
            else:
                report['semantic_blockers'].extend({'code':'auxiliary_'+reason,'coordinate':x['name']}
                                                  for x in aux if x['status']=='unsupported' and x['reason']==reason)
    if any(x['status']=='dropped_conflict' for x in aux):
        report['diagnostics'].extend({'code':'conflicting_auxiliary_dropped','coordinate':x['name']} for x in aux if x['status']=='dropped_conflict')
        if not contract.allow_drops:
            report['violations'].extend({'code':'dropped_auxiliary_coordinate','coordinate':x['name']} for x in aux if x['status']=='dropped_conflict')
            if report['contract_status']=='pass':report['contract_status']='fail'
    report['resources']=dict(budget.facts(),source_coordinate_bytes=getattr(budget,'source_coordinate_bytes',0),source_labels=getattr(budget,'source_labels',0))
    return {'schema_version':2,'profile':'coordguard-netcdf-v1','left':left,'right':right,'audit':report,'auxiliary_facts':aux,
            'claims':{'payload_inspected':False,'payload_integrity_checked':False,'physical_equivalence':False,
                      'coordinate_digest_scope':'inspected coordinate labels, metadata, decoding settings and runtime versions'}}


def _stable(report):
    # Counters depend on earlier parsing/contract reads sharing the budget.
    # Their values are diagnostics, not a certificate or an RSS assertion.
    report=dict(report)
    if isinstance(report.get('audit'),dict):
        report['audit']=dict(report['audit']);report['audit'].pop('resources',None)
    return report


def _same_json(left,right,budget):
    budget.tick()
    if type(left) is not type(right):return False
    if type(left) is dict:
        return len(left)==len(right) and all(k in right and _same_json(v,right[k],budget) for k,v in left.items())
    if type(left) is list:
        return len(left)==len(right) and all(_same_json(a,b,budget) for a,b in zip(left,right))
    return left==right


def _charge_report(report,budget):
    # Direct Python callers retain their original report while replay is built.
    # The CLI supplies its already-charged JSON parser budget instead.
    pending=[report]
    while pending:
        budget.tick();value=pending.pop()
        if type(value) is dict:
            budget.reserve(512+256*len(value));pending.extend(value.values());pending.extend(value.keys())
        elif type(value) is list:
            budget.reserve(256+128*len(value));pending.extend(value)
        elif type(value) is str:budget.reserve(128+16*len(value))
        elif type(value) in (int,float,bool) or value is None:budget.reserve(128)
        else:raise ValidationError('Report contains a non-JSON object')


def verify_report(report,left_path,right_path,*,limits=None,budget=None):
    """Re-extract both sources, replay, and independently check structural evidence.

Successful verification establishes evidence integrity, not contract success.
The CLI preserves the original audit's nonzero exit when its contract failed.
"""
    try:
        expected={'schema_version','profile','left','right','audit','auxiliary_facts','claims'}
        if type(report) is not dict or set(report)!=expected or report['schema_version']!=2 or report['profile']!='coordguard-netcdf-v1':
            return {'valid':False,'status':'invalid','reason':'invalid_report_schema'}
        if budget is not None and type(budget) is not Budget:raise ValidationError('Expected Budget')
        selected=report['audit'];declared=Limits(**selected['limits']);ceiling=(budget.limits if budget is not None else Limits()) if limits is None else limits
        if type(ceiling) is not Limits:raise ValidationError('Expected Limits')
        if any(getattr(declared,x.name)>getattr(ceiling,x.name) for x in fields(Limits)):
            return {'valid':False,'status':'resource_limited','reason':'report_limits_exceed_verifier_limits'}
        if selected['forecast_status']=='resource_limited':
            return {'valid':False,'status':'resource_limited','reason':'incomplete_producer_report'}
        work=Budget(ceiling)
        if budget is not None:
            work.reserve(budget.working_bytes);work.deadline=min(work.deadline,budget.deadline)
        else:_charge_report(report,work)
        left_name=report['left']['snapshot']['name'];right_name=report['right']['snapshot']['name']
        replay=audit_netcdf(left_path,right_path,left_name,right_name,join=selected['join'],contract=Contract.from_dict(selected['contract']),budget=work)
        if replay['audit']['forecast_status']=='resource_limited':
            return {'valid':False,'status':'resource_limited','reason':'verification_replay_exhausted_budget'}
        # Verification has additional retained-report and independent-check work.
        # Its larger explicit ceiling does not alter the source audit contract.
        replay['audit']['limits']=selected['limits']
        if not _same_json(_stable(report),_stable(replay),work):return {'valid':False,'status':'invalid','reason':'source_or_report_mismatch'}
        from .report_checks import check_operation_facts
        left=ArraySnapshot.from_dict(replay['left']['snapshot']);right=ArraySnapshot.from_dict(replay['right']['snapshot'])
        checked=check_operation_facts(left,right,selected,budget=work)
        if not checked['valid']:
            limited=checked['reason'] in ('deadline','work_limit','working_memory_limit','label_limit','coordinate_byte_limit','edge_limit')
            return {'valid':False,'status':'resource_limited' if limited else 'invalid','reason':'independent_check:'+checked['reason']}
        return {'valid':True,'status':'verified','reason':'source_digests_replay_and_independent_facts_agree',
                'audit_exit_code':exit_code(selected),'contract_status':selected['contract_status'],
                'payload_integrity_checked':False,'resource_counters_verified':False}
    except ResourceLimit as exc:return {'valid':False,'status':'resource_limited','reason':exc.code}
    except (UnsupportedInput,UnsupportedRuntime) as exc:return {'valid':False,'status':'unsupported','reason':exc.code}
    except (ValidationError,KeyError,TypeError,ValueError,IndexError) as exc:
        return {'valid':False,'status':'invalid','reason':'invalid_report_or_source','detail':str(exc)}
