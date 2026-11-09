"""Read-only inference attempt and receipt inspection."""
import hashlib
from uuid import UUID
from fastapi import APIRouter,HTTPException,Query,Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


def router(engine,raw_root):
    routes=APIRouter(prefix='/api/v1',tags=['experimental proposals'])
    def rows(sql,parameters=None):
        try:
            with engine.connect() as connection:
                return [dict(row) for row in connection.execute(text(sql),parameters or {}).mappings()]
        except SQLAlchemyError:
            raise HTTPException(503,'Proposal data store unavailable')

    @routes.get('/proposals')
    def proposals(limit:int=Query(25,ge=1,le=100)):
        return {'items':rows('SELECT proposal_id,episode_id,provider_status,model_id,model_revision,outcome,execution_eligible,source_episode_id,source_observation_id FROM plan_attempts ORDER BY started_at_utc DESC,proposal_id LIMIT :limit',{'limit':limit})}

    @routes.get('/proposal-ingestion/counts')
    def counts():
        return {name:rows('SELECT count(*) AS total FROM '+name)[0]['total'] for name in ['plan_attempts','proposal_artifacts','validation_results','proposal_receipts','proposal_findings']}

    @routes.get('/proposal-ingestion/receipts')
    def receipts(limit:int=Query(100,ge=1,le=500)):
        return {'items':rows('SELECT * FROM proposal_receipts ORDER BY received_at_utc DESC,receipt_sha256 LIMIT :limit',{'limit':limit})}

    @routes.get('/proposal-ingestion/findings')
    def findings(limit:int=Query(100,ge=1,le=500)):
        return {'items':rows('SELECT * FROM proposal_findings ORDER BY created_at_utc DESC,finding_id LIMIT :limit',{'limit':limit})}

    @routes.get('/proposals/{proposal_id}')
    def proposal(proposal_id:UUID):
        values=rows('SELECT * FROM plan_attempts WHERE proposal_id=:id',{'id':proposal_id})
        if not values:
            raise HTTPException(404,'Accepted inference attempt not found; inspect receipts/findings')
        return {**values[0],'artifacts':rows('SELECT * FROM proposal_artifacts WHERE proposal_id=:id ORDER BY role',{'id':proposal_id}),'validation':next(iter(rows('SELECT * FROM validation_results WHERE proposal_id=:id',{'id':proposal_id})),None)}

    @routes.get('/proposal-artifacts/{artifact_id}')
    def artifact(artifact_id:UUID):
        values=rows('SELECT * FROM proposal_artifacts WHERE artifact_id=:id',{'id':artifact_id})
        if not values:
            raise HTTPException(404,'Proposal artifact not found')
        item=values[0]
        path=raw_root/'objects'/'sha256'/item['sha256'][:2]/item['sha256']
        if not path.resolve().is_relative_to(raw_root.resolve()):
            raise HTTPException(409,'Invalid artifact location')
        try:
            data=path.read_bytes()
        except OSError:
            raise HTTPException(409,'Proposal artifact bytes unavailable')
        if hashlib.sha256(data).hexdigest()!=item['sha256'] or len(data)!=item['byte_size']:
            raise HTTPException(409,'Proposal artifact integrity failed')
        return Response(data,media_type=item['media_type'])
    return routes
