# Experimental proposal contract v1

Status: Step 9 contract, offline model adapter, mode-specific inference ingestion and analytical proposal tables are implemented. See [proposal ingestion and datasets](proposal-ingestion.md) for the data-only receiver and rebuild commands. Existing baseline bundles and the original published Step 8 dataset remain unchanged. The provider remains experimental and execution-ineligible.

## Model response

Return exactly one UTF-8 JSON object, with no Markdown fences, prose, additional fields or generated code. Contract version 1.0.0 is recorded by the adapter outside the model output. Examples:

```json
{"status":"ready","object_id":"package_01","destination_id":"zone_a"}
```

```json
{"status":"clarify","reason_code":"OBJECT_AMBIGUOUS"}
```

```json
{"status":"reject","reason_code":"OBJECT_UNAVAILABLE"}
```

| Status | Required fields | Meaning |
|---|---|---|
| ready | object_id, destination_id | Propose the bounded PICK then PLACE intent; no joint values, coordinates, paths or commands |
| clarify | reason_code | OBJECT_AMBIGUOUS, DESTINATION_AMBIGUOUS or INSTRUCTION_AMBIGUOUS |
| reject | reason_code | OBJECT_UNAVAILABLE, DESTINATION_INVALID or UNSUPPORTED_INSTRUCTION |

Handles are lowercase identifiers, at most 64 characters. Parsing rejects duplicate keys, nonfinite JSON numbers, invalid UTF-8, unknown fields, incorrect types and responses above 1 MiB. It does not repair malformed output or extract a convenient JSON substring. A provider-specific vocabulary mapping would require a separately versioned adapter and preservation of both original and mapped output; no such adapter exists yet.

The generated JSON Schema is examples/proposals/proposal.schema.json. Python definitions and validation live in robotics_data_platform.planning.contracts.

## Validation result

- schema_valid: whether the response matches exactly one allowed shape.
- reference_valid: for ready only, whether both handles belong to the supplied versioned vocabulary; null for parse failures, reject and clarify.
- execution_eligible: always false, including structurally valid ready proposals.
- reason_codes: validator findings plus EXPERIMENTAL_MODE; separate from the model's reason_code.
- parsed_proposal: validated shape or null; never replaces original response evidence.
- validator_version: proposal-validator-v1.

Unknown handles produce OBJECT_REFERENCE_UNKNOWN or DESTINATION_REFERENCE_UNKNOWN. They are distinguishable from PLAN_SCHEMA_INVALID. RESPONSE_LIMIT_EXCEEDED is a parser admission finding. Reject/clarify being schema-valid does not establish a correct disposition. MODEL_TIMEOUT, MODEL_UNAVAILABLE and provider errors belong to attempt status, not model-authored rejection codes.

This validator checks syntax and identifier membership, not visual grounding, target presence, instruction compliance or physical success. A known handle can still refer to the wrong or absent object. Those judgments require separately retained evaluator labels in later evaluation.

## Adapter input and evidence boundary

The adapter accepts a captured observation image plus instruction and a versioned public handle/zone vocabulary. Reference poses, contact measurements, outcomes, expected dispositions, evaluation labels and baseline plans must not enter the provider request. A fixed vocabulary lists possible handles; it must not become a hidden ground-truth list of objects currently visible. Any public image-to-handle legend must be documented as model input and reflected in evaluation claims.

Retain a new proposal ID for each inference attempt, linked to its own VLM_EVALUATION episode and original source observation. Do not append model events to an already finalized baseline episode. Cross-episode image reuse needs explicit lineage and a locally registered artifact association, preserving the source identity/hash.

Before inference integration, implement evidence for: original image and instruction hashes; provider/model ID and immutable model revision; adapter/prompt/contract revisions; preprocessing and generation settings; CPU/device and dependency versions; monotonic load/preprocess/generation durations; UTC capture times; provider status; raw response bytes or bounded error evidence; parsed proposal and versioned validation result. Never invent a model response for a timeout. Preserve each retry as a distinct attempt; retries cannot execute robot tasks.

The earlier design proposed proposal.recorded/proposal.validated events. Step 9c instead validates the actual typed intent/manifest/ready protocol captured in Step 9b; it does not invent producer events after the fact. The dedicated inference receiver enforces NOT_EXECUTED closure and projects proposal/validation records. The baseline event receiver still accepts finalized baseline bundles only. See [the implemented data boundary](proposal-ingestion.md).

## Verification

Tests exercise extra executable fields, wrong types, hallucinated handles, missing fields, duplicate keys, Markdown wrappers, malformed UTF-8, nonfinite numbers, invalid disposition codes and oversized responses. They establish acceptance boundaries; they are not model performance tests. No model weights or new dependencies are required for this substep.

