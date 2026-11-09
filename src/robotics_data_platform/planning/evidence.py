"""Typed inference evidence validation; malformed model text is valid evidence."""
import json
import math
import re
from typing import Annotated,Literal
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,UUID4,field_validator
from robotics_data_platform.ingestion.contracts import DataIssue,load_json
from robotics_data_platform.ingestion.storage import RawStore,canonical,sha
from robotics_data_platform.planning.attempts import Vocabulary,build_prompt
from robotics_data_platform.ingestion.validation import validate_receipt
from robotics_data_platform.planning.contracts import validate_response

Hash = Annotated[str,Field(pattern='^[0-9a-f]{64}$')]


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)


class Source(Record):
    episode_id: UUID4
    observation_id: UUID4
    image_artifact_id: UUID4
    image_sha256: Hash
    receipt_sha256: Hash
    source_fingerprint: Hash
    camera_config_artifact_id: UUID4


class Intent(Record):
    attempt_contract_version: Literal['1.0.0']
    proposal_id: UUID4
    episode_id: UUID4
    mode: Literal['VLM_EVALUATION']
    started_at_utc: AwareDatetime
    source: Source
    state: Literal['RUNNING']

    @field_validator('started_at_utc')
    @classmethod
    def utc(cls,value):
        if value.utcoffset().total_seconds() != 0:
            raise ValueError('UTC timestamp required')
        return value


class Artifact(Record):
    artifact_id: UUID4
    episode_id: UUID4
    role: Literal['proposal_image','instruction','public_vocabulary','prompt','provider_config','model_response','proposal_validation','provider_measurements','provider_diagnostic','rendered_prompt','generated_tokens']
    sha256: Hash
    byte_size: int = Field(ge=0,le=8388608)
    media_type: str = Field(max_length=128)
    access_class: Literal['OBSERVATION','MODEL_IO','CONFIGURATION']


class Manifest(Intent):
    state: Literal['FINISHED']
    outcome: Literal['NOT_EXECUTED']
    finished_at_utc: AwareDatetime
    provider_status: Literal['COMPLETED','MODEL_TIMEOUT','MODEL_UNAVAILABLE','MODEL_ERROR','RESPONSE_LIMIT_EXCEEDED']
    execution_eligible: Literal[False]
    validation: dict | None
    artifacts: list[Artifact] = Field(min_length=6,max_length=32)
    provider_metadata: dict

    @field_validator('finished_at_utc')
    @classmethod
    def utc_finish(cls,value):
        return Intent.utc(value)


class Ready(Record):
    proposal_id: UUID4
    manifest_sha256: Hash


def parsed(model,data):
    # Duplicate keys and nonfinite constants must fail before typed normalization.
    value = load_json(data)
    canonical(value)
    return model.model_validate_json(data)


