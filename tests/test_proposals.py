"""Adversarial output acceptance boundaries; no inference or robot execution."""
import pytest
from robotics_data_platform.planning.contracts import MAX_RESPONSE_BYTES,validate_response


def validate(raw):
    return validate_response(raw,object_ids=frozenset({'package_01'}),destination_ids=frozenset({'zone_a'}))


def test_ready_is_structurally_valid_but_never_executable():
    value = validate(b'{"status":"ready","object_id":"package_01","destination_id":"zone_a"}')
    assert value.schema_valid and value.reference_valid
    assert not value.execution_eligible
    assert value.reason_codes == ('EXPERIMENTAL_MODE',)


def test_hallucinated_handles_are_distinct_from_bad_json():
    value = validate(b'{"status":"ready","object_id":"package_99","destination_id":"zone_z"}')
    assert value.schema_valid and value.reference_valid is False
    assert value.reason_codes == ('OBJECT_REFERENCE_UNKNOWN','DESTINATION_REFERENCE_UNKNOWN','EXPERIMENTAL_MODE')


@pytest.mark.parametrize('raw',[
    b'{"status":"ready","object_id":"package_01"}',
    b'{"status":"ready","object_id":1,"destination_id":"zone_a"}',
    b'{"status":"ready","object_id":"package_01","destination_id":"zone_a","code":"run()"}',
    b'{"status":"reject","reason_code":"MODEL_TIMEOUT"}',
    b'{"status":"clarify","reason_code":"OBJECT_UNAVAILABLE"}',
    b'{"status":"ready","status":"reject","reason_code":"OBJECT_UNAVAILABLE"}',
    b'```json\n{"status":"reject","reason_code":"OBJECT_UNAVAILABLE"}\n```',
    b'[{"status":"reject","reason_code":"OBJECT_UNAVAILABLE"}]',
    b'{"status":"reject","reason_code":"OBJECT_UNAVAILABLE","confidence":NaN}',
    b'\xff',
])
def test_unsafe_or_noncontract_outputs_are_retained_as_parse_failures(raw):
    value = validate(raw)
    assert not value.schema_valid and value.reference_valid is None
    assert value.parsed_proposal is None and not value.execution_eligible
    assert 'PLAN_SCHEMA_INVALID' in value.reason_codes


@pytest.mark.parametrize('status,reason',[('reject','OBJECT_UNAVAILABLE'),('reject','UNSUPPORTED_INSTRUCTION'),('clarify','OBJECT_AMBIGUOUS'),('clarify','DESTINATION_AMBIGUOUS')])
def test_dispositions_do_not_claim_reference_or_semantic_correctness(status,reason):
    import json
    value = validate(json.dumps({'status':status,'reason_code':reason}).encode())
    assert value.schema_valid and value.reference_valid is None
    assert not value.execution_eligible


def test_bounded_parser():
    value = validate(b' '*(MAX_RESPONSE_BYTES+1))
    assert value.reason_codes[0] == 'RESPONSE_LIMIT_EXCEEDED'
