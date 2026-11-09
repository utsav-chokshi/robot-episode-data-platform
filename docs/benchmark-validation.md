# Fixed benchmark and workflow validation

The benchmark uses the exact Step 10 typed task-set checksum 4e83bc1aac993c668daddc21196b16bf0210a45010185fca6f05b90c33116643. It has 54 applicable structured simulator slots and 90 proposal-only model slots; 36 language-only cases are not applicable to the structured baseline. No generated proposal can dispatch a robot action.

## Execute or resume

```powershell
docker compose --profile benchmark build benchmark-simulator benchmark-planner benchmark-data
docker compose --profile benchmark run --rm benchmark-simulator baseline
docker compose --profile benchmark run --rm benchmark-data python -m robotics_data_platform.benchmark.run ingest-baseline
docker compose --profile benchmark run --rm benchmark-planner infer
docker compose --profile benchmark run --rm benchmark-data python -m robotics_data_platform.benchmark.run ingest-inference
docker compose --profile benchmark run --rm benchmark-data python -m robotics_data_platform.benchmark.run assignments
docker compose --profile benchmark run --rm benchmark-data python -m robotics_data_platform.benchmark.run status
```

This is explicit batch execution, not a data replay operation. The model uses the pinned 256M CPU provider, original public vocabulary and unchanged 120-second/80-token limits. The fixed task set and model profile are frozen before execution; evaluator targets remain outside the model prompt. Each model attempt sees only its image, instruction and public vocabulary/profile. Each supported baseline slot has a separate simulator attempt, even where language variants imply the same structured task.

benchmark_data/fixed-v1 contains immutable run configuration and per-slot intent/completion journals. A checksum binds each completion to its result. Capture and inference evidence live under capture_spool/benchmark-v1/<slot-hash> and proposal_spool/benchmark-v1/<slot-hash>. Slot paths are deterministic, while episode/proposal identities remain independent.

Reissuing an execution stage skips complete slots without another robot/model call. A process interrupted after final evidence publication can reconcile that same attempt. If intent exists without final evidence, the stage refuses automatic execution retry and retains the partial attempt for investigation. Data ingestion and replay remain safe to repeat. Changing the frozen task definition, runner or slot binding is rejected rather than silently starting a different run.

The three source-image associations are fixed from the initial observations of the first supported baseline case at each seed, before model outcomes are known. They are reused across instructions in their seed group. Seeds are repetition groups, not evidence of independent visual variation; distinct image hashes and the limited scene are reported with results. No score-based image or attempt selection occurs.

## Publish and inspect

After all 144 applicable slots have final evidence and are ingested, run the data-only publication module in the analytics/pipeline environment with benchmark_data mounted at /benchmark and evaluation_data at /evaluations:

```powershell
docker compose --profile pipelines run --no-deps --rm -v robotics-data-platform_benchmark_data:/benchmark -v robotics-data-platform_evaluation_data:/evaluations pipeline python -m robotics_data_platform.benchmark.results
```

Build the current pipeline image first when source changed: docker compose --profile pipelines build pipeline. The publisher requires every bound attempt in PostgreSQL, freezes all retained evidence, publishes the new dataset and scores only the frozen benchmark assignments. The corpus also retains older development attempts; its audit totals are not the benchmark denominator. The structured case split is fixed_evaluation and proposal corpus totals are labeled mixed_audit_corpus. Historical development snapshots retain their original labels and logical rebuilds.

The dashboard overview distinguishes audit totals from declared structured slots. Evaluations shows the fixed report's 54/90 denominators, completion and reason-coded failures. Physical execution success is never assigned to VLM_EVALUATION. Earlier unrun reference reports remain immutable historical versions; select the report attached to the new benchmark dataset for measured results.

## Clean-volume reproduction

```powershell
./scripts/Validate-CleanWorkspace.ps1 -OutputRoot 'C:/Projects/Vision-Language Robotics Data and Evaluation Platform/validation/step-11-output'
```

The script creates a uniquely named Compose project and free localhost API, Prefect and dashboard ports. It initializes fresh database/capture/raw/proposal/dataset/evaluation/Prefect volumes, executes one structured seed-101 task and one offline model attempt, ingests both, runs a real Prefect dataset flow, rebuilds the frozen dataset, publishes an evaluation, checks both user interfaces and verifies duplicate-safe raw replay. These extra smoke attempts are isolated from the fixed benchmark and primary database.

Pinned built images and the already prepared primary model cache are reused; model weights are mounted read-only. This tests fresh application data and service/network setup, not a fresh operating system, cold internet dependency/model download or another CPU architecture. The script stops only its own uniquely named project and preserves its named volumes as reviewable evidence. It restores the API port environment setting afterward.

## Recovery and measurement scope

The full tests cover raw-first durability ordering, duplicate/conflicting identities, DB outages, pending source references, interrupted capture, dataset/report promotion before registry commit, artifact corruption/restoration, frozen rebuilds and benchmark journal recovery without reexecution. Fault injection uses isolated test databases and temporary fixtures, not the measured benchmark corpus. It does not establish host power-loss or distributed transaction guarantees.

Measured worker-wall time includes process startup, imports, model checksums, load, preprocessing, generation and teardown. Completed inference phase timings are reported separately. All-attempt timing includes recorded failures/timeouts; samples and failure counts accompany summaries. Percentiles use nearest rank. Worker process RSS and sampled Docker memory are separate measurements; the largest sample is not claimed as an exhaustive container peak. CPU quotas, memory limits, offline network mode and software provenance accompany the evidence.

See the parent validation/step-11-workflow-validation.md for observed results and step-11-output for raw evidence. Next: Step 12 packages the portfolio with measured results and explicit model/scene/reproduction limits.

## Observed local results — 2026-11-09

| Configuration | Complete evidence | Result |
| --- | --- | --- |
| Structured baseline | 54/54 | Physical success 54/54; 36 language-only slots not applicable |
| Experimental model | 90/90 | Provider completion 90/90; schema-valid plans 0/90; expected-result matches 0/90 |

All 90 model outputs failed the plan schema and remain preserved. Model physical success is not applicable. This provider is unsuitable for task selection under the tested profile. The benchmark dataset is `c4715fa5-51e9-427a-8d2b-aed006dc5066`, with measured report `9daeb072-32b4-5c22-9f5c-f3cb93e35375`. Its full audit corpus contains 71 robot attempts and 93 proposals, including earlier development attempts; use the evaluation report for benchmark denominators.

Worker-wall p50/p95 is 17.492/24.876 seconds across 90 attempts; generation-only p50/p95 is 6.106/12.020 seconds. Sequential inference slots total about 28.1 minutes. Largest periodic Docker memory sample: 1478.656 MiB across 246 samples, under the 6 GiB model-container limit. Worker peak RSS maximum: 1836.465 MiB, measured differently. Three fixed source-image associations have three distinct image hashes; they still use the same limited scene design.

Checks: 103 core/analytics/evaluation tests and seven dashboard acceptance tests passed. Full raw replay produced 71 and 93 duplicates without changing fact counts. Historical seven-/twelve-table datasets and the benchmark dataset rebuilt with matching logical tables and metrics; publication retries reused identities. A fresh-volume namespace completed the real Prefect flow and entire smoke workflow, including dashboard health and replay. It reused built images and read-only prepared weights. Other hosts and remote CI remain unverified.

All pre-existing evidence retained its exact checksum: 516 capture files, 465 raw-store files, 41 proposal files and 42 analytical files. Detailed proof, frozen execution journals and resource samples are retained in the organized parent validation folder.