## Step 9b: offline inference and durable attempts

The first provider is the previously tested HuggingFaceTB/SmolVLM-256M-Instruct at revision 7e3e67edbbed1bf9888184d9df282b700a323964. It remains an experimental provider with known recognition/grounding failures. The Provider protocol separates inference from evidence capture so another adapter can be substituted without making model outputs executable.

The optional vlm image isolates Torch/Transformers in /opt/vlm; the main API/core environment does not import them. requirements-vlm.in records the exact tested dependency inventory; requirements-vlm.lock enforces hashes for Linux/amd64 CPython 3.12 CPU wheels. The image checks dependency closure with pip check. The lock is platform-specific; other architectures require a separately tested lock.

After the usual capture/ingestion setup:

```powershell
docker compose --profile vlm-prepare run --build --rm model-prepare

$source = 'YOUR_SOURCE_EPISODE_UUID'
$events = Invoke-RestMethod "http://localhost:8010/api/v1/episodes/$source/events"
$observation = $events.items | Where-Object event_type -eq 'observation.captured' | Select-Object -First 1

docker compose --profile vlm run --rm planner run --source-episode-id $source --observation-id $observation.payload.observation_id --instruction 'Move the red package to zone A.'
docker compose --profile vlm run --rm planner inspect PROPOSAL_UUID
```

Preparation needs network access and creates this project's own model_cache volume. It downloads only the pinned public revision and records file checksums in model-files.json. Runtime has network_mode:none, mounts models/raw evidence read-only and rechecks prepared model bytes before loading. No feasibility-project cache or image is required to reproduce a fresh setup. The local validation reused previously downloaded weights by copying them into the new cache before preparation.

Each inference child has two CPU threads, float32 eager attention, seed 7, image longest edge 512, image splitting disabled, greedy generation and an 80-new-token cap. The container has two CPUs and a 6 GiB memory limit. The parent enforces a default 120-second process deadline, configurable to a positive value at most 180 seconds. It kills the child process group on timeout. Output files are bounded by a 1 MiB per-file child limit; retained diagnostics are capped at 64 KiB. Instructions must fit within 4096 UTF-8 bytes; tokenized input is capped at 4096 tokens.

The fixed public vocabulary explicitly associates baseline red/green/blue appearances with package handles and lists zones A/B. This information is disclosed model input, not inferred localization. It lists possible handles regardless of current presence; it is unsuitable as an unchanged legend for recolored-object experiments. No current visible-object truth, poses or expected outcomes are passed to inference.

observation_source reads and validates preserved raw receipts, selects the requested observation, verifies its image and records source episode/observation/artifact IDs, receipt hash and source fingerprint. This step uses raw evidence rather than SQL projection. Every attempt gets a new proposal UUID and a separate VLM evaluation episode UUID. Image bytes are copied into the proposal store under a new local artifact association, with original lineage retained. Baseline episodes are never reopened.

proposal_spool contains a separate inference evidence format, **not a bundle accepted by the current receiver**:

- objects/sha256 holds input images, instruction, public vocabulary, prompt, provider configuration/code hashes, original response, validation and measurements as content-addressed bytes.
- attempts/<proposal-id>/attempt.json records durable RUNNING intent before inference.
- working retains the subprocess request, bounded logs and phase-progress evidence, including on interruption.
- manifest.json records completed capture, NOT_EXECUTED outcome, provider status, lineage and artifact bindings; ready.json publishes its checksum last.

Inspection requires the ready marker and verifies manifest/artifact hashes. A process/storage failure before ready publication leaves an explicit partial attempt, not a completed one. No automatic retry or partial-attempt cleanup occurs. Rerunning inference creates a distinct attempt. A worker error becomes MODEL_ERROR; missing runtime/model files become MODEL_UNAVAILABLE; the parent deadline becomes MODEL_TIMEOUT. These statuses are independent from a completed but malformed model response. Failure attempts have no fabricated response or validation record.

Recorded provenance includes intended model/revision, adapter/prompt/contract versions, code hashes, actual runtime dependency versions, prepared model-file-manifest checksum, CPU/Python architecture, rendered prompt, generated token IDs, token cap information and separate load/preprocess/generation durations. Parent wall time additionally includes process startup, imports, model-byte verification and teardown. Missing phase timings on timeout remain absent. Process peak RSS is not total container peak memory.

Step 9b tests add real child-process deadline enforcement, unique retry identity, raw-response preservation, failure/invalid-output distinctions, crash boundaries, manifest tampering, source-image corruption and bounded error recording. Step 9c adds transactional PostgreSQL ingestion and versioned Parquet/Prefect integration without executing or rerunning the model.
