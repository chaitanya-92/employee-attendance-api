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

# The five assignment questions

1. **Indexes.** I added a unique index on employee codes and a unique `(emp_code, date)` index for attendance, so duplicate employee/day records cannot slip in under concurrent requests. The other indexes support employee/date lookups, date-sorted attendance lists, and department/month filters. I'd check actual execution plans before adding more.
2. **Two identical punch-ins.** Both requests may reach the insert, but only one can satisfy the unique employee/date index. That request gets 201; MongoDB rejects the other insert and the API returns 409.
3. **A tie at the leaderboard cutoff.** The pipeline uses the MongoDB competition-rank operator, so equal totals share a rank and the next rank is skipped. Filtering by rank keeps every employee tied at a qualifying rank, then `emp_code` provides stable display order.
4. **Employees without logs.** The department summary starts from employees hired by month-end and looks up attendance. A missing lookup becomes zero-valued stats, so those employees still count in headcount; people hired later are excluded.
5. **At 100x the data.** I'd inspect execution stats and the workload first. If analytics become expensive, I'd consider pre-aggregated daily summaries and a measured refresh strategy instead of adding caching without evidence.

Authentication, authorization for regularization, and deployment hardening are outside this assignment's current API surface.
