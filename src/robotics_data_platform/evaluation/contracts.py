from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator


class Record(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class Target(Record):
    status: Literal['ready','reject','clarify']
    object_id: str | None=None
    destination_id: str | None=None
    reason_code: str | None=None

    @model_validator(mode='after')
    def shape(self):
        if self.status=='ready':
            if not self.object_id or not self.destination_id or self.reason_code is not None:
                raise ValueError('Ready reference requires object and destination only')
        elif not self.reason_code or self.object_id is not None or self.destination_id is not None:
            raise ValueError('Disposition reference requires a reason only')
        return self


class Case(Record):
    case_id: str=Field(min_length=1,max_length=128)
    category: Literal['supported','ambiguous','invalid','fault','rejection']
    instruction: str=Field(min_length=1,max_length=4096)
    expected: Target
    vlm_applicable: bool=True
    baseline_request: dict | None
    baseline_outcome: str | None=None
    baseline_failure_code: str | None=None
    seeds: list[int]=Field(min_length=1,max_length=10)

    @model_validator(mode='after')
    def consistency(self):
        if len(set(self.seeds))!=len(self.seeds) or any(isinstance(s,bool) or not 0<=s<=4294967295 for s in self.seeds):
            raise ValueError('Unique uint32 seeds required')
        if (self.baseline_request is None)!=(self.baseline_outcome is None):
            raise ValueError('Baseline applicability and expected outcome must agree')
        return self


class TaskSet(Record):
    task_set_version: Literal['1.0.0']='1.0.0'
    name: str=Field(min_length=1,max_length=128)
    split: Literal['development_retrospective','planned_evaluation']
    disclosure: str=Field(min_length=1,max_length=2048)
    cases: list[Case]=Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def unique(self):
        if len({c.case_id for c in self.cases})!=len(self.cases):
            raise ValueError('Duplicate case identities')
        return self


class Binding(Record):
    case_id: str
    seed: int
    mode: Literal['BASELINE_STRUCTURED','VLM_EVALUATION']
    attempt_id: str


class Assignments(Record):
    assignment_version: Literal['1.0.0']='1.0.0'
    bindings: list[Binding]=Field(max_length=2000)
