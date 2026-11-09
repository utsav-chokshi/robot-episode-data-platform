"""Read-only episode/evidence inspection; no task or command endpoints."""
import hashlib
from uuid import UUID

from fastapi import APIRouter,HTTPException,Query,Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from robotics_data_platform.ingestion.repository import counts


def router(engine,raw_root):
    routes = APIRouter(prefix="/api/v1",tags=["episode data"])

    def rows(statement,parameters=None):
        try:
            with engine.connect() as connection:
                return [dict(row) for row in connection.execute(text(statement),parameters or {}).mappings()]
        except SQLAlchemyError as error:
            raise HTTPException(503,"Data store is not ready") from error

    @routes.get("/ingestion/counts")
    def ingestion_counts():
        try:
            return counts(engine)
        except SQLAlchemyError as error:
            raise HTTPException(503,"Data store is not ready") from error

    @routes.get("/episodes")
    def episodes(limit:int=Query(25,ge=1,le=100)):
        return {"items":rows("SELECT * FROM episodes ORDER BY received_at_utc DESC,episode_id LIMIT :limit",{"limit":limit}),"limit":limit}

    @routes.get("/episodes/{episode_id}")
    def episode(episode_id:UUID):
        values = rows("SELECT * FROM episodes WHERE episode_id=:id",{"id":episode_id})
        if not values:
            raise HTTPException(404,"Episode not found")
        return {**values[0],"artifacts":rows("SELECT * FROM artifacts WHERE episode_id=:id ORDER BY artifact_id",{"id":episode_id}),"observations":rows("SELECT * FROM observations WHERE episode_id=:id ORDER BY observation_id",{"id":episode_id}),"actions":rows("SELECT * FROM actions WHERE episode_id=:id ORDER BY action_id",{"id":episode_id}),"findings":rows("SELECT * FROM ingestion_findings WHERE episode_id=:id ORDER BY created_at_utc",{"id":episode_id})}

    @routes.get("/episodes/{episode_id}/events")
    def events(episode_id:UUID,limit:int=Query(100,ge=1,le=500)):
        return {"items":rows("SELECT * FROM raw_events WHERE episode_id=:id ORDER BY producer_session_id,producer_sequence LIMIT :limit",{"id":episode_id,"limit":limit}),"ordering":"producer_session_id, producer_sequence; no global event-time order"}

    @routes.get("/ingestion/findings")
    def findings(limit:int=Query(100,ge=1,le=500)):
        return {"items":rows("SELECT * FROM ingestion_findings ORDER BY created_at_utc DESC,finding_id LIMIT :limit",{"limit":limit})}

    @routes.get("/artifacts/{artifact_id}")
    def artifact(artifact_id:UUID):
        values = rows("SELECT * FROM artifacts WHERE artifact_id=:id",{"id":artifact_id})
        if not values:
            raise HTTPException(404,"Artifact not found")
        value = values[0]
        # Do not create directories on the API's read-only artifact volume.
        path = raw_root/"objects"/"sha256"/value["sha256"][:2]/value["sha256"]
        if not path.resolve().is_relative_to(raw_root.resolve()):
            raise HTTPException(409,"Artifact path is unavailable")
        try:
            data = path.read_bytes()
        except OSError as error:
            raise HTTPException(409,"Artifact bytes are unavailable") from error
        if len(data) != value["byte_size"] or hashlib.sha256(data).hexdigest() != value["sha256"]:
            raise HTTPException(409,"Artifact integrity check failed")
        return Response(content=data,media_type=value["media_type"])

    return routes
