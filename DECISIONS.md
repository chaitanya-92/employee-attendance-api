# Design decisions

1. **Indexes:** `emp_code` is unique, and `(emp_code, date)` prevents duplicate daily attendance during concurrent punch-ins. Other indexes support common employee/date filters and sorting; I would check execution stats before adding more.

2. **Duplicate punch-ins:** both requests can reach the insert, but the unique index allows only one. The winner returns 201; the duplicate-key error becomes 409.

3. **Leaderboard ties:** MongoDB competition ranking gives equal totals the same rank and skips the next rank. Filtering by rank keeps everyone tied at the cutoff; `emp_code` provides stable display order.

4. **Employees without logs:** the department summary starts with employees hired by month-end and looks up attendance. Employees with no matching rows still count toward headcount; employees hired later are excluded.

5. **100x data:** I would inspect execution stats and query patterns first. If analytics become expensive, I would measure whether daily pre-aggregated summaries help before introducing caching.

Regularization recalculates derived fields and records actual changes with a reason and actor. Authentication and authorization would be needed before production use.
