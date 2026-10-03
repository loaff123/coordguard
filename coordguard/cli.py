"""Local regular-file JSON interface; no adapters, network or executable inputs."""
import argparse
from dataclasses import replace
import json
import math
import os
import stat
from .model import ArraySnapshot, Contract, Label, ValidationError, canonical_json
from .budget import Budget, Limits, ResourceLimit
from .matching import correspond
from .planner import audit, exit_code
from .verify import verify_correspondence


def _object(pairs):
    if len(pairs)>64:raise ValidationError('JSON object exceeds 64 keys')
    result={}
    for key,value in pairs:
        if key in result:raise ValidationError('Duplicate JSON object key')
        result[key]=value
    return result


def _integer(text):
    if len(text)>2048:raise ValidationError('JSON integer exceeds 2048 characters')
    return int(text)


def _float(text):
    if len(text)>128:raise ValidationError('JSON number token exceeds 128 characters')
    value=float(text)
    if not math.isfinite(value):raise ValidationError('Bare nonfinite JSON numbers are forbidden')
    return value


def _constant(text):
    raise ValidationError('Nonstandard JSON constant: '+text)


def _depth(text,budget):
    depth=0;quoted=False;escape=False;scalar=False
    for i,char in enumerate(text):
        if i%4096==0:budget.tick(min(4096,len(text)-i))
        if quoted:
            if escape:escape=False
            elif char=='\\':escape=True
            elif char=='"':quoted=False
        elif char=='"':
            budget.reserve(128);quoted=True;scalar=False
        elif char in '[{':
            budget.reserve(512 if char=='{' else 256);scalar=False
            depth+=1
            if depth>32:raise ValidationError('JSON nesting exceeds 32 levels')
        elif char in ']}':
            scalar=False;depth-=1
            if depth<0:raise ValidationError('Unbalanced JSON nesting')
        elif char in ' \t\r\n,:':scalar=False
        elif not scalar:
            budget.reserve(128);scalar=True


def read_json(path,budget):
    """Bound serialized input before stdlib parsing; reject links and devices.

All input bytes across this invocation count toward max_input_bytes. Reserve
16 times wire bytes for decoding/parser/model transients before reading them.
    """
    if type(path) is not str or '://' in path:raise ValidationError('Only local regular file paths are admitted')
    try:
        flags=os.O_RDONLY | getattr(os,'O_NONBLOCK',0) | getattr(os,'O_NOFOLLOW',0)
        descriptor=os.open(path,flags)
        with os.fdopen(descriptor,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):raise ValidationError('Input is not a local regular file')
            used=getattr(budget,'input_bytes',0)
            remaining=budget.limits.max_input_bytes-used
            if info.st_size>remaining:raise ResourceLimit('input_byte_limit','Combined serialized input exceeds cap')
            budget.reserve(16*info.st_size)
            raw=stream.read(info.st_size+1)
            if len(raw)!=info.st_size:raise ValidationError('Input changed while being read')
            budget.input_bytes=used+len(raw)
    except OSError as exc:
        raise ValidationError('Cannot open local regular input: '+str(exc)) from exc
    try:text=raw.decode('utf-8')
    except UnicodeError as exc:raise ValidationError('Input must be UTF-8 JSON') from exc
    _depth(text,budget)
    try:
        result=json.loads(text,object_pairs_hook=_object,parse_int=_integer,parse_float=_float,parse_constant=_constant)
    except (json.JSONDecodeError,RecursionError) as exc:
        raise ValidationError('Malformed JSON input') from exc
    budget.check()
    return result


def _axis(snapshot,name):
    for axis in snapshot.axes:
        if axis.name==name:return axis
    raise ValidationError('Requested dimension is absent from snapshot: '+name)


