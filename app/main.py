"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import re
import calendar
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")
client = MongoClient(MONGO_URI, tz_aware=True, serverSelectionTimeoutMS=3000)
db = client[MONGO_DB]
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")
PRESENT_STATUSES = ("PRESENT", "WFH", "ON_DUTY")
ALL_STATUSES = (*PRESENT_STATUSES, "ABSENT", "LEAVE")


@app.on_event("startup")
def ensure_indexes():
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_employee_code")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING), ("emp_code", ASCENDING)], name="department_joined_code")
    db.employees.create_index([("joined_on", ASCENDING)], name="employee_joined_on")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_employee_date")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="attendance_date_employee")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING), ("status", ASCENDING)], name="attendance_employee_date_status")
    db.attendance_logs.create_index([("punch_in", DESCENDING)], name="attendance_punch_in")
    db.attendance_logs.create_index([("date", ASCENDING), ("late_minutes", DESCENDING)], name="attendance_date_late")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def from_epoch_ms(value: int) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or not 100_000_000_000 <= value <= 4_102_444_800_000:
        raise HTTPException(422, "timestamps must be integer epoch milliseconds")
    return datetime.fromtimestamp(value // 1000, UTC)


def to_epoch_ms(value: Optional[datetime]):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def serialize_doc(doc: dict) -> dict:
    """Convert Mongo documents into the public contract's JSON representation."""
    doc = dict(doc)
    doc.pop("_id", None)
    for key in ("created_at", "punch_in", "punch_out"):
        if key in doc and isinstance(doc[key], datetime):
            doc[key] = to_epoch_ms(doc[key])

    # Normalize both sides of audit changes before returning the record. This
    # also handles older history entries where MongoDB stored a datetime value.
    history = []
    for entry in doc.get("history") or []:
        e = dict(entry)
        if isinstance(e.get("at"), datetime):
            e["at"] = to_epoch_ms(e["at"])
        changes = {}
        for field, values in (e.get("changes") or {}).items():
            v = dict(values)
            # Store audit timestamps as epoch milliseconds, just like the main record.
            if field in ("punch_in", "punch_out"):
                for side in ("from", "to"):
                    if isinstance(v.get(side), datetime):
                        v[side] = to_epoch_ms(v[side])
            changes[field] = v
        e["changes"] = changes
        history.append(e)
    if "history" in doc or history:
        doc["history"] = history
    doc.setdefault("history", [])
    doc.setdefault("punch_in", None)
    doc.setdefault("punch_out", None)
    doc.setdefault("work_hours", None)
    doc.setdefault("late_minutes", 0)
    doc.setdefault("overtime_minutes", 0)
    doc.setdefault("half_day", False)
    return doc


def ist_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(IST)


def shift_start_for_punch(punch_in: datetime, shift_start: str, shift_end: str) -> tuple[datetime, datetime]:
    local = ist_datetime(punch_in)
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    shift_start_dt = datetime.combine(local.date(), time(sh, sm), IST)
    shift_end_date = local.date()
    if (eh, em) <= (sh, sm):
        if (local.hour, local.minute) < (eh, em):
            shift_start_dt -= timedelta(days=1)
            shift_end_date -= timedelta(days=1)
        shift_end_dt = datetime.combine(shift_end_date + timedelta(days=1), time(eh, em), IST)
    else:
        shift_end_dt = datetime.combine(shift_end_date, time(eh, em), IST)
    return shift_start_dt, shift_end_dt


def attendance_date(punch_in: datetime, employee: dict) -> str:
    local = ist_datetime(punch_in)
    sh, sm = map(int, employee["shift_start"].split(":"))
    eh, em = map(int, employee["shift_end"].split(":"))
    day = local.date()
    if (eh, em) <= (sh, sm) and (local.hour, local.minute) < (eh, em):
        day -= timedelta(days=1)
    return day.isoformat()


def compute_late_minutes(punch_in: datetime, shift_start: str, shift_end: str = "18:30") -> int:
    if punch_in is None:
        return 0
    start, _ = shift_start_for_punch(punch_in, shift_start, shift_end)
    elapsed = int((punch_in - start.astimezone(UTC)).total_seconds() // 60)
    return elapsed if punch_in - start.astimezone(UTC) > timedelta(minutes=10) else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = int((punch_out - punch_in).total_seconds())
    return float((Decimal(seconds) / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out: datetime, shift_start: str, shift_end: str, date_str: str, punch_in: datetime = None) -> int:
    if punch_out is None:
        return 0
    if punch_in is not None:
        _, end = shift_start_for_punch(punch_in, shift_start, shift_end)
    else:
        start_day = date.fromisoformat(date_str)
        eh, em = map(int, shift_end.split(":"))
        sh, sm = map(int, shift_start.split(":"))
        end_day = start_day + timedelta(days=1) if (eh, em) <= (sh, sm) else start_day
        end = datetime.combine(end_day, time(eh, em), IST)
    minutes = int((punch_out - end.astimezone(UTC)).total_seconds() // 60)
    return minutes if minutes >= 30 else 0


def recalculate(status: str, punch_in: Optional[datetime], punch_out: Optional[datetime], employee: dict, day: str) -> dict:
    if status in ("ABSENT", "LEAVE"):
        return {"status": status, "punch_in": None, "punch_out": None, "work_hours": None,
                "late_minutes": 0, "overtime_minutes": 0, "half_day": False}
    late = compute_late_minutes(punch_in, employee["shift_start"], employee["shift_end"]) if punch_in else 0
    hours = compute_work_hours(punch_in, punch_out) if punch_in and punch_out else None
    overtime = compute_overtime(punch_out, employee["shift_start"], employee["shift_end"], day, punch_in) if punch_in and punch_out else 0
    return {"status": status, "punch_in": punch_in, "punch_out": punch_out, "work_hours": hours,
            "late_minutes": late, "overtime_minutes": overtime, "half_day": hours is not None and hours < 4.5}


def month_bounds(month: str) -> tuple[str, str, date, date]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "month must be YYYY-MM")
    year, mon = map(int, month.split("-"))
    first = date(year, mon, 1)
    last = date(year, mon, calendar.monthrange(year, mon)[1])
    return first.isoformat(), last.isoformat(), first, last


def weekdays_between(start: date, end: date) -> int:
    if end < start:
        return 0
    total = 0
    current = start
    while current <= end:
        total += current.weekday() < 5
        current += timedelta(days=1)
    return total


def api_validation_date(value: str, label: str = "date") -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise HTTPException(422, f"{label} must be YYYY-MM-DD")


def json_safe(value):
    if isinstance(value, datetime):
        return to_epoch_ms(value)
    if value.__class__.__name__ == "ObjectId":
        return str(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items() if k != "_id"}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def valid_join_date(cls, value):
        api_validation_date(value, "joined_on")
        return value

    @model_validator(mode="after")
    def different_shift_times(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def valid_timestamp(cls, value):
        if value is not None:
            from_epoch_ms(value)
        return value


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[StrictInt] = None

    @field_validator("punched_at")
    @classmethod
    def valid_timestamp(cls, value):
        if value is not None:
            from_epoch_ms(value)
        return value


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def reject_null_status(self):
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be null")
        return self

    @field_validator("punch_in", "punch_out")
    @classmethod
    def valid_timestamp(cls, value):
        if value is not None:
            from_epoch_ms(value)
        return value


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
    except PyMongoError:
        raise HTTPException(503, "MongoDB is unavailable")
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = now_utc()
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return serialize_doc(doc)


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {"department": department} if department else {}
    total = db.employees.count_documents(q)
    items = [serialize_doc(d) for d in db.employees.find(q, {"_id": 0}).sort("emp_code", ASCENDING).skip((page - 1) * page_size).limit(page_size)]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code}, {"_id": 0})
    if not emp:
        raise HTTPException(404, "employee not found")
    ts = from_epoch_ms(body.punched_at) if body.punched_at is not None else now_utc()
    ts = ts.replace(microsecond=0)
    day = attendance_date(ts, emp)
    doc = {"emp_code": body.emp_code, "date": day, "status": body.status, "punch_in": ts,
           "punch_out": None, "work_hours": None, "late_minutes": compute_late_minutes(ts, emp["shift_start"], emp["shift_end"]),
           "overtime_minutes": 0, "half_day": False, "history": []}
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return serialize_doc(doc)


@app.get("/attendance")
def list_attendance(emp_code: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                    status: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    for value, label in ((date_from, "date_from"), (date_to, "date_to")):
        if value is not None:
            api_validation_date(value, label)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be on or before date_to")
    if status is not None and status not in ALL_STATUSES:
        raise HTTPException(422, "invalid status")
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    total = db.attendance_logs.count_documents(q)
    items = [serialize_doc(d) for d in db.attendance_logs.find(q, {"_id": 0}).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip((page - 1) * page_size).limit(page_size)]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code}, {"_id": 0})
    if not emp:
        raise HTTPException(404, "employee not found")
    ts = from_epoch_ms(body.punched_at) if body.punched_at is not None else now_utc()
    ts = ts.replace(microsecond=0)
    # Ignore records already closed when finding the active punch-in. Otherwise,
    # a second punch-out could pick yesterday's newest completed record and mask
    # an older open record for an overnight shift.
    rec = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": ts},
         "punch_out": None, "status": {"$in": PRESENT_STATUSES}},
        sort=[("punch_in", DESCENDING)],
    )
    if not rec:
        future = db.attendance_logs.find_one(
            {"emp_code": body.emp_code, "punch_in": {"$gt": ts}, "punch_out": None, "status": {"$in": PRESENT_STATUSES}},
            sort=[("punch_in", ASCENDING)],
        )
        if future:
            raise HTTPException(422, "punch-out must be after punch-in and within 24 hours")
        raise HTTPException(404, "no punch-in found")
    if ts <= rec["punch_in"] or ts - rec["punch_in"] > timedelta(hours=24):
        raise HTTPException(422, "punch-out must be after punch-in and within 24 hours")
    fields = recalculate(rec["status"], rec["punch_in"], ts, emp, rec["date"])
    updated = db.attendance_logs.find_one_and_update(
        {"_id": rec["_id"], "punch_out": None}, {"$set": {k: fields[k] for k in ("punch_out", "work_hours", "overtime_minutes", "half_day")}},
        return_document=ReturnDocument.AFTER)
    if updated is None:
        raise HTTPException(409, "record is already punched out")
    return serialize_doc(updated)


