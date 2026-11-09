from pathlib import Path
from robotics_data_platform.ingestion.worker import Receiver
from robotics_data_platform.planning.adapter import PROFILE,ProviderResult
from robotics_data_platform.planning.attempts import Vocabulary,capture_attempt,observation_source
from robotics_data_platform.ingestion.storage import RawStore,canonical,sha
from robotics_data_platform.ingestion.validation import validate_receipt
from ingestion_helpers import success_bundle


class Provider:
    profile={**PROFILE,'provider':'test-fixture-not-model-inference'}
    def __init__(self,status='COMPLETED',response=b'not valid model JSON'):
        self.status,self.response=status,response
    def infer(self,*args):
        return ProviderResult(self.status,self.response,{'wall_ms':1,'worker':{'timings_ms':{'generation':.5}}})


def make_attempt(engine,root,status='COMPLETED',response=b'not valid model JSON',project_source=True):
    raw=root/'raw'
    capture=success_bundle(root/'capture')
    receiver=Receiver(engine,raw)
    if project_source:
        receiver.ingest(capture)
    else:
        receiver.store.preserve(capture)
    _,receipt=receiver.store.preserve(capture)
    bundle=validate_receipt(receiver.store,receipt)
    image,lineage=observation_source(raw,capture.name,bundle['observations'][0]['payload']['observation_id'])
    vocabulary=Vocabulary.model_validate({'version':'test-v1','objects':[{'id':'package_01','description':'red package'}],'destinations':[{'id':'zone_a','description':'zone A'}]})
    manifest=capture_attempt(root/'proposals',image,lineage,'Move the red package to zone A',vocabulary,Provider(status,response))
    return manifest,capture


def rewrite(root,manifest):
    directory=root/'proposals'/'attempts'/manifest['proposal_id']
    data=canonical(manifest)
    (directory/'manifest.json').write_bytes(data)
    (directory/'ready.json').write_bytes(canonical({'proposal_id':manifest['proposal_id'],'manifest_sha256':sha(data)}))
