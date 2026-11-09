# Development

Python 3.12 is the supported interpreter for this foundation. Keep application modules under `src/robotics_data_platform/`, migrations under `migrations/`, and meaningful tests under `tests/`. Runtime data, local environment files, downloaded weights and generated artifacts stay outside Git.

`pyproject.toml` pins direct requirements. `requirements.lock` pins resolved runtime/test requirements with hashes. Build tools are pinned in `requirements-build.txt` and pyproject build metadata. Use the lock in containers and CI. Regenerate it deliberately with pip-tools 7.6.2, then rebuild and rerun the checks; do not update dependencies implicitly while adding a feature.

Container images are pinned by digest. The application container runs as a non-root user. PostgreSQL's local bootstrap owner is intended for this isolated development stack; do not present it as a production deployment configuration. Future runtime/migration/worker roles can be separated with the domain tables.

Tests marked integration require `RDP_TEST_DATABASE_URL` for a dedicated PostgreSQL test service. The fixture creates and removes only its exact randomly named `rdp_test_<uuid>` databases. The Compose test profile supplies that isolated service automatically. Do not point the test setting at production or a shared external server.

CI runs the same locked Python requirements against a dedicated PostgreSQL service, verifies migration/readiness behavior and runs the test suite. A local passing run is not evidence of a remote CI run; report those separately.

Step 6 introduces the simulator adapter. Durable ingestion, domain contracts, Prefect flows and analytical exports remain later work. Do not add idle placeholder services that suggest those capabilities are operating.

## Regenerating the lock deliberately

Run pip-tools 7.6.2 under Python 3.12 in a container with the repository mounted at its working directory:

```text
pip-compile --generate-hashes --resolver=backtracking --extra=test --output-file=requirements.lock pyproject.toml
```

This can select newer transitive dependencies; review the resulting changes and rerun the foundation checks. Dependency updates are separate from ordinary feature work. The resolver container's own tools are not application runtime dependencies.

## Optional simulator development

The app Docker target keeps simulation imports optional. The simulator target adds MuJoCo, NumPy, Pillow, CPU OSMesa libraries and the pinned Panda asset; runtime networking is disabled. Use Compose's simulation profile rather than assuming a plain Docker build selects the API target.

requirements-simulation.lock was generated with pip-tools 7.6.2 under Python 3.12, with test and simulation extras, hashes, and requirements.lock as a constraint. Core dependency versions remain unchanged. Re-resolve intentionally and rerun both core tests and the development simulator suite after relevant changes. The OS package repositories remain live; the build is not a hermetic byte-identical build.

The workflow now contains both the PostgreSQL/core-test job and a CPU simulator development-suite job. Both have been exercised locally through their corresponding Docker commands. They have not run on GitHub yet.

## Receiver development and testing

Pillow is now a core requirement for decoding/validating captured image bytes. The core hashed lock was resolved with the existing core/simulator locks as constraints, preserving prior dependency versions. The simulator lock already includes that same Pillow version. Core tests import no MuJoCo runtime; synthetic image/action fixtures test contracts and transactions, not robot performance.

Run the core test profile for migration, API, capture and ingestion tests. Receiver integration fixtures create unique temporary databases and never use the application database. Raw-store tests use temporary directories. Fault injection is implemented through a receiver checkpoint callback at raw-preserved and DB-committed boundaries; the production CLI does not expose a robot retry or execution callback.

Readiness requires migration 0002_ingestion after this change. Apply migrations via the existing one-shot service before starting the API/receiver. Keep accepted event/artifact bytes immutable; do not repair a receiver problem by editing raw history or deleting rejected attempts.
