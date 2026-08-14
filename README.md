# AI Root Cause Analysis Platform

An MVP implementation of the platform described in the project overview PDF. It detects unusual metric movement, ranks likely dimensional drivers, and presents the findings in business language.

## Stack

- Frontend: Next.js, React, TypeScript
- Backend: FastAPI, Pydantic
- Data services: PostgreSQL-ready configuration, pandas/polars/duckdb dependencies
- RCA engine: baseline vs comparison anomaly scoring and driver contribution ranking
- Deployment: Docker Compose with frontend, backend, PostgreSQL, and nginx

## Repository Structure

```text
backend/
  app/
    api/
    core/
    schemas/
    services/
frontend/
  app/
  components/
  lib/
  types/
docker/
  nginx/
plans/
```

## Run With Docker

```bash
docker compose up --build
```

Then open:

- Frontend: http://localhost:3000
- Backend health: http://localhost:8000/health
- Nginx gateway: http://localhost:8080

## Run Locally

Backend:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## API

`POST /api/investigations`

Accepts a metric name, baseline period points, comparison period points, and a list of dimensions. Returns:

- anomaly severity
- baseline and comparison averages
- absolute and percent change
- top contributing drivers
- plain-language summary
- recommended next actions
