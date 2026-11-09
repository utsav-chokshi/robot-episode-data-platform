"""Read-only registry inspection without optional analytics dependencies."""
from uuid import UUID
from fastapi import APIRouter,HTTPException,Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from robotics_data_platform.analytics.files import verify_published


def router(engine,dataset_root):
    routes = APIRouter(prefix='/api/v1',tags=['datasets'])

    @routes.get('/pipeline-runs')
    def runs(limit:int=Query(default=25,ge=1,le=100)):
        try:
            with engine.connect() as connection:
                return {'items':[dict(r) for r in connection.execute(text('SELECT * FROM pipeline_runs ORDER BY started_at_utc DESC LIMIT :limit'),{'limit':limit}).mappings()]}
        except SQLAlchemyError:
            raise HTTPException(503,'Pipeline journal unavailable')

    @routes.get('/datasets')
    def datasets(limit:int=Query(default=25,ge=1,le=100)):
        try:
            with engine.connect() as connection:
                rows = connection.execute(text('SELECT dataset_id,build_fingerprint,input_sha256,created_at_utc FROM dataset_versions ORDER BY created_at_utc DESC LIMIT :limit'),{'limit':limit}).mappings().all()
                return {'items':[dict(r) for r in rows]}
        except SQLAlchemyError:
            raise HTTPException(503,'Dataset registry unavailable')

    @routes.get('/datasets/{dataset_id}')
    def dataset(dataset_id:UUID):
        try:
            with engine.connect() as connection:
                row = connection.execute(text('SELECT build_fingerprint,manifest_sha256 FROM dataset_versions WHERE dataset_id=:id'),{'id':dataset_id}).mappings().first()
        except SQLAlchemyError:
            raise HTTPException(503,'Dataset registry unavailable')
        if row is None:
            raise HTTPException(404,'Dataset not found')
        try:
            return verify_published(dataset_root,row['build_fingerprint'],row['manifest_sha256'])
        except (ValueError,OSError,KeyError):
            raise HTTPException(503,'Published dataset integrity check failed')
    return routes