def _parser():
    parser=argparse.ArgumentParser(prog='coordguard',description='Bounded offline coordinate-only audits; no payload reads or repairs')
    commands=parser.add_subparsers(dest='command',required=True)
    audit_parser=commands.add_parser('audit',help='Forecast the declared symbolic binary join')
    audit_parser.add_argument('left');audit_parser.add_argument('right')
    audit_parser.add_argument('--join',choices=('inner','outer','left','right','exact'),default='inner')
    audit_parser.add_argument('--contract')
    match_parser=commands.add_parser('match',help='Prove only a discrete numeric absolute-tolerance relation')
    match_parser.add_argument('left');match_parser.add_argument('right')
    match_parser.add_argument('--axis',required=True);match_parser.add_argument('--atol',required=True)
    verify_parser=commands.add_parser('verify',help='Independently verify a standalone correspondence certificate')
    verify_parser.add_argument('certificate');verify_parser.add_argument('--left',required=True)
    verify_parser.add_argument('--right',required=True);verify_parser.add_argument('--axis',required=True)
    nc_parser=commands.add_parser('audit-nc',help='Read coordinate-only NetCDF4 inputs using the pinned optional runtime')
    nc_parser.add_argument('left');nc_parser.add_argument('right')
    nc_parser.add_argument('--left-variable',required=True);nc_parser.add_argument('--right-variable',required=True)
    nc_parser.add_argument('--join',choices=('inner','outer','left','right','exact'),default='inner')
    nc_parser.add_argument('--contract');nc_parser.add_argument('--html',help='Create a new standalone HTML report; existing files are never overwritten')
    replay_parser=commands.add_parser('verify-report',help='Re-extract NetCDF sources and independently verify a complete report')
    replay_parser.add_argument('report');replay_parser.add_argument('--left',required=True);replay_parser.add_argument('--right',required=True)
    for sub in (audit_parser,match_parser,verify_parser,nc_parser,replay_parser):
        for name in ('labels','edges','coordinate-bytes','working-bytes','input-bytes','steps'):
            sub.add_argument('--max-'+name,type=int)
        sub.add_argument('--deadline',type=float)
    return parser


def main(argv=None):
    args=_parser().parse_args(argv)
    try:
        changes={}
        for name in ('labels','edges','coordinate_bytes','working_bytes','input_bytes','steps'):
            value=getattr(args,'max_'+name)
            if value is not None:changes['max_'+name]=value
        if args.deadline is not None:changes['deadline_seconds']=args.deadline
        limits=replace(Limits(),**changes);reader=Budget(limits)
        if args.command in ('audit-nc','verify-report'):
            from .report import audit_netcdf,verify_report
            if args.command=='audit-nc':
                contract=Contract.from_dict(read_json(args.contract,reader)) if args.contract else Contract()
                result=audit_netcdf(args.left,args.right,args.left_variable,args.right_variable,join=args.join,contract=contract,budget=reader)
                code=exit_code(result['audit'])
                if args.html:
                    from .html_report import render_html
                    html=render_html(result)
                    try:
                        fd=os.open(args.html,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                        with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(html)
                    except OSError as exc:raise ValidationError('Cannot create new HTML output: '+str(exc)) from exc
            else:
                result=verify_report(read_json(args.report,reader),args.left,args.right,limits=limits,budget=reader)
                code=result['audit_exit_code'] if result['valid'] else 3 if result['status'] in ('resource_limited','unsupported') else 1
            print(canonical_json(result));return code
        left=ArraySnapshot.from_dict(read_json(args.left,reader));right=ArraySnapshot.from_dict(read_json(args.right,reader))
        if args.command=='audit':
            contract=Contract.from_dict(read_json(args.contract,reader)) if args.contract else Contract()
            result=audit(left,right,join=args.join,contract=contract,budget=reader);code=exit_code(result)
        elif args.command=='match':
            if len(args.atol)>128:raise ValidationError('Tolerance token too long')
            try:tolerance=Label.from_value(float(args.atol))
            except (ValueError,OverflowError) as exc:raise ValidationError('Invalid tolerance text') from exc
            result=correspond(_axis(left,args.axis),_axis(right,args.axis),tolerance,budget=reader)
            code=0 if result['status']=='unique_within_tolerance' else 3 if result['status'] in ('unsupported','resource_limited') else 1
        else:
            certificate=read_json(args.certificate,reader)
            result=verify_correspondence(_axis(left,args.axis),_axis(right,args.axis),certificate,budget=reader)
            code=0 if result['valid'] else 3 if result['status'] in ('unsupported','resource_limited') else 1
    except (ValidationError,ResourceLimit) as exc:
        limited=isinstance(exc,ResourceLimit)
        unsupported=exc.code in ('unsupported_input','unsupported_runtime')
        result={'schema_version':1,'error':{'code':exc.code,'message':str(exc)}}
        if limited:result.update(forecast_status='resource_limited',contract_status='unverified')
        if unsupported:result.update(forecast_status='unsupported',contract_status='unverified')
        code=3 if limited or unsupported else 2
    print(canonical_json(result))
    return code
