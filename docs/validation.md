# Foundation verification

Verified locally: 2026-11-07 on Docker Desktop's Linux engine (linux/amd64).

## Observed checks

- Compose configuration validated without printing local credentials.
- Application image built using hashed resolved runtime/test dependencies, pinned build tools and the pinned Python base image.
- Clean persistent PostgreSQL volume initialized; Alembic migration 0001_foundation completed.
- `docker compose up --build -d --wait api` completed successfully.
- Host request to /healthz: HTTP 200, status ok, application version 0.1.0.
- Host request to /readyz: HTTP 200, database reachable, schema current, contract version 1.0.0.
- Host request to /docs: HTTP 200.
- `docker compose --profile test run --build --rm tests`: 4 passed, 1 dependency deprecation warning, in 1.96 seconds (test-process time, not build/startup time).

The tests cover process liveness with an unavailable database, fail-closed readiness without connection-detail exposure, a real clean-database migration followed by repeat upgrade without resetting metadata, and readiness rejection of an incompatible contract version. Integration tests used the dedicated test PostgreSQL service and randomly named test databases; the application database was not their target.

The warning is Starlette's deprecation of its httpx-based TestClient path. The suite passes; this does not indicate a failed migration or API check. Revisit the supported test-client dependency during a deliberate dependency update rather than suppressing the warning.

CI is configured with official actions pinned to commit SHAs and a dedicated PostgreSQL service. No remote repository was created and no GitHub workflow run has occurred. A local passing test result must not be described as a passing remote CI run.

## Scope and limits

This verifies the foundation only: package installation, database migration, process health/readiness and isolated tests. Robot integration, durable ingestion, replay, Prefect flows, datasets, dashboard, model adapters and combined workload performance remain later work. The separate feasibility prototype has not yet been integrated into this repository.

Other host operating systems and CPU architectures have not been tested. README portability instructions are not evidence of those runs. Local credentials are generated into ignored .env; the database is not host-published. The application API remains bound to localhost:8010. The temporary test database service was stopped after verification; the application's persistent database and API are retained.

## Step 6 verification - 2026-11-09

- Core API/database/capture tests: 10 passed, one existing Starlette TestClient dependency warning, in 2.48 seconds of test-process time.
- Simulator image built with pinned MuJoCo 3.3.7, NumPy 2.5.4, Pillow 11.3.0, the existing constrained core dependency versions, and the pinned Panda asset revision.
- The 17-case development suite completed offline under two CPUs and a 3 GiB container limit. All expectations passed: twelve distinct package/destination/seed combinations plus one exact repeated baseline succeeded; the deliberately open gripper produced GRASP_FAILED; missing-object, invalid-destination and experimental-mode requests were rejected without commands/actions.
- Each episode's declared byte hashes, sizes, contiguous event sequence, episode identity and terminal stream closure were checked. The repeated baseline had equal measured placement results and a new episode ID.
- The run captured 240 events and 224 artifact references across 17 episode folders. These are local captures awaiting ingestion and full quality assessment, not a validated analytical dataset.
- Successful and failed episodes were exported through the public CLI and their images were visually inspected. The success image shows the red package moved into its destination; the failure evidence retains the unchanged package and empty grasp contacts.
- The API was refreshed to the current app image; migration completion and readiness remained healthy. The temporary test PostgreSQL service was stopped afterward.

The development suite is not the planned frozen 90-slot benchmark. Coverage is three package handles, two destinations, seeds 7 and 8, and one repeat/fault scenario in this environment. No claim of arbitrary-object, general motion-planning, physical-robot or cross-platform bitwise repeatability is made. Event-specific payload validation, receiver-side duplicate/conflict handling, durable replay, crash recovery, quarantine, data sealing and analytical exports remain future work.

## Step 7 verification - 2026-11-09

Final suite: 30 passed in 12.07 seconds, with the existing Starlette TestClient dependency warning. The 20 receiver checks use synthetic contract fixtures and a real isolated PostgreSQL instance. They cover idempotency, concurrent delivery, DB outage, failure after raw promotion, failure after real DB commit/before acknowledgment, changed event/artifact/sequence identities, unsupported schemas/extra fields, invalid/missing PNGs, path traversal, out-of-order manifest entries, pending references, stale partial capture, cross-episode FK rejection, mid-projection rollback, raw corruption/restoration and low-storage admission.

Migration 0002_ingestion applied to the application database and the refreshed API became ready. The existing actual simulator run ingested all 17 episodes: 13 succeeded, one failed its grasp and three were rejected. Metadata contains 240 events, 224 artifact references, 69 observations, 27 actions, 17 outcomes and 17 receipts, with zero findings for this valid capture set.

