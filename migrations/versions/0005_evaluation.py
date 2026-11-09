"""Immutable dataset-linked evaluation report registry."""
from alembic import op
revision='0005_evaluation'
down_revision='0004_proposals'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE TABLE evaluation_runs (
      report_id uuid PRIMARY KEY, fingerprint varchar(64) NOT NULL UNIQUE,
      dataset_id uuid NOT NULL REFERENCES dataset_versions,
      task_set_name varchar(128) NOT NULL, split varchar(32) NOT NULL,
      manifest_sha256 varchar(64) NOT NULL, manifest jsonb NOT NULL,
      created_at_utc timestamptz NOT NULL DEFAULT now())""")


def downgrade():
    op.execute('DROP TABLE evaluation_runs')
