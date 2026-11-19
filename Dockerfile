# Its own stage so that app-code changes do not recompile Rust, and so neither
# the toolchain nor Cargo's target/ tree reaches the runtime image. Only the
# wheel is carried forward.
FROM python:3.12-slim AS rust-build

WORKDIR /src

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"
RUN pip install --no-cache-dir maturin

COPY rust/seymour_sets/ ./rust/seymour_sets/
# --compatibility linux skips the manylinux audit: this wheel is installed into
# the same debian image it was built in and is never published.
RUN maturin build --release --compatibility linux \
    -m rust/seymour_sets/Cargo.toml --out /wheels


FROM python:3.12-slim AS base

WORKDIR /app

# Unbuffered so worker logs reach `docker service logs` as they happen.
ENV PYTHONUNBUFFERED=1

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# find_custom_sets falls back to a ~10x slower numpy path when this is absent,
# and the fallback is silent by design. See rust/seymour_sets/README.md.
COPY --from=rust-build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# Copy application code
COPY . .

# The colour tables are only exact against the cKDTree of the SciPy installed
# above, so they are built here rather than committed. See README.md.
RUN python scripts/precompute_hex_matches.py && python scripts/precompute_hex_order.py


# Auction sync. Must run at exactly one replica: the loop has no cross-process lock.
FROM base AS worker
CMD ["python", "-m", "app.worker"]


# Runs the suite against the pinned dependency set rather than whatever is on
# the developer's machine -- the pre_ping outage got through precisely because
# local SQLAlchemy was newer than the pin. See scripts/run_tests.sh.
FROM base AS test
RUN pip install --no-cache-dir -r requirements-dev.txt
CMD ["pytest"]


# Last stage is what `docker build` targets when no --target is passed, so the
# API app keeps building correctly with its build-stage field left blank.
FROM base AS api
EXPOSE 8000
# Without this, swarm calls the task ready as soon as PID 1 exists and traefik routes
# to it mid-boot -- a 502 on every deploy. python:slim has no curl/wget, hence urllib.
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
# --proxy-headers or every request looks like it came from traefik and the per-IP
# rate limits become one shared bucket. Trusting all forwarders is only safe
# because nothing reaches this container except through traefik.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
