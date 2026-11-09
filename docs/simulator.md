# Deterministic task adapter and local episode capture

This component runs a single explicit structured request in MuJoCo and publishes a local captured episode. It uses simulator state as a labeled reference controller; it does not claim image-based selection or general motion planning. Model outputs cannot enter this task input mode.

## Run

Initialize the repository's local environment as in the main README. Then:

```powershell
docker compose --profile simulation run --build --rm simulator run --object-id package_01 --destination-id zone_a --seed 7
```

The simulator is an optional multi-stage build target. It downloads the Apache-licensed Panda asset at pinned revision 98a70c569c4c1b6da467a20012f766ee1f8dc521 during the build. The model files and license are embedded in the resulting image. No host Python, GPU, database connection or external model server is required. Runtime network access is disabled. The image uses two CPUs, a 3 GiB memory limit and CPU OSMesa rendering.

Objects are stable handles `package_01`, `package_02`, `package_03`; appearances are currently red, green and blue respectively. Destinations are `zone_a` and `zone_b`. New invocations deliberately create new attempts/episode IDs, even with the same task and seed. There is no queue, background polling, automatic task retry or API command claim in this step.

To use the image's structured example file:

```powershell
docker compose --profile simulation run --rm simulator run --task-file examples/task.json
```

For a host task file, mount its directory read-only and provide its container path. The task input contract is CLI-specific: task_input_version, BASELINE_STRUCTURED mode, object_id, destination_id, seed and scenario. It is not the future API's episode-submission contract. Extra fields, unsupported modes or invalid types are rejected and recorded. Unknown object/destination handles are also recorded as rejected episodes without fictional images/actions.

## Observe failures and verify development coverage

```powershell
docker compose --profile simulation run --rm simulator run --scenario no_grasp
docker compose --profile simulation run --rm simulator suite
```

The first command intentionally keeps the fingers open and should return a failed-grasp episode and a nonzero exit status. That is an expected simulated failure, not a successful task. The 17-case suite expects twelve normal object/destination/seed combinations, one repeated baseline, the injected failure and three rejections; it returns success only when each observed result matches the declared expectation, artifact/event verification passes and repeated terminal measurements match. These are development cases, not the final frozen evaluation set.

## Capture contents

Each attempt is written beneath the Docker-managed capture_spool volume at `/capture/episodes/<episode_id>/`:

- attempt.json: initial control record, retained if a process dies before terminal capture.
- events/: canonical JSON envelopes with UUID identities, an episode-scoped producer session, contiguous sequences, UTC and simulation timestamps, causation references and event payloads.
- objects/sha256/: content-addressed image/configuration/request/reference-state/measurement bytes.
- episode.json: terminal outcome and capture summary.
- manifest.json: final local READY marker and hashes/sizes/references for verification.

The runner records actual camera observations at initial, pregrasp, closed, lifted and final stages where reached. Reference poses and measurements are explicitly tagged EVALUATION_REFERENCE. Package positions are initialized at reset and thereafter move via contact/friction; there are no object grasp welds or runtime object teleportation. Numerical IK solves on a separate probe state and supplies joint actuator targets to the physical simulation.

Successful PICK requires a measured lift above 14 cm. Successful PLACE checks the full rotated package footprint inside the 13 cm destination zone, final center height and settled linear speed. Raw source hashes, asset revision/XML hash, controller code hashes, dependency versions, scene seed/configuration and camera parameters are captured. A controller exception is recorded as CONTROLLER_ERROR, distinct from GRASP_FAILED and PLACEMENT_FAILED.

Capture readiness is not ingestion/completeness certification: summaries say ingestion PENDING, completeness OPEN and quality_assessed false. `verify` checks local hashes, event sequence/episode identity and terminal closure, but event-specific payload/reference/quality projection and receiver-side deduplication remain Step 7 work. File writes are flushed and atomically published on Linux. A process/host failure, full disk or partial capture can still leave an open/incomplete episode; restart reconciliation and power-loss guarantees are not claimed here. There is no global final frozen dataset or PostgreSQL episode projection yet.

## Inspect and export

```powershell
docker compose --profile simulation run --rm simulator list
docker compose --profile simulation run --rm simulator verify <episode-id>
New-Item -ItemType Directory -Force runtime
docker compose --profile simulation run --rm --volume "${PWD}/runtime:/export" simulator export <episode-id> --output-dir /export
```

Export preserves the captured bundle and adds convenient phase PNGs in the exported directory. It refuses to overwrite an existing episode export. Runtime is ignored by Git. The capture volume survives normal Compose shutdown. The suite report is stored at `/capture/suite-result.json`; each rerun replaces that report but preserves previously captured episode folders.

The API remains the foundation health/readiness service. This adapter does not create database command rows or claim that the future API queue/outbox is implemented. It records a local command identity for the explicitly invoked task. API ingestion and operational command authority will be integrated with their implementation steps rather than simulated by idle placeholder workers.