@app.patch("/attendance/{emp_code}/{day}")
def regularize_attendance(emp_code: str, day: str, body: RegularizeIn):
    api_validation_date(day, "date")
    emp = db.employees.find_one({"emp_code": emp_code}, {"_id": 0})
    if not emp:
        raise HTTPException(404, "employee not found")
    current = db.attendance_logs.find_one({"emp_code": emp_code, "date": day})
    if not current:
        raise HTTPException(404, "attendance record not found")
    desired = body.model_dump(exclude_unset=True)
    status = desired.get("status", current.get("status"))
    punch_in = from_epoch_ms(desired["punch_in"]) if "punch_in" in desired and desired["punch_in"] is not None else (None if "punch_in" in desired else current.get("punch_in"))
    punch_out = from_epoch_ms(desired["punch_out"]) if "punch_out" in desired and desired["punch_out"] is not None else (None if "punch_out" in desired else current.get("punch_out"))
    if status in ("ABSENT", "LEAVE") and (("punch_in" in desired and desired["punch_in"] is not None) or ("punch_out" in desired and desired["punch_out"] is not None)):
        raise HTTPException(422, "ABSENT and LEAVE cannot include punch times")
    if status in ("ABSENT", "LEAVE"):
        punch_in = punch_out = None
    elif punch_in is None:
        raise HTTPException(422, "presence status requires punch_in")
    if punch_in is not None and attendance_date(punch_in, emp) != day:
        raise HTTPException(422, "punch_in must belong to the record attendance date")
    if punch_out is not None and (punch_in is None or punch_out <= punch_in or punch_out - punch_in > timedelta(hours=24)):
        raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
    values = recalculate(status, punch_in, punch_out, emp, day)
    tracked = ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day")
    changes = {}
    for field in tracked:
        before = current.get(field, False if field == "half_day" else 0 if field in ("late_minutes", "overtime_minutes") else None)
        after = values[field]
        if before != after:
            changes[field] = {"from": before, "to": after}
    if not changes:
        raise HTTPException(422, "request does not change the record")
    history_entry = {"at": now_utc(), "by": body.regularized_by, "reason": body.reason, "changes": changes}
    condition = {"_id": current["_id"]}
    for field in tracked:
        condition[field] = current[field] if field in current else {"$exists": False}
    updated = db.attendance_logs.find_one_and_update(condition, {"$set": values, "$push": {"history": history_entry}}, return_document=ReturnDocument.AFTER)
    if updated is None:
        raise HTTPException(409, "record changed concurrently; retry with the latest values")
    return serialize_doc(updated)


