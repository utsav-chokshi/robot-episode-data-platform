"""Pinned model preparation and offline byte verification; no application imports."""
import hashlib
import json
from pathlib import Path


def sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


def prepare(root:Path,profile):
    from huggingface_hub import snapshot_download
    snapshot = Path(snapshot_download(profile['model_id'],revision=profile['model_revision'],cache_dir=str(root/'cache'/'hub'),allow_patterns=['*.json','*.safetensors','*.txt','*.model','*.jinja','README.md','LICENSE*']))
    value = {'model_id':profile['model_id'],'model_revision':profile['model_revision'],'files':{p.relative_to(snapshot).as_posix():sha(p) for p in sorted(snapshot.rglob('*')) if p.is_file()}}
    data = json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    path = root/'model-files.json'
    if path.exists() and path.read_bytes() != data:
        raise ValueError('Prepared model manifest conflict')
    path.write_bytes(data)
    return value


def verified_snapshot(root,profile):
    path = root/'model-files.json'
    value = json.loads(path.read_bytes())
    if value['model_id'] != profile['model_id'] or value['model_revision'] != profile['model_revision']:
        raise ValueError('Pinned model revision differs from prepared cache')
    snapshot = root/'cache'/'hub'/('models--'+profile['model_id'].replace('/','--'))/'snapshots'/profile['model_revision']
    if not value['files'] or not any(name.endswith('.safetensors') for name in value['files']):
        raise ValueError('Prepared model weights missing')
    for name,checksum in value['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or not (snapshot/relative).resolve().is_relative_to(root.resolve()):
            raise ValueError('Invalid model cache reference')
        if sha(snapshot/relative) != checksum:
            raise ValueError('Model cache checksum mismatch')
    return str(snapshot),sha(path)
