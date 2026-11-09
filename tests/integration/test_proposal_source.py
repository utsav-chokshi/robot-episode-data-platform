import json
import pytest
from robotics_data_platform.ingestion.storage import RawStore
from robotics_data_platform.planning.attempts import observation_source
from ingestion_helpers import success_bundle


def test_source_observation_is_loaded_from_verified_raw_evidence(tmp_path):
    source = success_bundle(tmp_path/'capture')
    store = RawStore(tmp_path/'raw')
    _,receipt = store.preserve(source)
    from robotics_data_platform.ingestion.validation import validate_receipt
    bundle = validate_receipt(store,receipt)
    observation_id = bundle['observations'][0]['payload']['observation_id']
    image,lineage = observation_source(store.root,source.name,observation_id)
    assert image.startswith(b'\x89PNG') and lineage['source_fingerprint'] == bundle['fingerprint']
    store.object_path(lineage['image_sha256']).write_bytes(b'corrupted')
    with pytest.raises(ValueError,match='checksum'):
        observation_source(store.root,source.name,observation_id)
