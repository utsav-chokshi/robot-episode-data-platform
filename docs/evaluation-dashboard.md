# Evaluation and dashboard

Step 10 adds a read-only Streamlit workspace and immutable evaluation reports over frozen datasets. It does not execute a model or robot. The 90-slot benchmark is defined and registered as **planned / not run**; executing it is Step 11 work.

## Open the workspace

```powershell
docker compose --profile dashboard up --build -d --wait dashboard
```

Open http://localhost:8501. The five views are Overview, Evaluations, Episodes, Proposals and Data quality. The sidebar toggle is at the upper left; its collapse control appears when hovering over the sidebar. In narrow windows the sidebar overlays content until closed. The dashboard has a 1 GiB limit and its own hashed Streamlit 1.67.0 environment. It has only an API address: no database credentials, raw volumes, model weights or execution controls. API requests are read-only, bounded and cached for five seconds during interaction. Refresh evidence clears that cache.

Overview and evaluation use the selected immutable dataset. Episode/proposal explorers and receipt findings show live ingested records; this distinction is stated in the sidebar. Robot exploration includes original initial/final images, all captured frames, event order, action durations and artifact lineage. Proposal exploration displays original text as code, never executes it, separates invalid JSON from provider failures and retains absent timings as not recorded. Dataset hashes are checked by the API; failed evidence retrieval is displayed as a service/integrity error.

## Define and publish reports

Choose the dataset explicitly. The commands below use the latest published version; inspection of its provenance remains available in the UI.

```powershell
$dataset = (Invoke-RestMethod http://localhost:8010/api/v1/datasets).items[0].dataset_id

docker compose --profile evaluation run --rm evaluator python -m robotics_data_platform.evaluation.development --dataset-id $dataset

docker compose --profile evaluation run --rm evaluator python -m robotics_data_platform.evaluation.reports --dataset-id $dataset --task-set /evaluations/definitions/development-smoke-v1/task-set.json --assignments /evaluations/definitions/development-smoke-v1/assignments.json

docker compose --profile evaluation run --rm evaluator python -m robotics_data_platform.evaluation.reports --dataset-id $dataset --task-set /app/examples/evaluation/fixed-task-set.json --assignments /app/examples/evaluation/fixed-assignments.json
```

The evaluator is an ordinary data command, not a Prefect task that retries robot execution. It reads checksum-verified published tables and the frozen input snapshot; it never looks up later live attempts to fill gaps. No GPU or inference dependencies are required.

development-smoke-v1 is explicitly retrospective: seventeen predeclared structured cases plus three separately labeled VLM slots. Its baseline labels come from the frozen simulator suite definition, not passed flags. Its model targets are predefined from the red/yellow instructions, not generated text or provider status. Both red attempts, including the intentional timeout, remain included. It is an unpaired development cohort, not a held-out comparison. The helper rejects unexpected instructions or a larger model cohort instead of selecting successful examples. New cohorts require explicit versioned definitions/assignments.

The fixed tabletop-evaluation-v1 contains 18 supported, six ambiguous and six invalid/absent cases, each at seeds 17, 23 and 29: **90 applicable VLM slots** and **54 applicable structured-baseline slots**. Thirty-six language-only slots are NOT_APPLICABLE for the baseline. The public appearance/handle legend is disclosed model input. Seeds define repetition groups; they do not establish independent visual scenes or unseen generalization. The task-set reference and assignment hashes are frozen into each report before benchmark execution. Empty assignments deliberately remain empty; existing development data is not relabeled as the benchmark.

Input binding checks enforce the instruction's exact UTF-8 checksum, expected seed, source initial observation and structured request. One attempt cannot fill multiple slots within a configuration. Missing, ineligible, wrong-input, timeout, schema-invalid, unknown-reference and semantically wrong results remain separate categories. Schema validity is not semantic correctness; known handles do not establish grounding. A correct rejection/clarification must match the declared disposition and reason. A deliberately failed grasp can match its expected failure while still failing physical execution.

Every applicable slot remains in denominators, including missing attempts. VLM physical success is NOT_APPLICABLE. Do not combine physical execution with proposal correctness into one accuracy percentage. Ready/reject/clarify match subsets use the declared expected results; physical success has its own measured predicate. Worker-wall timing summaries include recorded failures/timeouts and have explicit sample counts; they are not completed-generation latency. Per-attempt load/preprocess/generation durations and hardware/configuration remain in the proposal explorer.

## Report integrity

Evaluation files live in evaluation_data/reports/<fingerprint>/: canonical task-set.json, assignments.json, result.json and their manifest. The fingerprint binds the dataset ID/manifest checksum, typed canonical task-set and assignment hashes, and evaluator code. Derived report IDs are deterministic UUIDv5 identities; retries reuse the same report. Files are flushed, atomically promoted and then registered in evaluation_runs. A failure after promotion can be recovered by repeating the exact command. Changed labels, assignments, dataset or evaluator code produce a different version; accepted reports are not overwritten. Prior reference/report versions remain in the registry.

GET /api/v1/evaluations lists registered versions; GET /api/v1/evaluations/{report_id} verifies report-file hashes. The API and dashboard mount evaluation data read-only. An abruptly incomplete publication is not listed until registration succeeds. These are local Docker/filesystem recovery guarantees, not a host power-loss or distributed transaction claim.

## Verify

```powershell
docker compose --profile test run --build --rm pipeline-tests
docker compose --profile dashboard run --no-deps --rm dashboard /opt/dashboard/bin/python -m pytest -q -p no:cacheprovider tests/test_dashboard.py
```

The first image runs core/evaluation/analytics tests; it intentionally omits Streamlit. The second uses Streamlit's [AppTest runtime](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest) to check all five views, planned coverage labels, verbatim invalid text, timeout handling and API outage messages. Its dependency lock is CPU Linux/amd64 CPython 3.12 specific. CI contains a separate dashboard job; no remote CI run is claimed.

Next: Step 11 executes the fixed benchmark, validates the complete workflow from a clean setup and records measured resource/performance limits. Do not describe the current planned report as an executed benchmark.
