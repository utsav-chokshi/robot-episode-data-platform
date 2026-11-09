FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock requirements-build.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && pip install --no-cache-dir -r requirements-build.txt
COPY pyproject.toml README.md alembic.ini ./
COPY migrations/ ./migrations/
COPY src/ ./src/
COPY tests/ ./tests/
COPY examples/ ./examples/
RUN pip install --no-cache-dir --no-deps --no-build-isolation . && useradd --uid 10001 --create-home app && install -d -o 10001 -g 10001 /raw /datasets /evaluations /benchmark
USER app
CMD ["uvicorn", "robotics_data_platform.api.factory:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

FROM app AS dashboard
USER root
COPY requirements-dashboard.lock ./
RUN python -m venv /opt/dashboard && /opt/dashboard/bin/pip install --no-cache-dir --require-hashes -r requirements-dashboard.lock && /opt/dashboard/bin/pip check
COPY .streamlit/ ./.streamlit/
ENV PYTHONPATH=/app/src
USER app
CMD ["/opt/dashboard/bin/streamlit", "run", "src/robotics_data_platform/dashboard/app.py", "--server.address=0.0.0.0", "--server.port=8501"]

FROM app AS vlm
USER root
COPY requirements-vlm.lock ./
RUN python -m venv /opt/vlm && /opt/vlm/bin/pip install --no-cache-dir --require-hashes -r requirements-vlm.lock && /opt/vlm/bin/pip check && install -d -o 10001 -g 10001 /models /proposals
COPY examples/ ./examples/
ENV HF_HOME=/models/cache HF_HUB_DISABLE_XET=1 HF_HUB_DISABLE_TELEMETRY=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 PYTHONPATH=/app/src
USER app
ENTRYPOINT ["python", "-m", "robotics_data_platform.planning.cli"]

FROM app AS analytics
USER root
COPY requirements-pipelines.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements-pipelines.lock
COPY examples/ ./examples/
USER app
CMD ["python", "-m", "robotics_data_platform.analytics.cli", "freeze"]

FROM analytics AS pipelines
USER root
COPY requirements-orchestration.lock ./
RUN python -m venv /opt/prefect && /opt/prefect/bin/pip install --no-cache-dir --require-hashes -r requirements-orchestration.lock && install -d -o 10001 -g 10001 /prefect
ENV PREFECT_HOME=/prefect PREFECT_SERVER_ANALYTICS_ENABLED=false PYTHONPATH=/app/src
USER app
CMD ["/opt/prefect/bin/python", "-m", "robotics_data_platform.analytics.flow"]

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS robot-assets
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git init /assets && cd /assets && git sparse-checkout init --cone && git sparse-checkout set franka_emika_panda && git remote add origin https://github.com/google-deepmind/mujoco_menagerie.git && git fetch --depth 1 --filter=blob:none origin 98a70c569c4c1b6da467a20012f766ee1f8dc521 && git checkout --detach FETCH_HEAD

FROM app AS simulator
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libosmesa6 libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY requirements-simulation.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements-simulation.lock && install -d -o 10001 -g 10001 /capture
COPY --from=robot-assets /assets/franka_emika_panda/ /opt/robot-assets/franka_emika_panda/
COPY examples/ ./examples/
ENV MUJOCO_GL=osmesa RDP_CAPTURE_ROOT=/capture RDP_ROBOT_ASSET_ROOT=/opt/robot-assets/franka_emika_panda
USER app
ENTRYPOINT ["python", "-m", "robotics_data_platform.simulator.cli"]
CMD ["run"]
