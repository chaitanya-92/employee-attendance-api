# Code review notes

I reviewed the supplied starter code and the endpoint contract while working through this implementation. These are the main issues and checks I focused on.

| Area | Risk in the starter or behavior to verify | What the implementation does |
|---|---|---|
| Readiness | A health check that does not contact the database can report ready while MongoDB is down. | `/health` pings MongoDB and returns 503 on a database error. |
| Employee creation | Checking uniqueness in Python is not enough when requests arrive at the same time. | The database unique index is authoritative; duplicate employee codes return 409. |
| Employee input | Invalid codes, dates, emails and shift times should fail at the API boundary. | Pydantic validates the request and the join date; equal shift start/end times are rejected. |
| Pagination | Incorrect offsets or inconsistent counts return confusing pages. | Lists use `(page - 1) * page_size`, matching filters for counts, and bounded page sizes. |
| Time zones | Host-local timestamps can change attendance dates between machines. | Instants are parsed as UTC and shift/day calculations use IST. |
| Overnight shifts | The calendar date after midnight can still belong to the prior shift. | Attendance-date and shift-boundary helpers account for overnight shifts. |
| Duplicate attendance | Application check-then-insert logic can race. | The unique `(emp_code, date)` index prevents duplicate daily records. |
| Punch-out | A completed record must not be mistaken for the currently open punch. | The lookup selects an open record and uses a conditional update to prevent double punch-out. |
| Regularization | A correction can erase context or overwrite a concurrent edit. | Changed fields are recorded in history and the update checks the prior record values. |
| Serialization | Database datetimes and `_id` should not leak into the public contract. | Public instants are converted to epoch milliseconds and `_id` is omitted. |
| Analytics | Application-side loops can become expensive on larger collections. | Reporting uses aggregation pipelines in MongoDB. |
| Query visibility | It is difficult to judge whether the intended indexes are being used. | The explain route exposes execution-plan data for the supported queries. |

## Important verification still required

I have not treated code inspection as proof that every evaluator case passes. The API should be run against MongoDB 6.0+ and checked against `openapi.yaml`, especially for empty-month responses, leaderboard cut-off ties, attendance trends on missing dates, and boundary conditions around overnight shifts. The department trend pipeline and all analytics results should be verified on the supplied sample data before submission.

## Existing design choices retained

- `emp_code` is the public identity; MongoDB `_id` is internal.
- Derived fields are recalculated on punch-out and correction instead of being accepted directly from clients.
- Regularization history records only fields that actually changed.
