# Employee Attendance & Analytics API

A FastAPI service for employee attendance, manual regularization with a history trail, and MongoDB-backed attendance analytics.

## Run locally

Requires Python 3.11+ and MongoDB 6.0+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export MONGO_URI='mongodb://localhost:27017'
export MONGO_DB='attendance_db'
uvicorn app.main:app --port 8000
```

`GET /health` checks that MongoDB is reachable. Indexes are created automatically at startup. The API uses epoch milliseconds for instants and the attendance contract's IST calendar conventions for dates and shift times.

## Endpoints

- Employee creation and paginated employee listing
- Attendance punch-in, punch-out, listing, and manual corrections with an audit trail
- Employee monthly summaries, department summaries, late leaderboard, and department trend
- MongoDB execution-plan endpoint for the main analytics/list queries

Analytics are implemented with MongoDB aggregation pipelines. Review the code and test it against MongoDB 6.0+ before relying on it with a production dataset.
