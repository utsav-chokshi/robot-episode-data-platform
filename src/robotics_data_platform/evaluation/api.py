from uuid import UUID
from fastapi import APIRouter,HTTPException,Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from robotics_data_platform.evaluation.reports import verify_report


def router(engine,root):
    routes=APIRouter(prefix='/api/v1',tags=['evaluation'])
    @routes.get('/evaluations')
    def reports(limit:int=Query(50,ge=1,le=100)):
        try:
            with engine.connect() as connection:
                return {'items':[dict(row) for row in connection.execute(text('SELECT report_id,dataset_id,task_set_name,split,created_at_utc FROM evaluation_runs ORDER BY created_at_utc DESC LIMIT :limit'),{'limit':limit}).mappings()]}
        except SQLAlchemyError:
            raise HTTPException(503,'Evaluation registry unavailable')

    @routes.get('/evaluations/{report_id}')
    def report(report_id:UUID):
        try:
            with engine.connect() as connection:
                row=connection.execute(text('SELECT fingerprint,manifest_sha256 FROM evaluation_runs WHERE report_id=:id'),{'id':report_id}).mappings().first()
        except SQLAlchemyError:
            raise HTTPException(503,'Evaluation registry unavailable')
        if row is None:
            raise HTTPException(404,'Evaluation not found')
        try:
            return verify_report(root,row['fingerprint'],row['manifest_sha256'])
        except (OSError,ValueError,KeyError):
            raise HTTPException(503,'Evaluation integrity check failed')
    return routes
