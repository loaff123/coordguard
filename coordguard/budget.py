"""Conservative accounting, never an operating-system RSS guarantee."""
from dataclasses import dataclass, fields
import math
import time
from .errors import ValidationError, ResourceLimit


@dataclass(frozen=True)
class Limits:
    max_labels: int = 1_000_000
    max_edges: int = 2_000_000
    max_coordinate_bytes: int = 64 * 1024 * 1024
    max_working_bytes: int = 512 * 1024 * 1024
    max_input_bytes: int = 64 * 1024 * 1024
    max_steps: int = 50_000_000
    deadline_seconds: float = 30.0

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self,field.name)
            if field.name == 'deadline_seconds':
                try:valid=type(value) in (int,float) and math.isfinite(value) and value>=0
                except OverflowError:valid=False
                if not valid:raise ValidationError('deadline_seconds must be finite and nonnegative')
            elif type(value) is not int or value < 0 or value > 2**63-1:
                raise ValidationError(field.name + ' must be a bounded nonnegative integer')

    def to_dict(self):
        return {field.name:getattr(self,field.name) for field in fields(self)}


class Budget:
    def __init__(self, limits=None):
        self.limits = Limits() if limits is None else limits
        if type(self.limits) is not Limits: raise ValidationError('Expected Limits')
        self.deadline = time.monotonic() + self.limits.deadline_seconds
        self.labels = self.coordinate_bytes = self.edges = self.steps = 0
        self.working_bytes = 0
        self._inspected = set()
        self.reserve(1024*1024)

    def check(self):
        if time.monotonic() >= self.deadline:
            raise ResourceLimit('deadline', 'Cooperative monotonic deadline reached')

    def tick(self, count=1):
        self.steps += count
        if self.steps > self.limits.max_steps:
            raise ResourceLimit('work_limit','Deterministic graph/planner work limit reached')
        self.check()

    def reserve(self, size):
        if self.working_bytes + size > self.limits.max_working_bytes:
            raise ResourceLimit('working_memory_limit','Conservative working allocation estimate exceeded')
        self.working_bytes += size

    def add_label(self, label):
        self.check()
        if self.labels + 1 > self.limits.max_labels:
            raise ResourceLimit('label_limit','Total inspected label limit exceeded')
        size = label.storage_bytes
        if self.coordinate_bytes + size > self.limits.max_coordinate_bytes:
            raise ResourceLimit('coordinate_byte_limit','Logical coordinate storage limit exceeded')
        self.reserve(2048 + 16*size)
        self.labels += 1; self.coordinate_bytes += size

    def add_edge(self):
        self.tick()
        if self.edges + 1 > self.limits.max_edges:
            raise ResourceLimit('edge_limit','Complete candidate graph exceeds edge limit')
        self.reserve(128)
        self.edges += 1

    def inspect_axis(self, axis, slot=None):
        self.check()
        token = None if slot is None else (slot, id(axis))
        if token is not None and token in self._inspected: return
        text_bytes=len(axis.name.encode('utf-8'))+sum(len(value.encode('utf-8')) for value in axis.metadata.to_dict().values() if value is not None)
        self.reserve(65536+32*text_bytes)
        if axis.labels is not None:
            for label in axis.labels: self.add_label(label)
        if token is not None: self._inspected.add(token)

    def facts(self):
        return {'labels':self.labels,'coordinate_bytes':self.coordinate_bytes,'candidate_edges':self.edges,
                'estimated_working_bytes':self.working_bytes,'steps':self.steps}
