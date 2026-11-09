# Implemented architecture

The platform treats an experiment as an auditable data product. Task outcome, evidence completeness, data quality, model validity and physical execution are separate fields rather than one success flag.

## Two producers, one evidence discipline

The simulator executes explicit BASELINE_STRUCTURED CLI requests offline in MuJoCo. A bounded Panda controller uses known simulator object/destination handles. Camera observations, action events, reference measurements, provenance and terminal outcomes form a finalized capture bundle. Inference is independent: an approved source image, instruction and public vocabulary produce a separate VLM_EVALUATION attempt with raw output or failure evidence. Proposal outcome is always NOT_EXECUTED, with execution eligibility false.

Neither producer depends on a command queue or API submission endpoint. There is no background robot polling, API claim/outbox or model-to-controller connection.

## Storage and ownership

| Volume | Writer | Readers / purpose |
| --- | --- | --- |
| capture_spool | Simulator | Ingestor; original episode bundles |
| proposal_spool | Offline model adapter | Proposal ingestor; attempt journals and model artifacts |
| raw_data | Data-only receivers | API, analytics and model source selection; admitted bytes and receipts |
| postgres_data | PostgreSQL transactions | API, receivers, analytics; identity/reference projections and registries |
| analytical_data | Analytics publisher | API and evaluator; frozen inputs, manifests and Parquet versions |
| evaluation_data | Evaluation publisher | API; immutable dataset-linked reports |
| model_cache | Explicit model preparation | Inference mounts it read-only |
| benchmark_data | Benchmark runner / publisher | Frozen intent, slot completion, assignments and summaries |
| prefect_data | Prefect | Local orchestration state |

The read-only Streamlit application calls FastAPI. It has no database credentials, artifact volume mounts or task-execution controls. FastAPI verifies retained bytes before serving artifact/report data. Service readiness checks the expected database migration head; migrations are explicit one-shot jobs.

## Intake and recovery

Receivers preserve admitted raw bytes and a receipt before projecting validated records into PostgreSQL. Unique identities, episode-scoped references and transactional projection prevent duplicate facts. Changed identities or invalid relationships become findings; failed/rejected robot outcomes can still have complete evidence. Raw-only replay reads the preserved receipts and cannot import or invoke a robot controller or model.

The system is a local batch pipeline with targeted recovery checks. It does not claim a distributed exactly-once protocol. Local locking, atomic publication, checksums and transactions cover specific process boundaries; they are not an exhaustive host power-loss guarantee.

## Analytical publication

Prefect orchestrates freezing accepted inputs and publishing datasets. A frozen snapshot includes source receipts, artifact hashes, quality state and explicit task assignments. PyArrow writes Parquet; DuckDB checks relationships and computes metrics. Baseline-only snapshots have seven tables. Inference snapshots add proposals, proposal_artifacts, proposal_validations, proposal_receipts and proposal_findings, for twelve tables.

Manifests contain file and logical-table checksums plus code/input identities. Publication promotes a complete version before registry registration; retries can register the same finalized version. Rebuilds read the frozen snapshot rather than the mutable current catalog, and compare logical rows and metrics. Baseline v1 behavior remains available for historical rebuilds.

## Evaluation boundaries

The fixed task definition predates benchmark execution. Assignments bind one retained attempt to each applicable case/seed/configuration slot. Schema validity, reference membership and expected-result matching are distinct checks. The public appearance-to-handle legend is disclosed model input. Evaluator targets and measured reference poses are excluded from provider requests.

The frozen split's identifier remains planned_evaluation for identity stability; dashboard coverage distinguishes unrun, partial and fully assigned reports. Corpus totals include earlier development attempts. Scores use declared assignments: 54 baseline and 90 proposal attempts, with 36 structured slots not applicable. Physical success belongs only to structured execution.

## Why these choices

- **Local artifact store + PostgreSQL:** images stay as content-addressed bytes while relational metadata supports inspection and joins. An object-store adapter would be a future extension.
- **Parquet + DuckDB:** portable analytical output and SQL checks without a permanent query cluster.
- **Prefect:** visible orchestration and retry history around data-only stages; it does not retry robot tasks.
- **Docker profiles and isolated environments:** simulator, model, orchestration and dashboard dependencies remain optional. The model runs offline with two CPUs and a 6 GiB limit.
- **Strict original-output retention:** malformed model responses remain evidence; no JSON repair or selective retry improves reported scores.

Open scale questions include continuous ingestion, multi-host coordination, object storage and larger task/scene coverage. The present evidence establishes a reproducible bounded local workflow, not production scale or general-purpose autonomy.
