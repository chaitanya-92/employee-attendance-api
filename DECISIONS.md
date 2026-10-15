# Design decisions

1. **Indexes.** `employees.emp_code` is unique because client codes are the public identity. The unique `(emp_code, date)` attendance index both prevents duplicate daily records during concurrent punch-ins and supports employee/day lookups. Date/employee ordering supports attendance listing; employee/date/status and punch-in indexes support filtered lists and finding an open punch record. I kept these focused rather than indexing every field, which would add write cost without helping the specified queries.

2. **Punch-in race.** Both requests can pass the employee lookup, but only one insert can satisfy the unique `(emp_code, date)` index. MongoDB accepts that insert; the other gets a duplicate-key error that the API turns into 409. The duplicate check is therefore enforced by the database, not by a vulnerable pre-check.

3. **Ties.** The leaderboard uses `$rank`, then filters ranks at or below the requested limit. If several employees tie at the cutoff rank, all are returned. `emp_code` is only the secondary display order; it does not break the ranking tie.

4. **Headcount.** The department summary starts from employees hired by the end of the month and looks up that employee's attendance logs. Employees with no matching logs still survive the lookup and count toward headcount.

5. **At 100x scale.** I would examine real execution plans and workload first, then consider pre-aggregated daily summaries for analytics and a background refresh strategy. I would not introduce caches before measuring the database bottleneck.
