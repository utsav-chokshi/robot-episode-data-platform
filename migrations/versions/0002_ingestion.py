"""Episode projections, source receipts and inspectable data findings."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0002_ingestion"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("episodes",
        sa.Column("episode_id",UUID(),primary_key=True),sa.Column("task_id",UUID()),
        sa.Column("mode",sa.String(32),nullable=False),sa.Column("execution_status",sa.String(32),nullable=False),
        sa.Column("outcome",sa.String(32)),sa.Column("failure_code",sa.String(64)),
        sa.Column("ingestion_status",sa.String(32),nullable=False),sa.Column("completeness_status",sa.String(32),nullable=False),
        sa.Column("content_fingerprint",sa.String(64)),sa.Column("source_summary",JSONB()),
        sa.Column("received_at_utc",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.CheckConstraint("completeness_status IN ('COMPLETE','INCOMPLETE','CORRUPT')",name="ck_episode_completeness"))
    op.create_table("raw_events",
        sa.Column("event_id",UUID(),primary_key=True),sa.Column("episode_id",UUID(),sa.ForeignKey("episodes.episode_id"),nullable=False),
        sa.Column("producer_id",sa.String(128),nullable=False),sa.Column("producer_session_id",UUID(),nullable=False),
        sa.Column("producer_sequence",sa.BigInteger(),nullable=False),sa.Column("event_type",sa.String(64),nullable=False),
        sa.Column("sha256",sa.String(64),nullable=False),sa.Column("payload",JSONB(),nullable=False),
        sa.Column("captured_at_utc",sa.DateTime(timezone=True),nullable=False),sa.Column("simulation_time_ns",sa.BigInteger()),
        sa.Column("received_at_utc",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint("producer_session_id","producer_sequence",name="uq_event_stream_sequence"),
        sa.UniqueConstraint("episode_id","event_id",name="uq_episode_event"),
        sa.CheckConstraint("producer_sequence >= 0",name="ck_event_sequence"))
    op.create_table("artifacts",
        sa.Column("artifact_id",UUID(),primary_key=True),sa.Column("episode_id",UUID(),sa.ForeignKey("episodes.episode_id"),nullable=False),
        sa.Column("producer_event_id",UUID(),nullable=False),sa.Column("sha256",sa.String(64),nullable=False),
        sa.Column("byte_size",sa.BigInteger(),nullable=False),sa.Column("role",sa.String(128),nullable=False),
        sa.Column("media_type",sa.String(64),nullable=False),sa.Column("access_class",sa.String(32),nullable=False),
        sa.Column("integrity_state",sa.String(16),nullable=False),
        sa.Column("created_at_utc",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("episode_id","artifact_id",name="uq_episode_artifact"),
        sa.ForeignKeyConstraint(["episode_id","producer_event_id"],["raw_events.episode_id","raw_events.event_id"],name="fk_artifact_source_event"),
        sa.CheckConstraint("byte_size >= 0",name="ck_artifact_size"))
    op.create_table("observations",
        sa.Column("observation_id",UUID(),primary_key=True),sa.Column("episode_id",UUID(),nullable=False),
        sa.Column("event_id",UUID(),nullable=False),sa.Column("image_artifact_id",UUID(),nullable=False),
        sa.Column("camera_config_artifact_id",UUID(),nullable=False),sa.Column("camera_id",sa.String(128),nullable=False),
        sa.Column("frame_id",sa.String(128),nullable=False),sa.Column("width_px",sa.Integer(),nullable=False),sa.Column("height_px",sa.Integer(),nullable=False),
        sa.ForeignKeyConstraint(["episode_id","event_id"],["raw_events.episode_id","raw_events.event_id"]),
        sa.ForeignKeyConstraint(["episode_id","image_artifact_id"],["artifacts.episode_id","artifacts.artifact_id"]),
        sa.ForeignKeyConstraint(["episode_id","camera_config_artifact_id"],["artifacts.episode_id","artifacts.artifact_id"]))
    op.create_table("actions",
        sa.Column("action_id",UUID(),primary_key=True),sa.Column("episode_id",UUID(),nullable=False),sa.Column("command_id",UUID(),nullable=False),
        sa.Column("primitive",sa.String(16),nullable=False),sa.Column("object_id",sa.String(64),nullable=False),sa.Column("destination_id",sa.String(64)),
        sa.Column("started_event_id",UUID(),nullable=False),sa.Column("finished_event_id",UUID(),nullable=False),
        sa.Column("measurement_artifact_id",UUID(),nullable=False),sa.Column("status",sa.String(16),nullable=False),
        sa.Column("duration_ms",sa.Float(),nullable=False),sa.Column("failure_code",sa.String(64)),
        sa.ForeignKeyConstraint(["episode_id","started_event_id"],["raw_events.episode_id","raw_events.event_id"]),
        sa.ForeignKeyConstraint(["episode_id","finished_event_id"],["raw_events.episode_id","raw_events.event_id"]),
        sa.ForeignKeyConstraint(["episode_id","measurement_artifact_id"],["artifacts.episode_id","artifacts.artifact_id"]))
    op.create_table("outcomes",
        sa.Column("episode_id",UUID(),sa.ForeignKey("episodes.episode_id"),primary_key=True),sa.Column("event_id",UUID(),nullable=False),
        sa.Column("outcome",sa.String(32),nullable=False),sa.Column("failure_code",sa.String(64)),sa.Column("measurement_artifact_id",UUID()),
        sa.ForeignKeyConstraint(["episode_id","event_id"],["raw_events.episode_id","raw_events.event_id"]),
        sa.ForeignKeyConstraint(["episode_id","measurement_artifact_id"],["artifacts.episode_id","artifacts.artifact_id"]))
    op.create_table("ingestion_receipts",
        sa.Column("receipt_sha256",sa.String(64),primary_key=True),sa.Column("source_episode_id",UUID()),
        sa.Column("manifest_sha256",sa.String(64),nullable=False),sa.Column("status",sa.String(32),nullable=False),
        sa.Column("processed_at_utc",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False))
    op.create_table("ingestion_findings",
        sa.Column("finding_id",sa.String(64),primary_key=True),sa.Column("receipt_sha256",sa.String(64),sa.ForeignKey("ingestion_receipts.receipt_sha256"),nullable=False),
        sa.Column("episode_id",UUID()),sa.Column("code",sa.String(64),nullable=False),sa.Column("message",sa.String(512),nullable=False),
        sa.Column("reference",sa.String(256)),sa.Column("resolved_by_receipt",sa.String(64)),
        sa.Column("created_at_utc",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False))
    op.create_index("ix_events_episode","raw_events",["episode_id","producer_sequence"])
    op.create_index("ix_findings_episode","ingestion_findings",["episode_id","code"])
    op.create_index("ix_episodes_received","episodes",["received_at_utc"])


def downgrade():
    for name in ["ingestion_findings","ingestion_receipts","outcomes","actions","observations","artifacts","raw_events","episodes"]:
        op.drop_table(name)
