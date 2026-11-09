# Implementation history (archived)

This is the chronological README retained before portfolio packaging. Present-tense status and next-step notes below describe their original implementation stage. Use the current [README](../README.md), [architecture](architecture.md) and [benchmark results](benchmark-validation.md) for the final state.

# Robotics Data and Evaluation Platform

A local portfolio project focused on reliable data capture and evaluation for simulated robotics tasks.

**Current stage: fixed benchmark executed and complete local workflow validated.** Captures pass through raw preservation, validation, PostgreSQL projection and immutable Parquet publication. The frozen benchmark produced 54/54 successful structured robot runs and 0/90 schema-valid model plans, with complete evidence for all 144 applicable slots. The dashboard exposes original images, traces, responses, failure categories and provenance. Model proposals cannot execute robot actions. See [benchmark results and reproduction](benchmark-validation.md).

## Run locally

Requires Docker with Linux containers and Compose. PowerShell is used below; no host Python or dedicated GPU is required.

After generating/ingesting data, open the workspace with `docker compose --profile dashboard up --build -d --wait dashboard`, then visit http://localhost:8501. See [evaluation, report publication and dashboard usage](evaluation-dashboard.md).

```powershell
./scripts/Initialize-LocalEnvironment.ps1
docker compose up --build -d --wait api
Invoke-RestMethod http://localhost:8010/healthz
Invoke-RestMethod http://localhost:8010/readyz
```

API reference: http://localhost:8010/docs. Change `API_PORT` in the generated `.env` if needed. The API binds to localhost; the optional Prefect UI also binds to localhost. The database stays inside the project network. Initial builds need internet; normal operation uses local containers.

The initialization script preserves an existing `.env`; it creates a random password on first use without displaying it. The PostgreSQL volume retains its original credentials. Do not regenerate `.env` while reusing a database volume without coordinating a credential change.

On Linux/macOS, create `.env` from `.env.example`, set `POSTGRES_PASSWORD` to a random URL-safe password, then use the same Compose commands. That host path is documented for portability but must be reported as unverified until tested.

## Verify

```powershell
docker compose --profile test run --build --rm tests
```

Integration tests use a separate PostgreSQL service with temporary storage, creating uniquely named test databases. They do not connect to the application's database. To stop services while preserving the application volume:

```powershell
docker compose --profile test down
```

Do not add `--volumes` unless you intend to remove persisted application data. See [development](development.md), [architecture](architecture.md), and [validation](validation.md).

## Direction

Python, MuJoCo/Panda, FastAPI/Pydantic, PostgreSQL, Prefect, Parquet/PyArrow, DuckDB, Streamlit and Docker Compose. Dependencies are introduced with their components. The supported task path will use deterministic structured execution. Vision-language proposals remain experimental: tested small models failed important object-grounding checks, and model output will not dispatch robot commands.

MIT license for this repository's original code. The embedded Panda robot asset retains its Apache 2.0 license; see THIRD_PARTY.md. No paid API or model weights are required for the simulator.

## Run a robot task and capture an episode

```powershell
docker compose --profile simulation run --build --rm simulator run --object-id package_01 --destination-id zone_a --seed 7
docker compose --profile simulation run --rm simulator suite
```

The optional simulator runs offline on CPU, using the pinned MuJoCo/Panda asset. Structured tasks use known simulator references; this is not image-based planning. Captured episodes persist in the project's capture_spool Docker volume. See [simulator commands, capture format and export](simulator.md).

Local development verification on 2026-11-09: ten core tests passed; all seventeen simulator/capture checks passed (thirteen successful tasks, one expected failed grasp and three expected rejections). This is bounded development evidence, not the final evaluation dataset or an ingestion durability claim. No remote CI run has been observed.

## Ingest and inspect captured episodes

```powershell
docker compose up --build -d --wait api
docker compose --profile ingestion run --rm ingestor
docker compose --profile ingestion run --rm ingestor python -m robotics_data_platform.ingestion.cli replay
```

Inspect localhost:8010/docs or GET /api/v1/episodes. Replay reads preserved raw receipts and cannot execute robot tasks. See [ingestion, quarantine and recovery](ingestion.md).

Local Step 7 verification on 2026-11-09: 30 tests passed. The existing 17 episodes were ingested as 240 events, 224 artifact references, 69 observations and 27 actions; repeating intake and raw replay left counts and capture manifests unchanged. Failed/rejected tasks remain in the catalog. This is a local batch pipeline, not the final held-out evaluation or a distributed exactly-once claim.

## Build and inspect analytical datasets

