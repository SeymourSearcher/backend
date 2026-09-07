FROM python:3.12-slim AS base

WORKDIR /app

# Unbuffered so worker logs reach `docker service logs` as they happen.
ENV PYTHONUNBUFFERED=1

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .


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