# --------------------------------------------------------------------------- #
# Analytics pipelines
# --------------------------------------------------------------------------- #
def employee_monthly_pipeline(emp_code: str, month: str):
    start, end, _, _ = month_bounds(month)
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": start, "$lte": end}}},
        {"$set": {"_weekday": {"$dayOfWeek": {"date": {"$dateFromString": {"dateString": "$date"}}, "timezone": "+05:30"}}}},
        {"$group": {"_id": None,
            "present_days": {"$sum": {"$cond": [{"$and": [{"$in": ["$status", list(PRESENT_STATUSES)]}, {"$lte": ["$_weekday", 6]}, {"$gte": ["$_weekday", 2]}]}, {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]}, 0]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}}},
        {"$project": {"_id": 0, "present_days": 1, "leave_days": 1, "late_count": 1, "total_late_minutes": 1, "total_overtime_minutes": 1}},
    ]


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    emp = db.employees.find_one({"emp_code": emp_code}, {"_id": 0})
    if not emp:
        raise HTTPException(404, "employee not found")
    _, _, first, last = month_bounds(month)
    working_days = weekdays_between(max(first, date.fromisoformat(emp["joined_on"])), last)
    result = list(db.attendance_logs.aggregate(employee_monthly_pipeline(emp_code, month)))
    sums = result[0] if result else {"present_days": 0, "leave_days": 0, "late_count": 0, "total_late_minutes": 0, "total_overtime_minutes": 0}
    present = float(sums.get("present_days", 0))
    pct = float(Decimal(str(present / working_days * 100)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)) if working_days else None
    return {"emp_code": emp_code, "month": month, "working_days": working_days, **sums, "attendance_pct": pct}