```powershell
docker compose --profile pipelines up --build -d --wait prefect
docker compose --profile pipelines run --build --rm pipeline
docker compose --profile test run --build --rm pipeline-tests
```

Prefect UI: http://localhost:4201. Dataset metadata and verified manifests: GET /api/v1/datasets and GET /api/v1/datasets/{dataset_id}. See [analytical datasets and frozen rebuilds](analytics.md). Baseline-only snapshots have seven tables; snapshots with inference evidence add five proposal audit/fact tables. Metrics expose their numerators and denominators. Historical development snapshots remain available alongside the fixed benchmark; the bounded scene and public vocabulary do not establish held-out visual generalization.

Local Step 8 verification on 2026-11-09: 37 tests passed. Two Prefect flows reused one dataset version; rebuilding frozen inputs matched all seven logical tables and metrics. Physical success was 13/14 (including the injected failed grasp); normal-task success was 13/13 and rejection matches were 3/3. All 516 capture files, 433 raw-store files and receiver table counts were unchanged. These are structured-baseline development results, not held-out language-planning performance.

## Experimental proposal pipeline — Step 9

The optional vlm profile runs pinned SmolVLM-256M inference offline with a hard process deadline. Its strict ready/clarify/reject contract separates schema validity from known-handle membership; every proposal remains execution-ineligible. Original images, instructions, prompts, outputs, provenance and timings are retained. See [preparation and inference](proposals.md) and examples/proposals for JSON Schemas.

```powershell
docker compose --profile proposal-ingestion run --rm proposal-ingestor
docker compose --profile proposal-ingestion run --rm proposal-ingestor python -m robotics_data_platform.planning.ingestion replay
docker compose --profile pipelines run --rm pipeline
```

See [proposal ingestion, audit and analytical metrics](proposal-ingestion.md). GET /api/v1/proposals exposes accepted inference attempts; receipts/findings expose incomplete or quarantined evidence. Replay and dataset builds cannot rerun the model or execute a robot.

Local verification on 2026-11-09: **85 tests passed** in the full analytics/core image. The three real development attempts (two schema-invalid responses, one deliberate timeout) ingested successfully; repeated delivery and raw-only replay produced duplicates without additional facts. Two Prefect runs reused one twelve-table dataset. Both the original seven-table version and the new version rebuilt with matching logical rows and metrics. All original capture, raw, proposal-spool and analytical files remained unchanged.

Inference metrics: complete evidence 3/3, provider completion 2/3, schema validity 0/2 completed attempts and 0/3 all captured attempts. VLM physical success is NOT_APPLICABLE. These results demonstrate traceability and failure handling; the current model remains unsuitable for robot task selection. Next: fixed task-set evaluation and dashboard work.

## Evaluation dashboard — Step 10

Open http://localhost:8501 after starting the dashboard profile. The five read-only views show dataset metrics, fixed-slot evaluation, robot images/traces, original model responses and quality findings. See [evaluation and dashboard](evaluation-dashboard.md) for report commands.

Local checks: 97 core/evaluation/analytics tests and seven dashboard acceptance tests passed. The current development report retains the failed grasp, two invalid model responses and timeout. The 30-case, three-seed benchmark is frozen with 90 VLM slots, 54 applicable structured-baseline slots and 36 baseline-not-applicable slots. It has not been run: the dashboard shows Not measured rather than benchmark accuracy. Next: Step 11 benchmark execution and full workflow validation.

## Complete workflow validation — Step 11

The fixed benchmark is now complete: structured physical success **54/54**, model provider completion **90/90**, model schema validity and expected-result matches **0/90**. All model failures are retained as PLAN_SCHEMA_INVALID; model physical success is not applicable. The current model is unsuitable for task selection in this configuration. Repetition seeds and the limited scene are disclosed; these results do not establish broad visual grounding.

Local checks: **103 core/analytics/evaluation tests plus seven dashboard acceptance tests passed**. A separate workspace with fresh application data completed capture, offline inference, ingestion, a real Prefect flow, frozen rebuild, evaluation, dashboard health and duplicate-safe replay. It reused pinned images and prepared read-only weights. Remote CI and other host platforms remain unverified.

Measured model worker wall: p50 17.49 seconds, p95 24.88 seconds; 90 sequential slots took about 28.1 minutes. The two-CPU, 6 GiB offline model container's largest periodic memory sample was about 1.44 GiB; this is not an exhaustive peak. Read [benchmark execution, recovery and reproduction](benchmark-validation.md) for denominators, provenance and resource details. Next: Step 12 portfolio packaging.
