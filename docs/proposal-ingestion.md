# Proposal ingestion and analytical datasets

Step 9c adds a data-only receiver for the inference evidence produced in Step 9b. It never imports an ML runtime, calls a provider or executes robot commands.

## Run

After capture/ingestion and proposal generation:

```powershell
docker compose up --build -d --wait api
docker compose --profile proposal-ingestion run --rm proposal-ingestor
docker compose --profile proposal-ingestion run --rm proposal-ingestor python -m robotics_data_platform.planning.ingestion replay
docker compose --profile pipelines run --build --rm pipeline
docker compose --profile test run --build --rm pipeline-tests
```

The first proposal command reads proposal_spool read-only, preserving admitted bytes into raw_data before SQL projection. Replay uses only raw proposal receipts and source evidence; the proposal spool and model cache are unnecessary. Baseline source observations must be ingested first. No model is rerun by these commands.

## Contract and storage

Inference has its own strict v1 evidence contract: RUNNING intent, a FINISHED/NOT_EXECUTED manifest and a ready marker containing the original manifest checksum. UUIDs, UTC timestamps, terminal closure, source lineage, artifact bindings, roles/media/access, bounds, provider provenance, measurements and validation consistency are checked. This uses the actual Step 9b manifest protocol, rather than manufacturing producer events or robot action traces that were never captured. The baseline event-envelope receiver remains unchanged.

All admitted ready/intent/manifest bytes and available artifact bytes are copied to the shared content-addressed raw store. proposal_receipts/<sha>.json freezes what was received, including missing/corrupt evidence mappings. proposal_statuses holds processing acknowledgments. Oversized admission diagnostics do not claim that unadmitted bytes were copied; originals remain in the spool. Existing corrupt raw-store objects stop preservation rather than being overwritten. The receiver applies the existing 128 MiB free-space threshold, at most 32 artifact references, 8 MiB per artifact and 32 MiB per attempt.

SQL projection is transactional and serialized for identity checks. plan_attempts holds accepted VLM_EVALUATION episode/proposal identities, source links, provider status and immutable manifest/configuration. proposal_artifacts holds local artifact associations; validation_results exists only for completed inference. Source observations and image artifacts have composite same-episode foreign keys. Inference identity is separate from baseline episodes, with no fictitious baseline event/action rows. Database checks enforce NOT_EXECUTED and execution_eligible=false.

Typed validation independently recomputes the proposal result from original response bytes and public vocabulary. A schema-invalid response is **complete, eligible inference evidence**, not quarantine. Timeout/unavailable/error attempts likewise ingest when their evidence is complete, with no fabricated response or validation. Visual/semantic correctness is not inferred from schema validity.

Unfinished capture or missing references is PENDING_REFERENCES; malformed contracts/corrupt evidence is QUARANTINED; changed accepted identities are CONFLICTED. Failed SQL leaves PENDING_DB after raw preservation. Receipts/findings remain inspectable. Retries of identical accepted evidence are DUPLICATE and do not append facts. New complete evidence may resolve earlier pending findings; historical receipts remain preserved. Older missing-byte receipts can remain historically pending even after a later complete receipt is accepted. Conflicting accepted snapshots are never replaced.

## Inspect

- GET /api/v1/proposals: accepted attempts with source IDs, provider status and model version.
- GET /api/v1/proposals/{proposal_id}: preserved manifest/configuration, local artifact associations and optional validation.
- GET /api/v1/proposal-artifacts/{artifact_id}: checksum-verified bytes, including original invalid model responses.
- GET /api/v1/proposal-ingestion/counts, /receipts and /findings: processing/audit metadata, including evidence not accepted as facts.

These routes are read-only and require no model dependencies. The baseline /episodes routes continue to describe robot baseline attempts; VLM attempt episodes are explicitly exposed through /proposals.

## Versioned Parquet

Snapshots with proposal receipts use input version 1.1.0 and publish twelve tables: the original seven plus proposals, proposal_artifacts, proposal_validations, proposal_receipts and proposal_findings. The PostgreSQL dataset registry additionally freezes proposal-receipt membership. All admitted attempt identities and receipt findings stay in audit views; only independently reverified complete attempts contribute eligible artifacts/validations. Later SQL changes do not alter frozen inputs. Corruption of frozen eligible evidence blocks a build/rebuild.

The original baseline materializer remains in baseline_v1.py. Inputs without proposal evidence retain the seven-table path and original logical metrics, so existing Step 8 datasets can still be rebuilt. The new build fingerprint covers all transform modules. Publication and retries keep the existing stage/verify/atomic-promotion/transactional-registry protocol.

DuckDB adds proposal/artifact uniqueness, eligible-parent relationships, validation-provider consistency and zero execution-eligibility checks. Inference metrics expose complete-data and provider-completion counts, schema validity over all captured attempts, and schema validity conditional on completed inference. Timeouts and incomplete evidence remain in the all-attempt denominator. These are captured-attempt metrics, not coverage of a frozen expected VLM benchmark. Physical success for VLM_EVALUATION is NOT_APPLICABLE. The structured baseline's physical-success metrics remain separate.

The current provider's two real smoke responses remain schema-invalid. This integration establishes preservation and analytical visibility, not usable robot planning or a held-out accuracy result. Dashboard and fixed task-set evaluation are Step 10 work.
