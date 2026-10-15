# Starter review

I reviewed the starter before extending it. The main issues I found and addressed are below.

| # | Where | What is wrong | How to notice | How it was fixed |
|---|---|---|---|---|
| 1 | `health` | Returned success without checking MongoDB. | Stop MongoDB and call `/health`. | Ping MongoDB and return 503 if the ping fails. |
| 2 | `compute_late_minutes` | Used naive local time and didn't account for IST or overnight shifts. | Try an early-morning punch for a 22:00–06:00 shift. | Use aware UTC instants and resolve the shift boundary in IST. |
| 3 | `compute_work_hours` | Python's float rounding can use ties-to-even instead of half-up. | Test a duration at a half-cent rounding boundary. | Use `Decimal` with `ROUND_HALF_UP`. |
| 4 | `compute_overtime` | Always treated shift end as the same calendar date. | Punch out after midnight for an overnight shift. | Calculate the proper overnight shift-end instant. |
| 5 | Employee creation | Checked for duplicates separately from insertion, allowing a race; creation timestamp was naive. | Submit duplicate codes concurrently. | Enforce uniqueness in MongoDB and translate duplicate-key errors to 409; store aware UTC timestamps. |
| 6 | Employee model | Did not validate the code, email, date, shift formats or equal shift start/end. | Submit malformed values or identical shift times. | Add schema validation and reject equal start/end values. |
| 7 | `list_employees` | Used `page * page_size` as the offset and counted all employees rather than filtered employees. | Request page 1 or filter by department. | Use `(page - 1) * page_size` and count the same filter. |
| 8 | `list_employees` | Returned database order rather than the contract's employee-code order and didn't bound pagination. | Insert out-of-order employees or request page size over 100. | Sort by `emp_code`; validate page and page size. |
| 9 | `punch_in` | Did not handle unknown employees before dereferencing them. | Punch in with an unknown code. | Return 404. |
| 10 | `punch_in` | Used system-local `datetime.fromtimestamp`, so the attendance date could vary by host timezone. | Run with a non-IST machine timezone. | Interpret API epoch milliseconds as UTC and derive attendance date in IST. |
| 11 | `punch_in` | Duplicate prevention used check-then-insert and could create duplicate records concurrently. | Send parallel punch-ins. | Enforce unique `(emp_code, date)` index and handle duplicate-key conflicts. |
| 12 | `punch_in` | Did not validate the allowed presence statuses or epoch timestamp format. | Try ABSENT or seconds instead of milliseconds. | Validate presence statuses and timestamp values. |
| 13 | `list_attendance` | Read the entire attendance collection and sorted/paginated in Python, which does not scale. | Use a large collection and observe memory/query cost. | Apply filters, sort, count, skip and limit in MongoDB. |
| 14 | `list_attendance` | Had no date-range or status validation and did not enforce the page-size cap. | Reverse date bounds or send an invalid status. | Validate date ordering/status and pagination. |
| 15 | Response serialization | MongoDB `_id` values and datetime objects were not consistently converted to the public contract. | Inspect responses from seeded records and nested history. | Keep `_id` internal and serialize instants as epoch milliseconds, including audit changes. |
| 16 | Startup | No required indexes were created. | Inspect indexes in a fresh database or race writes. | Create unique and query-supporting indexes idempotently at startup. |
| 17 | Remaining routes | Punch-out, correction/audit, analytics, and explain routes were absent. | Compare routes with `openapi.yaml`. | Implement the remaining contract routes and their MongoDB pipelines. |

## Reviewed and kept

- `emp_code` remains the public identity; MongoDB `_id` is internal.
- Analytics reads stored derived attendance fields, rather than recalculating them from punch times.
- Manual-correction history records only actual field changes. Punch-in and punch-out do not add history entries.

This review is based on the starter code and contract; behaviour still needs to be verified against a running MongoDB instance and the evaluator's larger dataset.
