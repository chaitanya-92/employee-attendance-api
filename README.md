# Employee Attendance & Analytics API

FastAPI service for employee attendance records, manual corrections with a history trail, and reporting backed by MongoDB aggregation pipelines.

## Requirements

- Python 3.11+
- MongoDB 6.0+
- Git (for the submission workflow)

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

export MONGO_URI='mongodb://localhost:27017'
export MONGO_DB='attendance_db'
uvicorn app.main:app --reload --port 8000
```

The app creates its indexes on startup. Keep credentials and local configuration out of Git; the defaults above are for local development only.

Open `http://127.0.0.1:8000/docs` to try the API. `GET /health` checks that MongoDB can be reached and returns 503 if the database ping fails.

## API areas

- `POST /employees` and `GET /employees`
- Attendance punch-in, punch-out, list and regularization endpoints
- Monthly employee attendance, department summaries, late leaderboard and daily department trend
- `GET /admin/explain/{endpoint}` for execution-plan information on supported list/analytics queries

The API uses epoch milliseconds for instants and YYYY-MM-DD strings for attendance days. Shift calculations use IST. Analytics run in MongoDB rather than loading the full attendance collection into application memory.

## Seed data

The kit includes sample JSON records and a `sample_seed.py` script. Review the script and the sample records before loading them into a database; do not run a seed script against a database containing data you need to keep.

## Verification

Install the dependencies, start MongoDB, and run `pytest -q`. The CI workflow runs the same tests against MongoDB. Pay particular attention to duplicate punch-ins, punch-out without an eligible record, regularization history, and analytics for a month with no records. The `openapi.yaml`, `DATA_MODEL.md`, seed script, and sample data are part of the candidate kit; keep a local copy of the kit for reference when running the project.

This repository should be treated as an assignment implementation, not a production-ready HR system. Authentication/authorization and deployment hardening are outside the current API surface.
