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


# Last stage is what `docker build` targets when no --target is passed, so the
# API app keeps building correctly with its build-stage field left blank.
FROM base AS api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
