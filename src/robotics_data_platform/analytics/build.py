"""Crash-recoverable immutable publication; replay transforms data only."""
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4
import duckdb
import pyarrow
from sqlalchemy import text
from robotics_data_platform.analytics.files import digest,file_hash,immutable_json,load_input,verify_published
from robotics_data_platform.analytics.snapshot import verify_frozen_evidence
from robotics_data_platform.analytics.transform import materialize
from robotics_data_platform.ingestion.storage import receiver_lock


def fingerprint(identity):
    code={name:file_hash(Path(__file__).with_name(name)) for name in ['transform.py','baseline_v1.py','proposal_transform.py','build.py']}
    return digest({'input':identity,'code':code,'arrow':pyarrow.__version__,'duckdb':duckdb.__version__,'contract':'1.1.0'})


def register(engine,manifest,path):
    with engine.begin() as connection:
        connection.execute(text('SELECT pg_advisory_xact_lock(802709)'))
        previous = connection.execute(text('SELECT manifest_sha256 FROM dataset_versions WHERE build_fingerprint=:fp'),{'fp':manifest['build_fingerprint']}).scalar_one_or_none()
        checksum = file_hash(path)
        if previous:
            if previous != checksum:
                raise ValueError('Dataset registry conflicts with immutable publication')
            return
        connection.execute(text('INSERT INTO dataset_versions(dataset_id,build_fingerprint,input_sha256,manifest_sha256,manifest) VALUES (:id,:fp,:input,:sha,CAST(:manifest AS jsonb))'),{'id':manifest['dataset_id'],'fp':manifest['build_fingerprint'],'input':manifest['input_sha256'],'sha':checksum,'manifest':json.dumps(manifest)})
        snapshot = load_input(path.parents[2],manifest['input_sha256'])
        for member in snapshot['members']:
            connection.execute(text('INSERT INTO dataset_members(dataset_id,episode_id,seal_id,eligible) VALUES (:dataset,:episode,:seal,:eligible)'),{'dataset':manifest['dataset_id'],'episode':member['catalog']['episode_id'],'seal':member['seal']['seal_id'],'eligible':member['seal']['eligible']})
        for receipt in snapshot.get('proposals',{}).get('receipts',[]):
            connection.execute(text('INSERT INTO dataset_proposal_receipts(dataset_id,receipt_sha256) VALUES (:dataset,:receipt)'),{'dataset':manifest['dataset_id'],'receipt':receipt['receipt_sha256']})


def publish(engine,raw_root,root,identity,checkpoint=lambda name:None):
    snapshot = load_input(root,identity)
    verify_frozen_evidence(snapshot,raw_root)
    fp = fingerprint(identity)
    root.mkdir(parents=True,exist_ok=True)
    with receiver_lock(root):
        destination = root/'versions'/fp
        with engine.connect() as connection:
            registered = connection.execute(text('SELECT manifest_sha256 FROM dataset_versions WHERE build_fingerprint=:fp'),{'fp':fp}).scalar_one_or_none()
        if registered and not destination.exists():
            raise ValueError('Registered dataset files are missing')
        if destination.exists():
            manifest = verify_published(root,fp,registered)
        else:
            (root/'staging').mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(dir=root/'staging') as temporary:
                staging = Path(temporary)
                tables,metrics = materialize(snapshot,staging)
                manifest = {'contract_version':'1.1.0' if 'proposals' in snapshot else '1.0.0','dataset_id':str(uuid4()),'build_fingerprint':fp,'input_sha256':identity,'tables':tables,'metrics':metrics}
                immutable_json(staging/'manifest.json',manifest)
                for path in staging.iterdir():
                    with path.open('rb') as handle:
                        os.fsync(handle.fileno())
                checkpoint('staged')
                destination.parent.mkdir(exist_ok=True)
                os.rename(staging,destination)
                descriptor = os.open(destination.parent,os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                checkpoint('promoted')
        register(engine,manifest,destination/'manifest.json')
        checkpoint('registered')
        return manifest


def rebuild(raw_root,root,fp):
    manifest = verify_published(root,fp)
    snapshot = load_input(root,manifest['input_sha256'])
    verify_frozen_evidence(snapshot,raw_root)
    with tempfile.TemporaryDirectory() as temporary:
        tables,metrics = materialize(snapshot,Path(temporary))
    matches = {name:table['logical_sha256']==manifest['tables'][name]['logical_sha256'] for name,table in tables.items()}
    if not all(matches.values()) or metrics != manifest['metrics']:
        raise ValueError('Frozen-input rebuild differs from published dataset')
    return {'dataset_id':manifest['dataset_id'],'build_fingerprint':fp,'logical_tables_match':matches,'metrics_match':True}
