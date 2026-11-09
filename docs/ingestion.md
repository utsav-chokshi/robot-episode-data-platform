# Receiver, replay and read-only episode inspection

This component ingests finalized v1 baseline capture bundles into PostgreSQL. It preserves admitted source bytes before projection, validates typed envelopes/payloads and relationships, and stores outcomes independently of data quality. Rejected tasks and failed grasps can be complete data. No ingestion/replay module imports the simulator controller or exposes command execution.

## Run

After creating episodes with the simulator profile:

```powershell
docker compose up --build -d --wait api
docker compose --profile ingestion run --rm ingestor
docker compose --profile ingestion run --rm ingestor python -m robotics_data_platform.ingestion.cli counts
docker compose --profile ingestion run --rm ingestor python -m robotics_data_platform.ingestion.cli replay
```

Ingestion is currently an explicitly invoked batch process. It mounts capture_spool read-only and raw_data read/write. The API mounts raw_data read-only. The receiver image needs PostgreSQL and Pillow for image decoding, but has no MuJoCo/model runtime dependency. The simulator is not started by these commands.

`ingest` scans ready manifests. `replay` reads only saved raw receipts/content; it does not read original capture folders or dispatch tasks. Replaying creates neither new attempts nor replacement event identities. `reconcile` rescans ready bundles, reports stale unfinished captures and then replays receipts:

```powershell
docker compose --profile ingestion run --rm ingestor python -m robotics_data_platform.ingestion.cli reconcile --stale-after-seconds 300
```

An old capture without a final manifest is cataloged as incomplete with unknown execution outcome; elapsed time alone does not prove a robot failure. Its available attempt/events/object bytes are retained. A subsequently completed bundle can resolve pending references. An accepted completed episode's changed identity/content set is a conflict, not a silent revision. Dataset/seal revision management remains later work.

## Validation and storage

The receiver validates schema version, UUID identity, UTC producer timestamps, finite JSON values, strict payload fields, event/stream identities, causal references, nondecreasing simulation time, terminal closure, summary consistency and episode-scoped artifact/action references. PNGs are decoded and checked against observation and camera dimensions. Reference-state and measurement artifacts must carry EVALUATION_REFERENCE access class; images must carry OBSERVATION. Unknown event versions/types or extra payload fields are not silently coerced or discarded.

PostgreSQL tables contain episodes, immutable event bindings, artifact metadata, observations, actions, outcomes, source receipts and findings. Composite foreign keys prevent an observation/action from referencing another episode's evidence. Original event/manifest/artifact bytes remain in the managed raw filesystem. An event's captured_at_utc remains producer time; received_at_utc is the first successful metadata-ingestion time, not a reconstructed simulation clock. Processing/replay does not rewrite producer bytes or timestamps.

Raw storage layout:

- objects/sha256/<prefix>/<hash>: immutable content bytes, deduplicated independently of observation/episode identities.
- receipts/<receipt-hash>.json: immutable source snapshot with actual received file hashes, original manifest hash and source findings.
- statuses/<receipt-hash>.json: latest processing acknowledgment, independently replaceable.
- last-run.json: most recent batch summary; export it before another run if retaining that summary.

Receipt identity includes actual received bytes/missing-file state. A corrupted second delivery cannot overwrite the first receipt just because its declared manifest is unchanged. The receiver never silently overwrites an existing content object that fails its address hash.

Admission bounds: 1 MiB manifest, 256 KiB event, 8 MiB artifact, at most 1000 events and 1000 artifact declarations, and decoded images up to four million pixels with bounded dimensions. Oversized inputs receive diagnostic receipts; their oversized original bytes remain in the source spool rather than being claimed as fully copied. The low-space threshold stops raw admission and retains pending source data. These limits do not grant automatic cleanup or permission to delete old captures.

## Duplicate, conflict and recovery behavior

- Identical event IDs/stream sequences/artifact bindings are idempotent; repeated delivery does not add logical records.
- Changed bytes under an accepted event ID, reused producer sequences or altered artifact/episode identity bindings are retained as conflicts. Previously accepted history is preserved.
- Malformed/unsupported inputs, corrupt images and invalid references are preserved with inspectable findings. Missing dependencies can remain PENDING_REFERENCES. A corrected complete snapshot can be accepted without editing the earlier receipt.
- Files are durably promoted before the database transaction. A failure after raw preservation can be recovered by replay. A failure after database commit but before acknowledgment is safely handled as a duplicate on replay.
- Database outages leave durable PENDING_DB acknowledgments. No successful database-ingestion acknowledgment is invented.
- Episode projection is transactional. A late FK/identity failure rolls back the whole attempted projection; the rejected source and finding remain available.
- A Linux filesystem lock serializes one raw-store writer; a PostgreSQL transaction advisory lock and unique constraints protect logical identities. This is a single-host local design, not distributed exactly-once delivery.
- Raw-object corruption found during replay marks data corrupt without changing the recorded task outcome. Restoration must provide verified original bytes; a successful recheck can restore completeness and resolve the integrity finding. The receiver does not overwrite corruption automatically.

Historical quarantine/conflict receipts remain immutable. A replay can continue to report a historical rejected receipt even after a newer snapshot resolves the episode. Receipt state, finding resolution and current accepted episode state are separate concepts. Non-success batch results return a nonzero CLI status; they are not robot retry instructions.

## Inspect the data

The API at localhost:8010/docs now exposes read-only routes:

- GET /api/v1/episodes and /episodes/{id}.
- GET /api/v1/episodes/{id}/events, ordered by producer session and sequence, not a fabricated global event-time order.
- GET /api/v1/ingestion/counts and /ingestion/findings.
- GET /api/v1/artifacts/{id}, returning only bytes that match registered hash and size.

The top-level episode ingestion/completeness fields describe receiver assessment. Nested source_summary preserves the producer's earlier OPEN/PENDING claims and is not the current receiver state. The API does not add task submission, a command queue, model tools or robot control. Authentication/remote deployment is not claimed for this localhost development stack.

## Verification and limits

The integration suite injects failures at durable boundaries, uses a real isolated PostgreSQL service, checks three repeated deliveries, concurrent delivery, raw-only replay after removing the original capture path, conflicts, malformed payloads, corrupt/missing images, raw corruption/restoration, late references, partial captures, cross-episode FK rejection and mid-projection rollback. These are injected process-boundary exceptions and database failures, not an exhaustive host power-loss or filesystem certification.

The current receiver supports finalized Step 6 baseline capture bundles and provisional incomplete-capture catalog entries. It does not provide per-event streaming intake, model-proposal contracts, API command leasing/outbox publication, immutable dataset versions, Prefect orchestration or analytical exports yet. No retention/deletion job is enabled. Back up both raw_data and PostgreSQL; operational command recovery is not inferred from data replay.