def department_summary_pipeline(month: str, department: Optional[str] = None):
    start, end, _, _ = month_bounds(month)
    employee_match = {"joined_on": {"$lte": end}}
    if department is not None:
        employee_match["department"] = department
    return [
        {"$match": employee_match},
        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code", "joined": "$joined_on"}, "pipeline": [
            {"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$code"]}, {"$gte": ["$date", start]}, {"$lte": ["$date", end]}, {"$gte": ["$date", "$$joined"]}]}}},
            {"$set": {"_weekday": {"$dayOfWeek": {"date": {"$dateFromString": {"dateString": "$date"}}, "timezone": "+05:30"}}}},
            {"$group": {"_id": None,
                "present_days": {"$sum": {"$cond": [{"$and": [{"$in": ["$status", list(PRESENT_STATUSES)]}, {"$lte": ["$_weekday", 6]}, {"$gte": ["$_weekday", 2]}]}, {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]}, 0]}},
                "work_sum": {"$sum": {"$cond": [{"$and": [{"$in": ["$status", list(PRESENT_STATUSES)]}, {"$ne": [{"$ifNull": ["$work_hours", None]}, None]}]}, "$work_hours", 0]}},
                "work_count": {"$sum": {"$cond": [{"$and": [{"$in": ["$status", list(PRESENT_STATUSES)]}, {"$ne": [{"$ifNull": ["$work_hours", None]}, None]}]}, 1, 0]}},
                "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "leave_count": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}}}},
        ], "as": "stats"}},
        {"$set": {"stats": {"$ifNull": [{"$arrayElemAt": ["$stats", 0]}, {"present_days": 0, "work_sum": 0, "work_count": 0, "late_count": 0, "total_late_minutes": 0, "leave_count": 0, "on_duty_count": 0}]}}},
        {"$group": {"_id": "$department", "headcount": {"$sum": 1},
            "present_days": {"$sum": "$stats.present_days"}, "work_sum": {"$sum": "$stats.work_sum"}, "work_count": {"$sum": "$stats.work_count"},
            "late_count": {"$sum": "$stats.late_count"}, "total_late_minutes": {"$sum": "$stats.total_late_minutes"},
            "leave_count": {"$sum": "$stats.leave_count"}, "on_duty_count": {"$sum": "$stats.on_duty_count"}}},
        {"$project": {"_id": 0, "department": "$_id", "headcount": 1, "present_days": 1,
            "avg_work_hours": {"$cond": [{"$gt": ["$work_count", 0]}, {"$divide": [{"$floor": {"$add": [{"$multiply": [{"$divide": ["$work_sum", "$work_count"]}, 100]}, 0.5]}}, 100]}, None]},
            "late_count": 1, "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1}},
        {"$sort": {"department": 1}},
    ]


@app.get("/analytics/departments/summary")
def department_summary(month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), department: Optional[str] = None):
    month_bounds(month)
    return {"month": month, "items": list(db.employees.aggregate(department_summary_pipeline(month, department), allowDiskUse=True))}


