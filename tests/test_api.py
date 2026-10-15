from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app, db

UTC = timezone.utc


def ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def employee(code: str, department: str = "Engineering", joined_on: str = "2026-07-01"):
    return {
        "emp_code": code,
        "name": f"Employee {code}",
        "email": f"{code.lower()}@example.com",
        "department": department,
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": joined_on,
    }


def create_employee(client: TestClient, code: str, department: str = "Engineering", joined_on: str = "2026-07-01"):
    response = client.post("/employees", json=employee(code, department, joined_on))
    assert response.status_code == 201, response.text
    return response.json()


def reset_database():
    db.attendance_logs.delete_many({})
    db.employees.delete_many({})


def test_employee_create_conflict_and_filtered_pagination():
    with TestClient(app) as client:
        reset_database()
        create_employee(client, "EMP0001", "Engineering")
        create_employee(client, "EMP0002", "Support")
        create_employee(client, "EMP0003", "Engineering")

        duplicate = client.post("/employees", json=employee("EMP0001"))
        assert duplicate.status_code == 409

        response = client.get("/employees", params={"department": "Engineering", "page": 1, "page_size": 1})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 2
        assert payload["page"] == 1
        assert [item["emp_code"] for item in payload["items"]] == ["EMP0001"]


def test_punch_workflow_and_audit_trail():
    with TestClient(app) as client:
        reset_database()
        create_employee(client, "EMP0001", joined_on="2026-07-06")

        punched_in = client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001",
            "punched_at": ms("2026-07-06T04:10:00Z"),  # 09:40 IST
        })
        assert punched_in.status_code == 201, punched_in.text
        assert punched_in.json()["date"] == "2026-07-06"
        assert punched_in.json()["late_minutes"] == 0
        assert punched_in.json()["history"] == []

        punched_out = client.post("/attendance/punch-out", json={
            "emp_code": "EMP0001",
            "punched_at": ms("2026-07-06T13:30:00Z"),  # 19:00 IST
        })
        assert punched_out.status_code == 200, punched_out.text
        assert punched_out.json()["work_hours"] == 9.33
        assert punched_out.json()["overtime_minutes"] == 30

        corrected = client.patch("/attendance/EMP0001/2026-07-06", json={
            "status": "WFH",
            "reason": "Worked from home",
            "regularized_by": "manager",
        })
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["status"] == "WFH"
        assert len(corrected.json()["history"]) == 1
        assert set(corrected.json()["history"][0]["changes"]) == {"status"}


def test_monthly_and_department_summary_include_zero_log_employee():
    with TestClient(app) as client:
        reset_database()
        create_employee(client, "EMP0001", "Engineering", "2026-07-06")
        create_employee(client, "EMP0002", "Engineering", "2026-07-01")

        response = client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001",
            "punched_at": ms("2026-07-06T04:00:00Z"),
        })
        assert response.status_code == 201
        response = client.post("/attendance/punch-out", json={
            "emp_code": "EMP0001",
            "punched_at": ms("2026-07-06T13:00:00Z"),
        })
        assert response.status_code == 200

        monthly = client.get("/analytics/employees/EMP0001/monthly", params={"month": "2026-07"})
        assert monthly.status_code == 200, monthly.text
        monthly_json = monthly.json()
        assert monthly_json["working_days"] == 20
        assert monthly_json["present_days"] == 1
        assert monthly_json["attendance_pct"] == 5.0

        summary = client.get("/analytics/departments/summary", params={"month": "2026-07"})
        assert summary.status_code == 200, summary.text
        engineering = next(item for item in summary.json()["items"] if item["department"] == "Engineering")
        assert engineering["headcount"] == 2
        assert engineering["present_days"] == 1
        assert engineering["avg_work_hours"] == 9.0


def test_leaderboard_includes_all_employees_tied_at_cutoff():
    with TestClient(app) as client:
        reset_database()
        create_employee(client, "EMP0001", "Sales")
        create_employee(client, "EMP0002", "Sales")
        create_employee(client, "EMP0003", "Sales")
        db.attendance_logs.insert_many([
            {"emp_code": "EMP0001", "date": "2026-07-06", "status": "PRESENT", "late_minutes": 30},
            {"emp_code": "EMP0002", "date": "2026-07-06", "status": "PRESENT", "late_minutes": 30},
            {"emp_code": "EMP0003", "date": "2026-07-06", "status": "PRESENT", "late_minutes": 10},
        ])

        response = client.get("/analytics/leaderboard/late", params={"month": "2026-07", "limit": 1})
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert [item["emp_code"] for item in items] == ["EMP0001", "EMP0002"]
        assert [item["rank"] for item in items] == [1, 1]


def test_department_trend_returns_missing_days_and_moving_average():
    with TestClient(app) as client:
        reset_database()
        create_employee(client, "EMP0001", "Engineering", "2026-07-01")
        create_employee(client, "EMP0002", "Engineering", "2026-07-01")
        db.attendance_logs.insert_one({
            "emp_code": "EMP0001", "date": "2026-07-06", "status": "PRESENT",
            "half_day": False, "late_minutes": 0,
        })

        response = client.get(
            "/analytics/departments/Engineering/trend",
            params={"from": "2026-07-06", "to": "2026-07-10"},
        )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 5
        assert items[0]["date"] == "2026-07-06"
        assert items[0]["headcount"] == 2
        assert items[0]["present_count"] == 1
        assert items[0]["attendance_rate"] == 0.5
        assert items[1]["date"] == "2026-07-07"
        assert items[1]["present_count"] == 0
        assert items[1]["attendance_rate"] == 0.0
        assert items[1]["moving_avg_7d"] == 0.25
