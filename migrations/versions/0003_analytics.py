"""Immutable analytical seals and dataset registry."""
from alembic import op

revision = "0003_analytics"
down_revision = "0002_ingestion"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE pipeline_runs (
      run_id uuid PRIMARY KEY, operation varchar(16) NOT NULL,
      input_identity varchar(64), prefect_run_id uuid,
      state varchar(16) NOT NULL CHECK(state IN ('RUNNING','COMPLETED','FAILED')),
      result_identity varchar(64), error_type varchar(128),
      started_at_utc timestamptz NOT NULL DEFAULT now(), finished_at_utc timestamptz)""")
    op.execute("""CREATE TABLE episode_seals (
      seal_id uuid PRIMARY KEY, episode_id uuid NOT NULL REFERENCES episodes,
      revision integer NOT NULL CHECK(revision>0), seal_digest varchar(64) NOT NULL,
      quality varchar(16) NOT NULL CHECK(quality IN ('COMPLETE','INCOMPLETE','CORRUPT')),
      eligible boolean NOT NULL, assessment jsonb NOT NULL,
      created_at_utc timestamptz NOT NULL DEFAULT now(),
      UNIQUE(episode_id,revision), UNIQUE(episode_id,seal_digest), UNIQUE(episode_id,seal_id))""")
    op.execute("""CREATE TABLE dataset_versions (
      dataset_id uuid PRIMARY KEY, build_fingerprint varchar(64) NOT NULL UNIQUE,
      input_sha256 varchar(64) NOT NULL, manifest_sha256 varchar(64) NOT NULL,
      manifest jsonb NOT NULL, created_at_utc timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE dataset_members (
      dataset_id uuid REFERENCES dataset_versions NOT NULL,
      episode_id uuid NOT NULL, seal_id uuid NOT NULL, eligible boolean NOT NULL,
      PRIMARY KEY(dataset_id,episode_id),
      FOREIGN KEY(episode_id,seal_id) REFERENCES episode_seals(episode_id,seal_id))""")


def downgrade():
    for table in ('dataset_members','dataset_versions','episode_seals','pipeline_runs'):
        op.execute('DROP TABLE '+table)