def late_leaderboard_pipeline(month: str, limit: int, department: Optional[str] = None):
    start, end, _, _ = month_bounds(month)
    stages = [
        {"$match": {"date": {"$gte": start, "$lte": end}, "late_minutes": {"$gt": 0}}},
        {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}, "late_count": {"$sum": 1}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        stages.append({"$match": {"employee.department": department}})
    stages += [
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$project": {"_id": 0, "rank": 1, "emp_code": "$_id", "name": "$employee.name", "department": "$employee.department", "total_late_minutes": 1, "late_count": 1}},
    ]
    return stages


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):
    month_bounds(month)
    items = list(db.attendance_logs.aggregate(late_leaderboard_pipeline(month, limit, department), allowDiskUse=True))
    return {"month": month, "items": items}


def department_trend_pipeline(department: str, start: str, end: str):
    """Build a daily series in MongoDB, including days with no attendance documents."""
    start_dt = datetime.combine(date.fromisoformat(start), time.min, UTC)
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    return [
        {"$match": {"department": department}},
        {"$set": {"_days": {"$map": {"input": {"$range": [0, days]}, "as": "offset",
            "in": {"$dateToString": {"date": {"$dateAdd": {"startDate": start_dt, "unit": "day", "amount": "$$offset"}}, "format": "%Y-%m-%d", "timezone": "UTC"}}}}}},
        {"$unwind": "$_days"},
        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code", "day": "$_days"}, "pipeline": [
            {"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$code"]}, {"$eq": ["$date", "$$day"]}]}}},
            {"$project": {"_id": 0, "status": 1, "half_day": 1, "late_minutes": 1}}
        ], "as": "_day_logs"}},
        {"$unwind": {"path": "$_day_logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {"_id": "$_days",
            "headcount": {"$sum": {"$cond": [{"$lte": ["$joined_on", "$_days"]}, 1, 0]}},
            "present_count": {"$sum": {"$cond": [{"$and": [{"$lte": ["$joined_on", "$_days"]}, {"$in": ["$_day_logs.status", list(PRESENT_STATUSES)]}]}, {"$cond": [{"$ifNull": ["$_day_logs.half_day", False]}, 0.5, 1]}, 0]}},
            "late_count": {"$sum": {"$cond": [{"$and": [{"$lte": ["$joined_on", "$_days"]}, {"$gt": [{"$ifNull": ["$_day_logs.late_minutes", 0]}, 0]}]}, 1, 0]}}}},
        {"$project": {"_id": 0, "date": "$_id", "headcount": 1, "present_count": 1, "late_count": 1,
            "_dow": {"$dayOfWeek": {"$dateFromString": {"dateString": "$_id"}}}}},
        {"$set": {"is_working_day": {"$and": [{"$gte": ["$_dow", 2]}, {"$lte": ["$_dow", 6]}]},
            "attendance_rate": {"$cond": [{"$and": [{"$gte": ["$_dow", 2]}, {"$lte": ["$_dow", 6]}, {"$gt": ["$headcount", 0]}]},
                {"$divide": [{"$floor": {"$add": [{"$multiply": [{"$divide": ["$present_count", "$headcount"]}, 10000]}, 0.5]}}, 10000]}, None]}}},
        {"$setWindowFields": {"sortBy": {"date": 1}, "output": {"_moving_avg_raw": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}}}},
        {"$set": {"moving_avg_7d": {"$cond": [{"$ne": ["$_moving_avg_raw", None]},
            {"$divide": [{"$floor": {"$add": [{"$multiply": ["$_moving_avg_raw", 10000]}, 0.5]}}, 10000]}, None]}}},
        {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1, "present_count": 1, "late_count": 1, "attendance_rate": 1, "moving_avg_7d": 1}},
        {"$sort": {"date": 1}},
    ]


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_: str = Query(..., alias="from"), to: str = Query(...)):
    start_date = api_validation_date(from_, "from")
    end_date = api_validation_date(to, "to")
    if end_date < start_date:
        raise HTTPException(422, "to must be on or after from")
    if (end_date - start_date).days + 1 > 92:
        raise HTTPException(422, "date range cannot exceed 92 days")
    if not db.employees.find_one({"department": department}, {"_id": 1}):
        raise HTTPException(404, "department not found")
    items = list(db.employees.aggregate(department_trend_pipeline(department, from_, to), allowDiskUse=True))
    return {"department": department, "items": items}