Repeated ingestion classified all 17 as duplicates. Raw-only replay likewise classified all 17 as duplicates. All eight inspected table counts were unchanged, as were the original capture episode IDs and manifest hashes. No new robot tasks were run for Step 7. Episode inspection and hash-verified PNG retrieval returned HTTP 200 through localhost:8010.

Detailed evidence is preserved in the parent validation/step-7-output folder. Quarantine/failure-injection examples were run in isolated test databases, not mixed into this clean application dataset. Tests simulate specific process-boundary failures and DB errors; exhaustive host/power-loss behavior, throughput and production deployment remain unverified. Remote CI has not run.

## Step 8 verification - 2026-11-09

The analytics/regression suite passed 37 tests in 24.61 seconds, with the existing Starlette TestClient deprecation warning. Recovery tests exercised failures during staging and after promotion before registration, damaged raw/published files, immutable frozen rebuilds after catalog changes, absent slots, duplicate assignments and failed/rejected retention. Core-only tests separately passed 30 checks and skipped the optional analytics module.

Migration 0003_analytics applied, and the API became ready. Two local Prefect flows completed in 5.92 and 5.70 seconds of recorded flow runtime, reusing dataset e52da6c6-d226-45e5-8cbb-557e9391e75a. The published development dataset contains 17 episodes, 240 events, 224 artifact references, 69 observations, 27 actions, zero findings and 17 expected slots. All six DuckDB quality checks reported zero violations.

Physical task success is 13/14 including the injected failed grasp; normal success is 13/13; rejection match is 3/3. Expectation match is 17/17 and must not be described as overall physical success. A frozen-input rebuild, also exercised through the public CLI, matched every logical table hash and metric. All 516 capture files, 433 raw-store files and eight receiver table counts remained unchanged. No new robot task was run.

Dataset listing, checksum-verified manifest retrieval, application pipeline journal, readiness and both completed Prefect states were verified through local APIs. The temporary test PostgreSQL service was stopped. API, application PostgreSQL and Prefect remain running; observed idle memory was approximately 79 MiB, 24 MiB and 225 MiB respectively, not a peak-load benchmark.

See the parent validation/step-8-analytics.md and step-8-output evidence folder, plus [analytics usage](analytics.md). This remains structured development evidence, not the final held-out benchmark or cross-platform/host-power-loss validation. Remote CI has not run.

## Step 9c verification — 2026-11-09

Full core/analytics suite: 85 passed, one existing TestClient warning, in 38.55 seconds. Migration 0004_proposals, data-only ingestion/replay, proposal/artifact APIs, twelve-table publication, repeated Prefect build and both historical/current frozen rebuilds were verified. Three real attempts persisted as two completed schema-invalid responses and one timeout. Original capture/raw/proposal/analytical files and baseline counts were unchanged. See ../../validation/step-9-proposal-ingestion.md and [proposal ingestion](proposal-ingestion.md). No inference was rerun and no model output executed a robot. Remote CI has not run.

## Step 10 verification — 2026-11-09

Core/evaluation/analytics: 97 passed in 36.67 seconds, one existing TestClient warning. Isolated dashboard acceptance: seven passed in 3.45 seconds. Both live evaluation APIs and all five dashboard views were verified, including loaded before/after images, verbatim invalid outputs, absent timeout response/timings and 11/11 quality assertions. The planned report has zero assigned benchmark attempts and is explicitly not scored as measured accuracy. Report retries reused identities; original raw/capture/dataset tree hashes were unchanged. See ../../validation/step-10-evaluation-dashboard.md. Remote CI has not run.

## Step 11 verification — 2026-11-09

The frozen benchmark completed all 54 structured and 90 proposal slots, with complete evidence. Physical structured success: 54/54. Model completion: 90/90; schema validity and expected-result matches: 0/90, all PLAN_SCHEMA_INVALID. Model physical success is not applicable. The current model remains unsuitable for task selection in this configuration. The dashboard distinguishes full audit totals from the benchmark cohort.

Checks: 103 core/analytics/evaluation tests passed in 37.51 seconds with one existing TestClient warning; seven isolated dashboard checks passed in 3.25 seconds. Repeated publication reused identities; replay yielded 71 baseline and 93 proposal duplicates with unchanged facts; both historical datasets and the new benchmark dataset rebuilt with matching logical tables and metrics. A unique fresh-volume workspace completed capture, offline inference, ingestion, a real Prefect flow, rebuild, evaluation, dashboard health and replay. Built images/prepared read-only model weights were reused. Other platforms, cold internet downloads, host power loss and remote CI remain unverified.

See [benchmark commands and measured limits](benchmark-validation.md) and ../../validation/step-11-workflow-validation.md for detailed evidence and resource percentiles. Next: Step 12 portfolio packaging.
