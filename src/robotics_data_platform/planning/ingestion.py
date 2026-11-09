"""Raw-first data-only receiver for proposal evidence, not robot commands."""
import argparse
import json
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from robotics_data_platform.ingestion.contracts import DataIssue,load_json
from robotics_data_platform.ingestion.storage import RawStore,atomic_write,canonical,receiver_lock,sha,safe_relative
from robotics_data_platform.planning.evidence import validate_evidence


class ProposalReceiver:
    def __init__(self,engine,raw_root,checkpoint=lambda stage:None):
        self.engine,self.store,self.checkpoint=engine,RawStore(raw_root),checkpoint

    def preserve(self,root,identity):
        identity=str(UUID(identity))
        directory=safe_relative(root,'attempts/'+identity)
        receipt={'receipt_version':'proposal-receipt-v1','proposal_id':identity,'intent_object':None,'manifest_object':None,'ready_object':None,'objects':{},'source_issue':None}
        for filename,key,limit in [('attempt.json','intent_object',65536),('manifest.json','manifest_object',1048576),('ready.json','ready_object',65536)]:
            path=safe_relative(directory,filename)
            if not path.exists():
                receipt['source_issue']='CAPTURE_INCOMPLETE'
                continue
            if path.stat().st_size>limit:
                receipt['source_issue']='CAPTURE_LIMIT_EXCEEDED'
                continue
            receipt[key]=self.store.put(path.read_bytes())
        if receipt['manifest_object']:
            try:
                value=load_json(self.store.get(receipt['manifest_object']))
                artifacts=value.get('artifacts',[])
                if not isinstance(artifacts,list) or len(artifacts)>32:
                    raise ValueError('Artifact admission bound')
                total=0
                for item in artifacts:
                    checksum=item['sha256']
                    # Validate hash before using it in a managed path.
                    relative=self.store.object_path(checksum).relative_to(self.store.root).as_posix()
                    path=safe_relative(root,relative)
                    if not path.exists():
                        receipt['objects'][checksum]=None
                        continue
                    size=path.stat().st_size
                    total+=size
                    if size>8388608 or total>33554432:
                        receipt['source_issue']='CAPTURE_LIMIT_EXCEEDED'
                        break
                    receipt['objects'][checksum]=self.store.put(path.read_bytes())
            except DataIssue as error:
                if error.code.startswith('RAW_OBJECT'):
                    raise
                receipt['source_issue']=error.code
            except (ValueError,TypeError,KeyError,AttributeError):
                receipt['source_issue']='MANIFEST_INVALID'
        identity=sha(canonical(receipt))
        path=self.store.root/'proposal_receipts'/(identity+'.json')
        if path.exists() and path.read_bytes()!=canonical(receipt):
            raise DataIssue('RECEIPT_CONFLICT','Immutable receipt differs')
        if not path.exists():
            atomic_write(path,canonical(receipt))
        return identity,receipt

    def read_receipt(self,identity):
        path=self.store.root/'proposal_receipts'/(self.store.object_path(identity).name+'.json')
        data=path.read_bytes()
        if sha(data)!=identity:
            raise DataIssue('RECEIPT_CORRUPT','Proposal receipt checksum mismatch')
        return load_json(data)

    def ingest(self,root,identity):
        with receiver_lock(self.store.root):
            receipt_id,receipt=self.preserve(root,identity)
            self.checkpoint('raw_persisted')
            return self.process(receipt_id,receipt)

    def process(self,identity,receipt):
        try:
            try:
                bundle=validate_evidence(self.store,receipt)
            except DataIssue:
                raise
            except (ValueError,TypeError,KeyError,UnicodeError) as error:
                raise DataIssue('PROPOSAL_CONTRACT_INVALID','Typed inference contract failed') from error
            status=self.project(identity,receipt,bundle)
            self.checkpoint('db_committed')
        except DataIssue as error:
            status='CONFLICTED' if error.code.endswith('CONFLICT') else 'PENDING_REFERENCES' if error.pending else 'QUARANTINED'
            try:
                with self.engine.begin() as connection:
                    self.receipt_row(connection,identity,receipt,status)
                    finding=sha(canonical([identity,error.code]))
                    connection.execute(text('INSERT INTO proposal_findings(finding_id,receipt_sha256,proposal_id,code,message) VALUES (:id,:receipt,:proposal,:code,:message) ON CONFLICT DO NOTHING'),{'id':finding,'receipt':identity,'proposal':receipt['proposal_id'],'code':error.code,'message':str(error)[:512]})
            except SQLAlchemyError:
                status='PENDING_DB'
        except SQLAlchemyError:
            status='PENDING_DB'
        result={'proposal_id':receipt['proposal_id'],'receipt_sha256':identity,'status':status}
        atomic_write(self.store.root/'proposal_statuses'/(identity+'.json'),canonical(result))
        return result

    def receipt_row(self,connection,identity,receipt,status):
        connection.execute(text("INSERT INTO proposal_receipts(receipt_sha256,proposal_id,manifest_sha256,status) VALUES (:id,:proposal,:manifest,:status) ON CONFLICT(receipt_sha256) DO UPDATE SET status=CASE WHEN proposal_receipts.status='INGESTED' THEN 'INGESTED' ELSE excluded.status END"),{'id':identity,'proposal':receipt['proposal_id'],'manifest':receipt['manifest_object'],'status':status})

    def project(self,identity,receipt,bundle):
        manifest=bundle['manifest']
        proposal=manifest['proposal_id']
        with self.engine.begin() as connection:
            connection.execute(text('SELECT pg_advisory_xact_lock(802710)'))
            previous=connection.execute(text('SELECT manifest_sha256 FROM plan_attempts WHERE proposal_id=:id'),{'id':proposal}).scalar_one_or_none()
            if previous and previous!=bundle['fingerprint']:
                raise DataIssue('PROPOSAL_ID_CONFLICT','Proposal identity already binds another finalized snapshot')
            source=manifest['source']
            linked=connection.execute(text('SELECT image_artifact_id FROM observations WHERE episode_id=:episode AND observation_id=:observation'),{'episode':source['episode_id'],'observation':source['observation_id']}).scalar_one_or_none()
            if linked is None:
                raise DataIssue('SOURCE_PROJECTION_MISSING','Original observation must be ingested first',pending=True)
            if str(linked)!=source['image_artifact_id']:
                raise DataIssue('SOURCE_MISMATCH','Source observation/image relation differs')
            collision=connection.execute(text('SELECT proposal_id FROM plan_attempts WHERE episode_id=:episode'),{'episode':manifest['episode_id']}).scalar_one_or_none()
            if (collision and str(collision)!=proposal) or connection.scalar(text('SELECT count(*) FROM episodes WHERE episode_id=:episode'),{'episode':manifest['episode_id']}):
                raise DataIssue('EPISODE_ID_CONFLICT','Inference episode identity already has another binding')
            for item in manifest['artifacts']:
                binding=connection.execute(text('SELECT proposal_id FROM proposal_artifacts WHERE artifact_id=:id'),{'id':item['artifact_id']}).scalar_one_or_none()
                if binding and str(binding)!=proposal:
                    raise DataIssue('ARTIFACT_ID_CONFLICT','Proposal artifact identity already has another owner')
                if connection.scalar(text('SELECT count(*) FROM artifacts WHERE artifact_id=:id'),{'id':item['artifact_id']}):
                    raise DataIssue('ARTIFACT_ID_CONFLICT','Artifact identity collides with source evidence')
            self.receipt_row(connection,identity,receipt,'INGESTED')
            if not previous:
                configuration=bundle['configuration']
                connection.execute(text("""INSERT INTO plan_attempts(proposal_id,episode_id,mode,outcome,execution_eligible,source_episode_id,source_observation_id,source_image_artifact_id,source_image_sha256,receipt_sha256,manifest_sha256,provider_status,model_id,model_revision,manifest,configuration,started_at_utc,finished_at_utc)
                  VALUES (:proposal,:episode,'VLM_EVALUATION','NOT_EXECUTED',false,:source_episode,:observation,:image,:image_sha,:receipt,:sha,:status,:model,:revision,CAST(:manifest AS jsonb),CAST(:config AS jsonb),:started,:finished)"""),{'proposal':proposal,'episode':manifest['episode_id'],'source_episode':source['episode_id'],'observation':source['observation_id'],'image':source['image_artifact_id'],'image_sha':source['image_sha256'],'receipt':identity,'sha':bundle['fingerprint'],'status':manifest['provider_status'],'model':configuration['profile']['model_id'],'revision':configuration['profile']['model_revision'],'manifest':json.dumps(manifest),'config':json.dumps(configuration),'started':manifest['started_at_utc'],'finished':manifest['finished_at_utc']})
                for item in manifest['artifacts']:
                    connection.execute(text('INSERT INTO proposal_artifacts(artifact_id,episode_id,proposal_id,role,sha256,byte_size,media_type,access_class) VALUES (:artifact_id,:episode_id,:proposal,:role,:sha256,:byte_size,:media_type,:access_class)'),{**item,'proposal':proposal})
                if bundle['validation'] is not None:
                    value=bundle['validation']
                    connection.execute(text('INSERT INTO validation_results(proposal_id,validator_version,schema_valid,reference_valid,execution_eligible,reason_codes,parsed_proposal) VALUES (:proposal,:version,:schema,:reference,false,CAST(:reasons AS jsonb),CAST(:parsed AS jsonb))'),{'proposal':proposal,'version':value['validator_version'],'schema':value['schema_valid'],'reference':value['reference_valid'],'reasons':json.dumps(value['reason_codes']),'parsed':json.dumps(value['parsed_proposal'])})
            connection.execute(text("UPDATE proposal_findings SET resolved_by_receipt=:receipt WHERE proposal_id=:proposal AND resolved_by_receipt IS NULL AND code NOT LIKE '%CONFLICT'"),{'receipt':identity,'proposal':proposal})
            return 'DUPLICATE' if previous else 'INGESTED'

    def replay(self):
        with receiver_lock(self.store.root):
            return [self.process(path.stem,self.read_receipt(path.stem)) for path in sorted((self.store.root/'proposal_receipts').glob('*.json'))]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=['ingest','replay'])
    parser.add_argument('--proposal-root',type=Path,default=Path('/proposals'))
    parser.add_argument('--raw-root',type=Path,default=Path('/raw'))
    args=parser.parse_args()
    from robotics_data_platform.settings import Settings
    from robotics_data_platform.database import build_engine
    engine=build_engine(Settings())
    try:
        receiver=ProposalReceiver(engine,args.raw_root)
        values=receiver.replay() if args.operation=='replay' else [receiver.ingest(args.proposal_root,path.name) for path in sorted((args.proposal_root/'attempts').iterdir()) if path.is_dir()]
        print(json.dumps(values,sort_keys=True))
    finally:
        engine.dispose()


if __name__=='__main__':
    main()