# --------------------------------------------------------------------------- #
# Explain endpoint - use the same filters and pipelines as the corresponding API.
# --------------------------------------------------------------------------- #
def json_safe_explain(value):
    if isinstance(value, datetime):
        return to_epoch_ms(value)
    if isinstance(value, dict):
        return {key: json_safe_explain(item) for key, item in value.items() if key != "_id"}
    if isinstance(value, list):
        return [json_safe_explain(item) for item in value]
    if value.__class__.__name__ == "ObjectId":
        return str(value)
    return value


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str, emp_code: Optional[str] = None, month: Optional[str] = None,
                     department: Optional[str] = None, limit: int = Query(10, ge=1, le=50),
                     date_from: Optional[str] = None, date_to: Optional[str] = None, status: Optional[str] = None,
                     from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                     page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    if endpoint == "attendance_list":
        for value, label in ((date_from, "date_from"), (date_to, "date_to")):
            if value is not None:
                api_validation_date(value, label)
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "date_from must be on or before date_to")
        q = {}
        if emp_code:
            q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to
        if status:
            if status not in ALL_STATUSES:
                raise HTTPException(422, "invalid status")
            q["status"] = status
        command = {"find": "attendance_logs", "filter": q, "sort": {"date": -1, "emp_code": 1}, "skip": (page - 1) * page_size, "limit": page_size}
        col = "attendance_logs"
    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month are required")
        month_bounds(month)
        command = {"aggregate": "attendance_logs", "pipeline": employee_monthly_pipeline(emp_code, month), "cursor": {}}
        col = "attendance_logs"
    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month is required")
        month_bounds(month)
        command = {"aggregate": "employees", "pipeline": department_summary_pipeline(month, department), "cursor": {}}
        col = "employees"
    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required")
        month_bounds(month)
        command = {"aggregate": "attendance_logs", "pipeline": late_leaderboard_pipeline(month, limit, department), "cursor": {}}
        col = "attendance_logs"
    elif endpoint == "department_trend":
        if not department or not from_ or not to:
            raise HTTPException(422, "department, from and to are required")
        start_date, end_date = api_validation_date(from_, "from"), api_validation_date(to, "to")
        if end_date < start_date or (end_date - start_date).days + 1 > 92:
            raise HTTPException(422, "invalid trend date range")
        command = {"aggregate": "employees", "pipeline": department_trend_pipeline(department, from_, to), "cursor": {}}
        col = "employees"
    else:
        raise HTTPException(422, "unknown endpoint")
    try:
        result = db.command({"explain": command, "verbosity": "executionStats"})
    except (PyMongoError, ValueError) as exc:
        raise HTTPException(422, f"could not explain query: {exc}")
    return {"endpoint": endpoint, "collection": col, "explain": json_safe_explain(result)}