def validate_evidence(store,receipt):
    if receipt['source_issue']:
        raise DataIssue(receipt['source_issue'],'Incomplete or out-of-bounds inference evidence',pending=receipt['source_issue']=='CAPTURE_INCOMPLETE')
    manifest_bytes = store.get(receipt['manifest_object'])
    manifest = parsed(Manifest,manifest_bytes)
    ready = parsed(Ready,store.get(receipt['ready_object']))
    intent = parsed(Intent,store.get(receipt['intent_object']))
    identity = str(manifest.proposal_id)
    if identity != receipt['proposal_id'] or ready.proposal_id != manifest.proposal_id or ready.manifest_sha256 != sha(manifest_bytes):
        raise DataIssue('MANIFEST_MISMATCH','Ready identity/checksum differs')
    expected_intent = manifest.model_dump(mode='json',include=set(Intent.model_fields))
    expected_intent['state'] = 'RUNNING'
    if intent.model_dump(mode='json') != expected_intent or manifest.finished_at_utc < manifest.started_at_utc:
        raise DataIssue('CLOSURE_INVALID','Attempt intent/terminal closure differs')
    if manifest.episode_id == manifest.source.episode_id:
        raise DataIssue('MODE_INVALID','Inference must have a separate episode identity')
    roles,ids = {},set()
    for item in manifest.artifacts:
        key = str(item.artifact_id)
        if item.role in roles or key in ids or item.episode_id != manifest.episode_id:
            raise DataIssue('ARTIFACT_BINDING_INVALID','Duplicate or cross-episode artifact binding')
        ids.add(key)
        actual = receipt['objects'].get(item.sha256)
        if actual is None:
            raise DataIssue('ARTIFACT_MISSING','Attempt artifact unavailable',item.sha256,pending=True)
        data = store.get(actual)
        if sha(data) != item.sha256 or len(data) != item.byte_size:
            raise DataIssue('ARTIFACT_HASH_MISMATCH','Attempt artifact checksum/size differs',item.sha256)
        required_access = 'OBSERVATION' if item.role=='proposal_image' else 'CONFIGURATION' if item.role in {'public_vocabulary','provider_config'} else 'MODEL_IO'
        required_media = 'image/png' if item.role=='proposal_image' else 'text/plain; charset=utf-8' if item.role in {'instruction','prompt','model_response','provider_diagnostic'} else 'application/json'
        maximum = 8388608 if item.role=='proposal_image' else 65536 if item.role=='provider_diagnostic' else 1048576
        if item.access_class != required_access or item.media_type != required_media or len(data)>maximum:
            raise DataIssue('ARTIFACT_CONTRACT_INVALID','Artifact role/media/access/size differs')
        roles[item.role] = data
    required = {'proposal_image','instruction','public_vocabulary','prompt','provider_config','provider_measurements'}
    if not required <= roles.keys():
        raise DataIssue('ARTIFACT_MISSING','Required attempt artifact absent',pending=True)
    instruction = roles['instruction'].decode('utf-8')
    if not instruction.strip() or len(roles['instruction'])>4096:
        raise DataIssue('INSTRUCTION_INVALID','Instruction admission bound differs')
    vocabulary = parsed(Vocabulary,roles['public_vocabulary'])
    if roles['prompt'] != build_prompt(instruction,vocabulary).encode('utf-8'):
        raise DataIssue('PROMPT_MISMATCH','Prompt differs from declared v1 inputs')
    config = load_json(roles['provider_config'])
    if set(config) != {'profile','adapter_revision','prompt_revision','contract_version','timeout_seconds','code_sha256'} or (config['adapter_revision'],config['prompt_revision'],config['contract_version']) != ('proposal-adapter-v1','proposal-prompt-v1','1.0.0'):
        raise DataIssue('UNSUPPORTED_SCHEMA','Unsupported provider configuration revision')
    canonical(config)
    if not isinstance(config['profile'],dict) or not all(isinstance(config['profile'].get(k),str) and config['profile'][k] for k in ['provider','model_id','model_revision']):
        raise DataIssue('PROVIDER_CONFIG_INVALID','Missing model provenance')
    if not 0 < config['timeout_seconds'] <= 180 or len(config['profile']['model_revision']) != 40:
        raise DataIssue('PROVIDER_CONFIG_INVALID','Invalid model revision or deadline')
    if not re.fullmatch('[0-9a-f]{40}',config['profile']['model_revision']) or not isinstance(config['code_sha256'],dict) or not config['code_sha256'] or any(not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value) for value in config['code_sha256'].values()):
        raise DataIssue('PROVIDER_CONFIG_INVALID','Invalid provenance checksums')
    if load_json(roles['provider_measurements']) != manifest.provider_metadata:
        raise DataIssue('MEASUREMENT_MISMATCH','Provider measurement artifact differs')
    canonical(manifest.provider_metadata)
    worker=manifest.provider_metadata.get('worker',{})
    if not isinstance(worker,dict) or not isinstance(worker.get('timings_ms',{}),dict):
        raise DataIssue('MEASUREMENT_INVALID','Worker timing metadata must be an object')
    timing_values=list(worker.get('timings_ms',{}).values())
    if 'wall_ms' in manifest.provider_metadata:
        timing_values.append(manifest.provider_metadata['wall_ms'])
    if any(isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or value<0 for value in timing_values):
        raise DataIssue('MEASUREMENT_INVALID','Durations must be finite and nonnegative')
    validation = None
    if manifest.provider_status == 'COMPLETED':
        if not {'model_response','proposal_validation'} <= roles.keys():
            raise DataIssue('ARTIFACT_MISSING','Completed inference lacks response/validation',pending=True)
        validation = validate_response(roles['model_response'],object_ids=frozenset(h.id for h in vocabulary.objects),destination_ids=frozenset(h.id for h in vocabulary.destinations)).model_dump(mode='json')
        if validation != manifest.validation or validation != load_json(roles['proposal_validation']):
            raise DataIssue('VALIDATION_MISMATCH','Recorded validation differs from independent validation')
    elif manifest.validation is not None or {'model_response','proposal_validation'} & roles.keys():
        raise DataIssue('PROVIDER_STATUS_INVALID','Failed provider must not claim completed response/validation')
    try:
        original_receipt=store.read_receipt(store.root/'receipts'/(manifest.source.receipt_sha256+'.json'))
        original=validate_receipt(store,original_receipt)
        observation=next(event['payload'] for event in original['observations'] if event['payload']['observation_id']==str(manifest.source.observation_id))
        original_image=next(item for item in original['artifacts'] if item['artifact_id']==observation['image_artifact_id'])
        image=store.get(original_image['sha256'])
        lineage={'episode_id':original['summary']['episode_id'],'observation_id':observation['observation_id'],'image_artifact_id':original_image['artifact_id'],'image_sha256':original_image['sha256'],'receipt_sha256':manifest.source.receipt_sha256,'source_fingerprint':original['fingerprint'],'camera_config_artifact_id':observation['camera_config_artifact_id']}
    except (OSError,ValueError,StopIteration) as error:
        raise DataIssue('SOURCE_UNAVAILABLE','Source observation cannot be revalidated',pending=True) from error
    if lineage != manifest.source.model_dump(mode='json') or image != roles['proposal_image']:
        raise DataIssue('SOURCE_MISMATCH','Original observation lineage or image differs')
    return {'manifest':manifest.model_dump(mode='json'),'configuration':config,'validation':validation,'fingerprint':sha(manifest_bytes)}
