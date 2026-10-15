# Design decisions

## Data and consistency

- `emp_code` is the client-facing employee identifier. MongoDB's `_id` stays an internal implementation detail.
- A unique index on `(emp_code, date)` is the final guard against two attendance records for one employee/day. Application-side checks alone would leave a race between concurrent requests.
- Manual regularization recalculates the derived values from the requested source fields and appends a history entry containing the actor, reason, timestamp and changed fields. A conditional update checks the previous values so a correction does not silently overwrite a concurrent change.
- Audit instants are serialized as epoch milliseconds to match the API contract, including timestamps nested inside history changes.

## Time handling

- Incoming instants are epoch milliseconds and are interpreted as UTC instants.
- Attendance dates and shift boundaries use IST. For an overnight shift, an early-morning punch belongs to the shift that started on the previous calendar day.
- Work hours and derived fields are calculated in one place so punch-out and regularization use the same rules.

## Analytics

- The analytics endpoints use MongoDB aggregation pipelines for filtering and rollups. This keeps aggregation close to the data and avoids loading a whole month's records into Python.
- The department summary starts from employees hired by the end of the selected month and looks up attendance records, so employees with no attendance rows still contribute to headcount.
- The leaderboard ranks by total late minutes. Equal totals share the same rank; employee code provides a stable display order after ranking.
- The trend pipeline builds a date series so a day with no attendance rows can still appear in the response.

## Trade-offs and follow-up

- Indexes target the lookups, uniqueness constraints and sort patterns used by the current endpoints. I'd validate their effectiveness with the explain endpoint on representative data before adding more indexes.
- For a much larger deployment, I'd consider pre-aggregated daily summaries after measuring the real query workload. I would not add a cache before identifying a measurable bottleneck.
- Authentication, authorization for regularization, and deployment settings would need to be added before production use.
