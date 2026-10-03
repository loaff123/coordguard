"""Run from source checkout with python -m examples.generate."""
from pathlib import Path
from coordguard import ArraySnapshot, Axis, Contract, Label
from coordguard.model import canonical_json


def main():
    root=Path(__file__).parent
    fixtures={
        'near-left.json':ArraySnapshot('temperature',(Axis.from_values('x',[0.,1.,2.]),)),
        'near-right.json':ArraySnapshot('temperature',(Axis.from_values('x',[0.,1.000000001,2.]),)),
        'broadcast-right.json':ArraySnapshot('temperature',(Axis.from_values('longitude',[0.,1.,2.]),)),
        'ambiguous-left.json':ArraySnapshot('temperature',(Axis.from_values('x',[0.,.1]),)),
        'ambiguous-right.json':ArraySnapshot('temperature',(Axis.from_values('x',[.04,.06]),)),
        'tolerance-contract.json':Contract(tolerances=(('x',Label.from_value(1e-8)),)),
    }
    for name,value in fixtures.items():
        (root/name).write_text(canonical_json(value.to_dict())+'\n',encoding='utf-8')

if __name__=='__main__':main()
