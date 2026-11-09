# Versioned analytical datasets

Step 8 adds data-only transforms. No ingestion, dataset build, retry or rebuild can dispatch a robot task.

## Run after capture and ingestion

```powershell
docker compose up --build -d --wait api
docker compose --profile pipelines up --build -d --wait prefect
docker compose --profile pipelines run --build --rm pipeline
```

Prefect's local UI is at http://localhost:4201. Its SQLite metadata uses its own volume; PostgreSQL and immutable manifests remain the dataset source of truth. No Prefect Cloud account is needed. Prefect runs in a separate Python environment because its FastAPI requirements differ from the data API. Both environments have hashed dependency locks.

The server writes generated UI assets under /prefect/ui using PREFECT_SERVER_UI_STATIC_DIRECTORY. This location is writable by the container's application user. The health check verifies both the API and an HTML response from the dashboard root; checking /api/health alone can miss a broken UI. If an older configuration serves `{"detail":"Not Found"}` at the root, recreate the Prefect service with the current Compose configuration while preserving its data volume.

The development flow has two tasks: freeze accepted evidence, then publish a version. Only publication retries automatically, once, against the same frozen input. It never retries execution. Large images and episode bundles stay out of orchestration parameters/results. Prefect records flow/task execution history; dataset IDs remain stable independently of that history.

## Storage and provenance

The analytical_data volume contains seals/<assessment-sha>.json, inputs/<input-sha>.json and versions/<build-fingerprint>/. Each published version has seven typed Parquet tables and a manifest with logical-row hashes, file hashes, schemas, row counts and metrics.

Freezing reads the receiver catalog, receipts and findings from one repeatable-read transaction. It verifies the immutable raw evidence before assigning a seal assessment. Accepted episode data produces integrity-eligible facts; incomplete or corrupt episodes remain in the audit table and findings. Failed and rejected tasks are eligible when their evidence is complete. PostgreSQL stores immutable seal revisions and dataset membership. Later catalog changes require a new input snapshot; they cannot change published membership.

Facts derive from validated source bundles, not mutable catalog payloads. The development task definition supplies expected labels; the capture suite report supplies attempt assignments only. Captured request fields must match the case before it receives analytical credit. Report passed flags and reported actual outcomes are not analytical truth. Missing assignments remain expected slots. Duplicate assignments and changed expected labels fail input validation.

The build identity includes the input checksum, transformation/builder code and Arrow/DuckDB versions. Processing time, random flow IDs and Parquet bytes are excluded. Files are staged, flushed, atomically promoted on the same filesystem and then registered transactionally. A retry after promotion reconciles the existing manifest. Registered files that disappear or change cause an error; they are never silently regenerated over a published version. This is local-filesystem recovery evidence, not a power-loss or distributed transaction guarantee.

## Inspect and rebuild

GET /api/v1/datasets lists registry metadata. GET /api/v1/datasets/{dataset_id} verifies the manifest, frozen input and all Parquet checksums before returning metrics. The API mounts analytical files read-only and requires no Arrow, DuckDB or Prefect dependencies.

GET /api/v1/pipeline-runs exposes the application journal, including operation, frozen/build identity, state and optional Prefect flow ID. Errors retain a bounded exception class, without credentials. An abruptly killed process can leave a RUNNING row; this remains visible for investigation rather than being silently marked successful.

Use the input and build identities printed by the flow:

```powershell
docker compose --profile pipelines run --rm pipeline python -m robotics_data_platform.analytics.cli build --identity INPUT_SHA256
docker compose --profile pipelines run --rm pipeline python -m robotics_data_platform.analytics.cli rebuild --identity BUILD_FINGERPRINT
docker compose --profile test run --build --rm pipeline-tests
```

A rebuild uses frozen inputs and revalidates raw evidence, writes private temporary tables, and compares sorted logical rows and metrics. It does not require byte-identical Parquet and does not publish another dataset.

DuckDB checks primary-key uniqueness, eligible parent episodes and image references. Metrics expose numerator and denominator: eligible-slot coverage, expectation matches, physical success including injected faults, normal-task success and rejection matches. Missing/ineligible slots do not disappear from denominators. An expected failed grasp can match its expected disposition without being a physical success.

## Limits

This is the 17-case structured baseline development set. It is not the future held-out 90-slot evaluation, a language-planning benchmark, or evidence of real-world robot reliability. Model proposals, dashboard comparisons, richer latency metrics and scheduled deployments remain subsequent work. Pipeline execution uses two CPUs and a 2 GiB limit; Prefect is capped at 1 GiB. The simulator need not run during analytics.

## Step 9c extension

Snapshots containing proposal receipts now use input version 1.1.0 and add five inference audit/fact tables to the original seven. Historical baseline-only rebuilds dispatch through baseline_v1.py; the original published version was rebuilt successfully after the extension. See [proposal ingestion and dataset denominators](proposal-ingestion.md).
