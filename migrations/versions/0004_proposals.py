"""Mode-specific inference receipts, attempts and validation projections."""
from alembic import op

revision='0004_proposals'
down_revision='0003_analytics'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('ALTER TABLE observations ADD CONSTRAINT uq_episode_observation UNIQUE(episode_id,observation_id)')
    op.execute("""CREATE TABLE proposal_receipts (
      receipt_sha256 varchar(64) PRIMARY KEY, proposal_id uuid NOT NULL,
      manifest_sha256 varchar(64), status varchar(32) NOT NULL,
      received_at_utc timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE plan_attempts (
      proposal_id uuid PRIMARY KEY, episode_id uuid NOT NULL UNIQUE,
      mode varchar(32) NOT NULL CHECK(mode='VLM_EVALUATION'),
      outcome varchar(32) NOT NULL CHECK(outcome='NOT_EXECUTED'),
      execution_eligible boolean NOT NULL CHECK(NOT execution_eligible),
      source_episode_id uuid NOT NULL, source_observation_id uuid NOT NULL,
      source_image_artifact_id uuid NOT NULL, source_image_sha256 varchar(64) NOT NULL,
      receipt_sha256 varchar(64) NOT NULL REFERENCES proposal_receipts,
      manifest_sha256 varchar(64) NOT NULL, provider_status varchar(32) NOT NULL,
      model_id text NOT NULL, model_revision varchar(40) NOT NULL,
      manifest jsonb NOT NULL, configuration jsonb NOT NULL,
      started_at_utc timestamptz NOT NULL, finished_at_utc timestamptz NOT NULL,
      UNIQUE(episode_id,proposal_id),
      FOREIGN KEY(source_episode_id,source_observation_id) REFERENCES observations(episode_id,observation_id),
      FOREIGN KEY(source_episode_id,source_image_artifact_id) REFERENCES artifacts(episode_id,artifact_id))""")
    op.execute("""CREATE TABLE proposal_artifacts (
      artifact_id uuid PRIMARY KEY, episode_id uuid NOT NULL, proposal_id uuid NOT NULL,
      role varchar(64) NOT NULL, sha256 varchar(64) NOT NULL, byte_size bigint NOT NULL,
      media_type varchar(128) NOT NULL, access_class varchar(32) NOT NULL,
      UNIQUE(proposal_id,role), UNIQUE(episode_id,artifact_id),
      FOREIGN KEY(episode_id,proposal_id) REFERENCES plan_attempts(episode_id,proposal_id))""")
    op.execute("""CREATE TABLE validation_results (
      proposal_id uuid PRIMARY KEY REFERENCES plan_attempts,
      validator_version varchar(64) NOT NULL, schema_valid boolean NOT NULL,
      reference_valid boolean, execution_eligible boolean NOT NULL CHECK(NOT execution_eligible),
      reason_codes jsonb NOT NULL, parsed_proposal jsonb)""")
    op.execute("""CREATE TABLE proposal_findings (
      finding_id varchar(64) PRIMARY KEY, receipt_sha256 varchar(64) NOT NULL REFERENCES proposal_receipts,
      proposal_id uuid NOT NULL, code varchar(64) NOT NULL, message varchar(512) NOT NULL,
      resolved_by_receipt varchar(64), created_at_utc timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE dataset_proposal_receipts (
      dataset_id uuid NOT NULL REFERENCES dataset_versions,
      receipt_sha256 varchar(64) NOT NULL REFERENCES proposal_receipts,
      PRIMARY KEY(dataset_id,receipt_sha256))""")


def downgrade():
    for name in ['dataset_proposal_receipts','proposal_findings','validation_results','proposal_artifacts','plan_attempts','proposal_receipts']:
        op.execute('DROP TABLE '+name)
    op.execute('ALTER TABLE observations DROP CONSTRAINT uq_episode_observation')
