"""Strict model-output parsing and reference checks, never execution authority."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from robotics_data_platform.ingestion.contracts import load_json

CONTRACT_VERSION = "1.0.0"
MAX_RESPONSE_BYTES = 1048576


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid",strict=True,allow_inf_nan=False,frozen=True)


class Ready(Record):
    status: Literal["ready"]
    object_id: str = Field(min_length=1,max_length=64,pattern=r"^[a-z][a-z0-9_]*$")
    destination_id: str = Field(min_length=1,max_length=64,pattern=r"^[a-z][a-z0-9_]*$")


class Reject(Record):
    status: Literal["reject"]
    reason_code: Literal["OBJECT_UNAVAILABLE","DESTINATION_INVALID","UNSUPPORTED_INSTRUCTION"]


class Clarify(Record):
    status: Literal["clarify"]
    reason_code: Literal["OBJECT_AMBIGUOUS","DESTINATION_AMBIGUOUS","INSTRUCTION_AMBIGUOUS"]


Proposal = Annotated[Ready | Reject | Clarify,Field(discriminator="status")]
PROPOSAL_ADAPTER = TypeAdapter(Proposal)


class ValidationResult(Record):
    validator_version: Literal["proposal-validator-v1"] = "proposal-validator-v1"
    schema_valid: bool
    reference_valid: bool | None
    execution_eligible: Literal[False] = False
    reason_codes: tuple[str,...]
    parsed_proposal: Ready | Reject | Clarify | None


def validate_response(raw:bytes,*,object_ids:frozenset[str],destination_ids:frozenset[str]) -> ValidationResult:
    """Check JSON shape and membership in a supplied scene vocabulary.

    Membership does not prove visual grounding or the correctness of a rejection.
    The caller retains original response bytes and records provider failures separately.
    """
    if len(raw) > MAX_RESPONSE_BYTES:
        return ValidationResult(schema_valid=False,reference_valid=None,reason_codes=("RESPONSE_LIMIT_EXCEEDED","EXPERIMENTAL_MODE"),parsed_proposal=None)
    try:
        parsed = PROPOSAL_ADAPTER.validate_python(load_json(raw.decode('utf-8')))
    except (ValueError,UnicodeError,ValidationError,RecursionError):
        return ValidationResult(schema_valid=False,reference_valid=None,reason_codes=("PLAN_SCHEMA_INVALID","EXPERIMENTAL_MODE"),parsed_proposal=None)
    reasons = []
    references = None
    if isinstance(parsed,Ready):
        if parsed.object_id not in object_ids:
            reasons.append("OBJECT_REFERENCE_UNKNOWN")
        if parsed.destination_id not in destination_ids:
            reasons.append("DESTINATION_REFERENCE_UNKNOWN")
        references = not reasons
    return ValidationResult(schema_valid=True,reference_valid=references,reason_codes=tuple([*reasons,"EXPERIMENTAL_MODE"]),parsed_proposal=parsed)
