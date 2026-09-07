import gc
import json
import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import (
    clear_session,
    current_user,
    hash_password,
    require_staff,
    require_student,
    require_supervisor,
    require_user,
    start_session,
    verify_password,
)
from app.briefs import load_assignment_text, load_rubric_text
from app.config import (
    ALLOWED_UPLOAD_EXTENSIONS,
    ARCHIVE_DIR,
    COURSE_UPLOAD_EXTENSIONS,
    DISPLAY_TZ_OFFSET,
    REPORT_DIR,
    RUBRIC_DIR,
    SESSION_COOKIE,
    SLIDES_DIR,
    STATIC_DIR,
    STUDENT_DIR,
    TEXTBOOK_DIR,
    list_available_providers,
)
from app.db import (
    YANGON,
    assignment_label,
    create_assignment,
    create_user,
    get_assignment,
    get_primary_report,
    get_report,
    get_report_by_pdf,
    get_setting,
    get_submission,
    get_submission_by_stored,
    get_user,
    get_user_by_email,
    init_db,
    insert_report,
    insert_submission,
    iso,
    list_assignments,
    list_reports,
    list_system_log,
    list_submissions,
    list_users,
    public_user,
    report_count,
    set_setting,
    submission_count,
    system_log_count,
    update_assignment,
    update_report,
    update_submission,
    update_user,
    user_count,
    utcnow,
)
from app.files_util import archive_file, list_files, zip_selected
from app.grader import find_submission_date, parse_student_filename
from app.grading_service import grade_submission
from app.integrity import verify_pdf
from app.job_queue import grading_queue
from app.rag import get_index, get_index_status, initialize_index_background, rebuild_layer
from app.report import generate_report
from app.record_archive import archive_report_record, archive_submission_record
from app.reports_persist import finalize_report, persist_generated_report, write_result_json
from app.submission import extract_submission
from app.word_limits import evaluate_word_count

app = FastAPI(title="Educator Assessment Automation")


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    """Avoid stale nav/CSS after UI renames (Settings → System Log, etc.)."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response

os.makedirs(STUDENT_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)
os.makedirs(SLIDES_DIR, exist_ok=True)
os.makedirs(TEXTBOOK_DIR, exist_ok=True)
os.makedirs(RUBRIC_DIR, exist_ok=True)


class GradeRequest(BaseModel):
    filename: str
    provider: Optional[str] = None
    model: Optional[str] = Field(default=None)
    compare: bool = False


class BatchGradeRequest(BaseModel):
    filenames: List[str]
    provider: Optional[str] = None
    model: Optional[str] = Field(default=None)
    compare: bool = False


class SetupRequest(BaseModel):
    email: str
    password: str
    full_name: str = "Supervisor"


class LoginRequest(BaseModel):
    email: str
    password: str


class AssignmentCreateRequest(BaseModel):
    title: str
    batch_name: str
    local_deadline: Optional[str] = None


class AssignmentPatchRequest(BaseModel):
    title: Optional[str] = None
    batch_name: Optional[str] = None
    local_deadline: Optional[str] = None
    is_active: Optional[bool] = None


class UserCreateRequest(BaseModel):
    email: str
    password: str
    full_name: str
    role: str = "student"
    can_self_grade: bool = False
    assignment_id: Optional[int] = None


class UserPatchRequest(BaseModel):
    email: Optional[str] = None
    is_active: Optional[bool] = None
    can_self_grade: Optional[bool] = None
    password: Optional[str] = None
    full_name: Optional[str] = None
    assignment_id: Optional[int] = None


class DeadlineRequest(BaseModel):
    local_datetime: Optional[str] = None


class ZipRequest(BaseModel):
    kind: str
    filenames: List[str]


class SubmissionMapRequest(BaseModel):
    student_user_id: int


class ReportMapRequest(BaseModel):
    student_user_id: int


class ArchiveRequest(BaseModel):
    filename: str
    confirm: bool = False


class RecordArchiveRequest(BaseModel):
    reason: str
    confirm: bool = False


class ReviewSaveRequest(BaseModel):
    grading_result: dict


def _page(name: str) -> FileResponse:
    path = os.path.join(STATIC_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Page missing.")
    return FileResponse(path)


def _gate_html(request: Request):
    if user_count() == 0:
        return RedirectResponse("/setup", status_code=302)
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return user


def _staff_html(request: Request, name: str):
    gated = _gate_html(request)
    if isinstance(gated, RedirectResponse):
        return gated
    if gated["role"] == "student":
        return RedirectResponse("/student", status_code=302)
    return _page(name)


def _supervisor_html(request: Request, name: str):
    gated = _gate_html(request)
    if isinstance(gated, RedirectResponse):
        return gated
    if gated["role"] != "supervisor":
        return RedirectResponse("/dashboard" if gated["role"] != "student" else "/student", status_code=302)
    return _page(name)


def _student_assess_html(request: Request, name: str):
    gated = _gate_html(request)
    if isinstance(gated, RedirectResponse):
        return gated
    if gated["role"] != "student":
        return RedirectResponse("/dashboard", status_code=302)
    if not gated.get("can_self_grade"):
        return RedirectResponse("/student", status_code=302)
    return _page(name)


@app.on_event("startup")
async def startup_event():
    init_db()
    initialize_index_background()


@app.get("/")
async def root(request: Request):
    if user_count() == 0:
        return RedirectResponse("/setup", status_code=302)
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if user["role"] == "student":
        return RedirectResponse("/student", status_code=302)
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/setup")
async def setup_page():
    if user_count() > 0:
        return RedirectResponse("/login", status_code=302)
    return _page("setup.html")


@app.get("/login")
async def login_page(request: Request):
    if user_count() == 0:
        return RedirectResponse("/setup", status_code=302)
    if current_user(request):
        return RedirectResponse("/", status_code=302)
    return _page("login.html")


@app.get("/dashboard")
async def dashboard_page(request: Request):
    return _staff_html(request, "dashboard.html")


@app.get("/assess")
async def assess_page(request: Request):
    return _staff_html(request, "index.html")


@app.get("/jobs")
async def jobs_page(request: Request):
    return _staff_html(request, "jobs.html")


@app.get("/files")
async def files_page(request: Request):
    return _staff_html(request, "files.html")


@app.get("/review")
async def review_page(request: Request):
    return _staff_html(request, "review.html")


@app.get("/accounts")
async def accounts_page(request: Request):
    return _supervisor_html(request, "accounts.html")


@app.get("/settings")
async def settings_page(request: Request):
    return _supervisor_html(request, "settings.html")


@app.get("/student")
async def student_page(request: Request):
    return _student_html(request, "student.html")


@app.get("/student/assess")
async def student_assess_page(request: Request):
    return _student_assess_html(request, "student-assess.html")


@app.get("/student/submit")
async def student_submit_redirect(request: Request):
    gated = _gate_html(request)
    if isinstance(gated, RedirectResponse):
        return gated
    if gated["role"] != "student":
        return RedirectResponse("/dashboard", status_code=302)
    return RedirectResponse("/student", status_code=302)


def _student_html(request: Request, name: str):
    gated = _gate_html(request)
    if isinstance(gated, RedirectResponse):
        return gated
    if gated["role"] != "student":
        return RedirectResponse("/dashboard", status_code=302)
    return _page(name)


@app.get("/report")
async def report_page(request: Request):
    return _staff_html(request, "report.html")


def _parse_local_datetime(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        naive = datetime.strptime(value[:16], "%Y-%m-%dT%H:%M")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Use datetime as YYYY-MM-DDTHH:MM in UTC+6:30.") from exc
    aware = naive.replace(tzinfo=YANGON)
    return iso(aware)


def _deadline_payload(deadline_utc: Optional[str]):
    if not deadline_utc:
        return {"deadline_utc": None, "deadline_local": None, "deadline_input": None, "timezone": DISPLAY_TZ_OFFSET, "is_open": True}
    try:
        dt = datetime.fromisoformat(deadline_utc.replace("Z", "+00:00"))
    except ValueError:
        return {"deadline_utc": deadline_utc, "deadline_local": None, "deadline_input": None, "timezone": DISPLAY_TZ_OFFSET, "is_open": True}
    local = dt.astimezone(YANGON)
    return {
        "deadline_utc": iso(dt),
        "deadline_local": local.strftime("%d-%m-%Y %H:%M"),
        "deadline_input": local.strftime("%Y-%m-%dT%H:%M"),
        "timezone": DISPLAY_TZ_OFFSET,
        "is_open": utcnow() < dt.astimezone(timezone.utc),
    }


def _assignment_view(assignment: dict) -> dict:
    return {
        **assignment,
        "label": f"{assignment.get('title', '').strip()} / {assignment.get('batch_name', '').strip()}".strip(" /"),
        **_deadline_payload(assignment.get("submission_deadline_at")),
    }


def _user_deadline(user: dict) -> dict:
    if user.get("submission_deadline_at"):
        return _deadline_payload(user.get("submission_deadline_at"))
    return _deadline_payload(get_setting("assignment_deadline_at"))


def _assignment_open(user: dict) -> bool:
    return _user_deadline(user).get("is_open", True)


@app.post("/api/setup")
async def api_setup(payload: SetupRequest, response: Response):
    if user_count() > 0:
        raise HTTPException(status_code=400, detail="Setup already completed.")
    email = payload.email.strip().lower()
    if "@" not in email or len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Use a valid email and a password of at least 8 characters.")
    user = create_user(email, hash_password(payload.password), "supervisor", payload.full_name or "Supervisor")
    start_session(response, user["id"])
    return {"user": public_user(user)}


@app.post("/api/login")
async def api_login(payload: LoginRequest, response: Response):
    user = get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="This account is deactivated.")
    start_session(response, user["id"])
    return {"user": public_user(user)}


@app.post("/api/logout")
async def api_logout(request: Request, response: Response):
    clear_session(response, request.cookies.get(SESSION_COOKIE))
    return {"ok": True}


@app.get("/api/me")
async def api_me(user=Depends(require_user)):
    return public_user(user)


@app.get("/api/assignments")
async def api_list_assignments(search: str = "", active_only: bool = False, user=Depends(require_staff)):
    return {"assignments": [_assignment_view(row) for row in list_assignments(active_only=active_only, search=search)]}


@app.post("/api/assignments")
async def api_create_assignment(payload: AssignmentCreateRequest, user=Depends(require_staff)):
    if not payload.title.strip() or not payload.batch_name.strip():
        raise HTTPException(status_code=400, detail="Assignment title and batch name are required.")
    assignment = create_assignment(
        payload.title.strip(),
        payload.batch_name.strip(),
        _parse_local_datetime(payload.local_deadline),
        user["id"],
    )
    return {"assignment": _assignment_view(assignment)}


@app.patch("/api/assignments/{assignment_id}")
async def api_patch_assignment(assignment_id: int, payload: AssignmentPatchRequest, user=Depends(require_staff)):
    current = get_assignment(assignment_id)
    if not current:
        raise HTTPException(status_code=404, detail="Assignment not found.")
    provided = getattr(payload, "model_fields_set", set())
    deadline = current.get("submission_deadline_at")
    if "local_deadline" in provided:
        deadline = _parse_local_datetime(payload.local_deadline)
    updated = update_assignment(
        assignment_id,
        title=payload.title.strip() if payload.title is not None else current["title"],
        batch_name=payload.batch_name.strip() if payload.batch_name is not None else current["batch_name"],
        submission_deadline_at=deadline,
        is_active=current["is_active"] if payload.is_active is None else payload.is_active,
    )
    return {"assignment": _assignment_view(updated)}


@app.get("/api/dashboard")
async def api_dashboard(search: str = "", user=Depends(require_staff)):
    counts = grading_queue.status_counts()
    recent = list_reports(limit=10, search=search)
    return {
        "submissions": submission_count(),
        "reports": report_count(),
        "published_reports": report_count("published"),
        "failed_reports": report_count("failed"),
        "jobs": counts,
        "index": get_index_status(),
        "recent": recent,
        "assignments": [_assignment_view(row) for row in list_assignments()],
        "user": public_user(user),
    }


@app.get("/api/users")
async def api_list_users(
    role: Optional[str] = None,
    search: str = "",
    batch: Optional[str] = None,
    user=Depends(require_supervisor),
):
    assignment_id = None
    unassigned_only = False
    if batch and batch not in ("all", ""):
        assignment_id, unassigned_only = _assignment_filter(batch)
    return {
        "users": list_users(
            role=role,
            search=search,
            assignment_id=assignment_id,
            unassigned_only=unassigned_only,
        ),
        "assignments": [_assignment_view(row) for row in list_assignments(active_only=True)],
    }


@app.post("/api/users")
async def api_create_user(payload: UserCreateRequest, user=Depends(require_supervisor)):
    if payload.role not in ("student", "educator", "supervisor"):
        raise HTTPException(status_code=400, detail="Role must be student, educator, or supervisor.")
    if get_user_by_email(payload.email):
        raise HTTPException(status_code=400, detail="Email already exists.")
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    created = create_user(
        payload.email.strip().lower(),
        hash_password(payload.password),
        payload.role,
        payload.full_name,
        can_self_grade=payload.can_self_grade if payload.role == "student" else False,
        assignment_id=payload.assignment_id if payload.role == "student" else None,
    )
    return {"user": public_user(created)}


@app.patch("/api/users/{user_id}")
async def api_patch_user(user_id: int, payload: UserPatchRequest, user=Depends(require_supervisor)):
    provided = getattr(payload, "model_fields_set", set())
    fields = {}
    if "email" in provided:
        email = payload.email.strip().lower()
        if "@" not in email:
            raise HTTPException(status_code=400, detail="Use a valid email.")
        existing = get_user_by_email(email)
        if existing and existing["id"] != user_id:
            raise HTTPException(status_code=400, detail="Email already exists.")
        fields["email"] = email
    if payload.is_active is not None:
        fields["is_active"] = payload.is_active
    if payload.can_self_grade is not None:
        fields["can_self_grade"] = payload.can_self_grade
    if "full_name" in provided:
        fields["full_name"] = payload.full_name.strip()
    if "assignment_id" in provided:
        fields["assignment_id"] = payload.assignment_id
    if payload.password:
        if len(payload.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
        fields["password_hash"] = hash_password(payload.password)
    updated = update_user(user_id, **fields)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"user": public_user(updated)}


@app.get("/api/settings/deadline")
async def api_get_deadline(user=Depends(require_supervisor)):
    return _deadline_payload(get_setting("assignment_deadline_at"))


@app.put("/api/settings/deadline")
async def api_put_deadline(payload: DeadlineRequest, user=Depends(require_supervisor)):
    set_setting("assignment_deadline_at", _parse_local_datetime(payload.local_datetime) or "")
    return _deadline_payload(get_setting("assignment_deadline_at"))


def _assignment_filter(batch: Optional[str]):
    if not batch or batch == "all":
        return None, False
    if batch == "unassigned":
        return None, True
    try:
        return int(batch), False
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid batch filter.")


@app.get("/api/files/{kind}")
async def api_list_files(
    kind: str,
    search: str = "",
    batch: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    user=Depends(require_staff),
):
    try:
        page_size = max(1, min(limit, 100))
        page_offset = max(0, offset)
        assignment_id, unassigned_only = _assignment_filter(batch)
        if kind == "students":
            submissions = list_submissions(
                limit=5000,
                search=search,
                assignment_id=assignment_id,
                unassigned_only=unassigned_only,
            )
            for row in submissions:
                row["assess_state"] = _assess_state(row["stored_filename"])
            total = len(submissions)
            page = submissions[page_offset : page_offset + page_size]
            groups: dict[str, list[dict]] = {}
            for row in page:
                label = assignment_label(row) if row.get("assignment_title") or row.get("batch_name") else "Unassigned"
                batch_name = label or "Unassigned"
                groups.setdefault(batch_name, []).append(row)
            grouped = [{"batch_name": key, "items": value, "count": len(value)} for key, value in sorted(groups.items())]
            allowed = {row["stored_filename"] for row in page}
            disk_files = list_files(kind)
            files = [f for f in disk_files if f["name"] in allowed]
            return {
                "kind": kind,
                "files": files,
                "submissions": page,
                "groups": grouped,
                "total": total,
                "limit": page_size,
                "offset": page_offset,
                "assignments": [_assignment_view(row) for row in list_assignments()],
            }
        if kind == "reports":
            reports = list_reports(
                limit=5000,
                search=search,
                assignment_id=assignment_id,
                unassigned_only=unassigned_only,
            )
            total = len(reports)
            page = reports[page_offset : page_offset + page_size]
            allowed = {row["pdf_filename"] for row in page}
            disk_files = list_files(kind)
            files = [f for f in disk_files if f["name"] in allowed]
            # Keep disk-only PDFs visible when no DB filter, paginated by name
            if not search.strip() and assignment_id is None and not unassigned_only:
                disk_all = disk_files
                by_pdf = {r["pdf_filename"]: r for r in reports}
                combined = []
                for item in disk_all:
                    combined.append({**item, "record": by_pdf.get(item["name"])})
                total = len(combined)
                page_items = combined[page_offset : page_offset + page_size]
                return {
                    "kind": kind,
                    "files": [{"name": i["name"], "size": i["size"], "modified": i.get("modified")} for i in page_items],
                    "reports": [i["record"] for i in page_items if i.get("record")],
                    "total": total,
                    "limit": page_size,
                    "offset": page_offset,
                    "assignments": [_assignment_view(row) for row in list_assignments()],
                }
            return {
                "kind": kind,
                "files": files,
                "reports": page,
                "total": total,
                "limit": page_size,
                "offset": page_offset,
                "assignments": [_assignment_view(row) for row in list_assignments()],
            }
        if kind not in ("slides", "textbook", "rubric"):
            raise HTTPException(status_code=400, detail="Unknown files kind.")
        files = list_files(kind)
        return {
            "kind": kind,
            "files": files,
            "total": len(files),
            "limit": page_size,
            "offset": page_offset,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/api/submissions/{submission_id}")
async def api_map_submission(submission_id: int, payload: SubmissionMapRequest, user=Depends(require_staff)):
    student = get_user(payload.student_user_id)
    if not student or student.get("role") != "student":
        raise HTTPException(status_code=400, detail="Select a valid student account.")
    existing = get_submission(submission_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Submission not found.")
    row = update_submission(
        submission_id,
        student_user_id=payload.student_user_id,
        assignment_id=student.get("assignment_id"),
    )
    return {"submission": row}


@app.post("/api/submissions/{submission_id}/archive")
async def api_archive_submission(
    submission_id: int,
    payload: RecordArchiveRequest,
    user=Depends(require_supervisor),
):
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Provide a reason for removal.")
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true. Files move to data/archive/ (not hard-deleted).",
        )
    try:
        result = archive_submission_record(submission_id, user["id"], reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


def _map_record_to_student(student_user_id: int, source_filename: Optional[str] = None):
    student = get_user(student_user_id)
    if not student or student.get("role") != "student":
        raise HTTPException(status_code=400, detail="Select a valid student account.")
    submission_id = None
    if source_filename:
        sub = get_submission_by_stored(os.path.basename(source_filename))
        if sub and sub.get("student_user_id") == student_user_id:
            submission_id = sub["id"]
    return student, submission_id


@app.patch("/api/reports/{report_id}")
async def api_map_report(report_id: int, payload: ReportMapRequest, user=Depends(require_staff)):
    existing = get_report(report_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Report not found.")
    student, submission_id = _map_record_to_student(
        payload.student_user_id,
        existing.get("source_filename"),
    )
    row = update_report(
        report_id,
        student_user_id=payload.student_user_id,
        assignment_id=student.get("assignment_id"),
        submission_id=submission_id,
        student_name=student.get("full_name") or existing.get("student_name"),
    )
    return {"report": row}


@app.patch("/api/reports/by-pdf/{pdf_filename}")
async def api_map_report_by_pdf(pdf_filename: str, payload: ReportMapRequest, user=Depends(require_staff)):
    safe_name = os.path.basename(pdf_filename)
    existing = get_report_by_pdf(safe_name)
    if existing:
        return await api_map_report(existing["id"], payload, user)
    student, submission_id = _map_record_to_student(payload.student_user_id)
    row = insert_report(
        pdf_filename=safe_name,
        json_path=None,
        sha256=None,
        student_name=student.get("full_name"),
        total_score_percent=None,
        student_user_id=payload.student_user_id,
        submission_id=submission_id,
        assignment_id=student.get("assignment_id"),
        source_filename=None,
        status="published",
    )
    return {"report": row}


@app.post("/api/reports/{report_id}/archive")
async def api_archive_report(
    report_id: int,
    payload: RecordArchiveRequest,
    user=Depends(require_supervisor),
):
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Provide a reason for removal.")
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true. Files move to data/archive/ (not hard-deleted).",
        )
    try:
        result = archive_report_record(report_id, user["id"], reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@app.get("/api/system-log")
@app.get("/api/deletion-log")
async def api_system_log(limit: int = 5, offset: int = 0, search: str = "", user=Depends(require_supervisor)):
    grading_queue.flush_finished_to_log()
    page_size = max(1, min(limit, 100))
    page_offset = max(0, offset)
    return {
        "entries": list_system_log(limit=page_size, offset=page_offset, search=search),
        "total": system_log_count(search=search),
        "limit": page_size,
        "offset": page_offset,
    }


@app.get("/api/word-limits")
async def api_word_limits(user=Depends(require_user)):
    return evaluate_word_count(None)["limits"]


@app.post("/api/files/{kind}")
async def api_upload_course_file(kind: str, file: UploadFile = File(...), user=Depends(require_supervisor)):
    if kind not in ("slides", "textbook", "rubric"):
        raise HTTPException(status_code=400, detail="Only slides, textbook, or rubric can be uploaded here.")
    ext = os.path.splitext(file.filename or "")[1].lower()
    allowed = COURSE_UPLOAD_EXTENSIONS if kind != "rubric" else {".pdf", ".docx", ".txt", ".doc"}
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported type. Allowed: {', '.join(sorted(allowed))}")
    dest_dir = {"slides": SLIDES_DIR, "textbook": TEXTBOOK_DIR, "rubric": RUBRIC_DIR}[kind]
    os.makedirs(dest_dir, exist_ok=True)
    safe_name = os.path.basename(file.filename)
    dest = os.path.join(dest_dir, safe_name)
    contents = await file.read()
    with open(dest, "wb") as handle:
        handle.write(contents)
    rebuilt = rebuild_layer(kind) if kind in ("slides", "textbook") else {"count": 0, "layer": kind, "skipped": True}
    return {"filename": safe_name, "rebuild": rebuilt}


@app.post("/api/files/{kind}/archive")
async def api_archive_course_file(kind: str, payload: ArchiveRequest, user=Depends(require_supervisor)):
    if kind not in ("slides", "textbook", "rubric"):
        raise HTTPException(status_code=400, detail="Only slides, textbook, or rubric can be archived here.")
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to archive this file (moved, not hard-deleted).")
    try:
        info = archive_file(kind, payload.filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    rebuilt = rebuild_layer(kind) if kind in ("slides", "textbook") else {"count": 0, "layer": kind, "skipped": True}
    return {"archived": info, "rebuild": rebuilt}


@app.post("/api/zip")
async def api_zip(payload: ZipRequest, user=Depends(require_staff)):
    try:
        data = zip_selected(payload.kind, payload.filenames)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"File not found: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    filename = f"{payload.kind}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(content=data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _load_live_briefs():
    assignment_text = load_assignment_text()
    rubric_text = load_rubric_text()
    if not assignment_text:
        raise HTTPException(status_code=400, detail="No assignment file found in data/assignments/.")
    return assignment_text, rubric_text


def _validate_provider_choice(provider: Optional[str], model: Optional[str]):
    available = {item["id"] for item in list_available_providers()}
    if not available:
        raise HTTPException(status_code=400, detail="No LLM providers are configured. Uncomment a provider block in .env.")
    if provider and provider not in available:
        raise HTTPException(status_code=400, detail=f"Provider '{provider}' is not available. Active providers: {', '.join(sorted(available))}")
    if not provider:
        default = next((p for p in list_available_providers() if p.get("is_default")), None)
        provider = default["id"] if default else next(iter(available))
    if not model:
        for item in list_available_providers():
            if item["id"] == provider:
                model = item["model"]
                break
    return provider, model


def _assess_state(stored_filename: str) -> dict:
    base = os.path.basename(stored_filename)
    primary = get_primary_report(base)
    job_active = grading_queue.has_active_job_for(base)
    has_report = primary is not None
    status = primary.get("status") if primary else None
    return {
        "can_assess": not has_report and not job_active,
        "has_report": has_report,
        "report_status": status,
        "approved": status == "published",
        "report_pdf": primary.get("pdf_filename") if primary else None,
        "job_active": job_active,
    }


def _grade_file_path(path, assignment_text, rubric_text, provider=None, model=None, compare=False):
    submission = extract_submission(path)
    if submission.word_count < 20:
        raise ValueError(
            f"Could not extract enough text from {submission.source_file} "
            f"({submission.word_count} words). {submission.extraction_notes}"
        )
    file_name = submission.source_file
    student_name, _ = parse_student_filename(file_name)
    date_str = find_submission_date(submission.text)
    result = grade_submission(
        get_index(),
        submission,
        assignment_text,
        rubric_text,
        provider=provider,
        model=model,
    )
    if not result.get("student_name") or result.get("student_name") == file_name:
        result["student_name"] = student_name
    report_student_name = (result.get("student_name") or student_name).strip()
    filename_variant = None
    if compare and provider and model:
        filename_variant = f"{provider}-{model}"
    pdf_path = generate_report(
        report_student_name,
        date_str,
        result,
        REPORT_DIR,
        filename_variant=filename_variant,
    )
    record = persist_generated_report(
        pdf_path,
        result,
        file_name,
        is_primary=not compare,
    )
    gc.collect()
    return {
        "file": file_name,
        "student_name": report_student_name,
        "date": date_str,
        "report": os.path.basename(pdf_path),
        "report_path": pdf_path,
        "report_id": record.get("id"),
        "sha256": record.get("sha256"),
        "provider": provider,
        "model": model,
        "assignment_id": record.get("assignment_id"),
        "extraction_word_count": submission.word_count,
        "total_score_percent": result.get("total_score_percent"),
        "error": result.get("error"),
        "status": "Failed" if result.get("error") else "Success",
        "result": result,
    }


def _log_actor(user: dict) -> str:
    name = (user.get("full_name") or "").strip() or user.get("email") or "user"
    role = (user.get("role") or "").strip()
    if role in ("supervisor", "educator", "student"):
        return f"{name} ({role})"
    return name


def _enqueue_grade(
    filename: str,
    provider: str,
    model: str,
    compare: bool = False,
    requested_by: Optional[str] = None,
):
    base = os.path.basename(filename)
    path = os.path.join(STUDENT_DIR, base)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    if not compare:
        existing = get_primary_report(base)
        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Primary assessment already exists for {base} "
                    f"({existing.get('pdf_filename')}). "
                    "Use the Assess page to compare models."
                ),
            )
        if grading_queue.has_active_job_for(base):
            raise HTTPException(status_code=409, detail=f"Grading already in progress for {base}.")
    assignment_text, rubric_text = _load_live_briefs()

    def task():
        return _grade_file_path(path, assignment_text, rubric_text, provider, model, compare=compare)

    return grading_queue.enqueue(base, provider, model, task, requested_by=requested_by)


@app.get("/api/providers")
async def get_providers(user=Depends(require_staff)):
    providers = list_available_providers()
    default = next((p for p in providers if p.get("is_default")), providers[0] if providers else None)
    return {
        "providers": providers,
        "default_provider": default["id"] if default else None,
        "default_model": default["model"] if default else None,
    }


@app.get("/api/index-status")
async def index_status(user=Depends(require_user)):
    return get_index_status()


@app.get("/api/jobs")
async def list_jobs_api(limit: int = 20, offset: int = 0, status: Optional[str] = None, search: str = "", user=Depends(require_staff)):
    jobs, total = grading_queue.list_jobs(limit=max(1, min(limit, 50)), offset=max(0, offset), status=status)
    if search:
        needle = search.lower().strip()
        jobs = [job for job in jobs if needle in job.filename.lower() or needle in (job.student_name or "").lower()]
    return {
        "active": grading_queue.active_count(),
        "counts": grading_queue.status_counts(),
        "jobs": [j.to_dict() for j in jobs],
        "total": total,
        "limit": limit,
        "offset": offset,
        "note": "Recent Job Worker is in-memory only. Search all outcomes in System log.",
    }


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, user=Depends(require_user)):
    job = grading_queue.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if user["role"] == "student":
        sub = get_submission_by_stored(job.filename)
        if not sub or sub.get("student_user_id") != user["id"]:
            raise HTTPException(status_code=404, detail="Job not found.")
    return job.to_dict()


async def _save_upload(file: UploadFile, student_user_id: Optional[int] = None, assignment_id: Optional[int] = None) -> dict:
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file.filename}. Allowed: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}")
    if student_user_id and assignment_id is None:
        student = get_user(student_user_id)
        if student:
            assignment_id = student.get("assignment_id")
    original = os.path.basename(file.filename)
    safe_name = original if not student_user_id else f"{student_user_id}_{original}"
    dest = os.path.join(STUDENT_DIR, safe_name)
    contents = await file.read()
    with open(dest, "wb") as handle:
        handle.write(contents)
    preview = extract_submission(dest)
    row = insert_submission(student_user_id, original, safe_name, assignment_id=assignment_id)
    return {
        "filename": safe_name,
        "original_filename": original,
        "saved_to": dest,
        "submission_id": row["id"],
        "assignment_id": row.get("assignment_id"),
        "assignment_title": row.get("assignment_title"),
        "batch_name": row.get("batch_name"),
        "extraction_word_count": preview.word_count,
        "section_headings": preview.section_headings[:8],
    }


@app.post("/upload/")
async def upload_student_file(file: UploadFile = File(...), user=Depends(require_staff)):
    return await _save_upload(file)


@app.get("/api/students")
async def api_students(search: str = "", batch: Optional[str] = None, user=Depends(require_staff)):
    assignment_id = None
    unassigned_only = False
    if batch and batch not in ("all", ""):
        assignment_id, unassigned_only = _assignment_filter(batch)
    users = list_users(
        role="student",
        search=search,
        assignment_id=assignment_id,
        unassigned_only=unassigned_only,
    )
    return {"students": users}


@app.post("/upload-batch/")
async def upload_batch(
    files: List[UploadFile] = File(...),
    student_ids: Optional[str] = Form(None),
    user=Depends(require_staff),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")
    parsed_ids = []
    if student_ids:
        try:
            parsed_ids = json.loads(student_ids)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="student_ids must be a JSON array.") from exc
    saved = []
    for index, file in enumerate(files):
        raw_id = parsed_ids[index] if index < len(parsed_ids) else None
        student_user_id = int(raw_id) if raw_id not in (None, "", "null") else None
        saved.append(await _save_upload(file, student_user_id=student_user_id))
    return {"uploaded": saved, "count": len(saved)}


@app.post("/grade/")
async def grade_uploaded_file(payload: GradeRequest, user=Depends(require_staff)):
    provider, model = _validate_provider_choice(payload.provider, payload.model)
    job = _enqueue_grade(
        payload.filename,
        provider,
        model,
        compare=payload.compare,
        requested_by=_log_actor(user),
    )
    return {"job_id": job.id, "status": job.status, "filename": job.filename, "message": "Queued. Poll GET /api/jobs/{job_id} or /api/jobs for progress. PDF saves to data/reports/ when done."}


@app.post("/grade/batch")
async def grade_batch(payload: BatchGradeRequest, user=Depends(require_staff)):
    if not payload.filenames:
        raise HTTPException(status_code=400, detail="No filenames provided.")
    provider, model = _validate_provider_choice(payload.provider, payload.model)
    actor = _log_actor(user)
    jobs = []
    for filename in payload.filenames:
        jobs.append(
            _enqueue_grade(
                filename,
                provider,
                model,
                compare=payload.compare,
                requested_by=actor,
            ).to_dict()
        )
    return {"jobs": jobs, "queued": len(jobs), "message": "Files queued for sequential grading. Reports appear in data/reports/ as each finishes."}


@app.post("/run-batch-grading/")
async def run_batch_grading(provider: Optional[str] = None, model: Optional[str] = None, user=Depends(require_staff)):
    if not os.path.exists(STUDENT_DIR) or not os.listdir(STUDENT_DIR):
        return {"error": "No files found in data/students/"}
    provider, model = _validate_provider_choice(provider, model)
    actor = _log_actor(user)
    job_ids = []
    for name in sorted(os.listdir(STUDENT_DIR)):
        if name.startswith("."):
            continue
        path = os.path.join(STUDENT_DIR, name)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            continue
        if get_primary_report(name) or grading_queue.has_active_job_for(name):
            continue
        job_ids.append(_enqueue_grade(name, provider, model, requested_by=actor).id)
    return {"message": "All student files queued for sequential grading", "job_ids": job_ids, "poll": "/api/jobs"}


@app.post("/api/student/submit")
async def student_submit(file: UploadFile = File(...), user=Depends(require_student)):
    if not _assignment_open(user):
        raise HTTPException(status_code=403, detail="The assignment deadline for your batch has passed.")
    return await _save_upload(file, student_user_id=user["id"], assignment_id=user.get("assignment_id"))


@app.post("/api/student/self-grade")
async def student_self_grade(payload: GradeRequest, user=Depends(require_student)):
    if not user.get("can_self_grade"):
        raise HTTPException(status_code=403, detail="Special permission is required to generate a grade.")
    stored = os.path.basename(payload.filename)
    row = get_submission_by_stored(stored)
    if not row or row.get("student_user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Submission not found.")
    provider, model = _validate_provider_choice(payload.provider, payload.model)
    job = _enqueue_grade(stored, provider, model, requested_by=_log_actor(user))
    return {"job_id": job.id, "status": job.status, "filename": stored}


def _enrich_submissions_for_student(submissions: list[dict], reports: list[dict]) -> list[dict]:
    by_source: dict[str, dict] = {}
    for row in reports:
        source = row.get("source_filename")
        if not source or not row.get("is_primary", 1):
            continue
        existing = by_source.get(source)
        if not existing or (row.get("id") or 0) > (existing.get("id") or 0):
            by_source[source] = row

    enriched = []
    for sub in submissions:
        item = dict(sub)
        primary = by_source.get(sub.get("stored_filename"))
        if primary and primary.get("status") == "published":
            item["grade_state"] = "approved"
            item["score_percent"] = primary.get("total_score_percent")
            item["report_pdf"] = primary.get("pdf_filename")
        elif primary and primary.get("status") == "pending_review":
            item["grade_state"] = "under_review"
            item["preliminary_pdf"] = primary.get("pdf_filename")
        else:
            item["grade_state"] = "submitted"
        enriched.append(item)
    return enriched


@app.get("/api/student/work")
async def student_work(search: str = "", user=Depends(require_student)):
    submissions = list_submissions(user["id"], search=search)
    all_reports = list_reports(student_user_id=user["id"], search=search, limit=500)
    published = [r for r in all_reports if r.get("status") == "published" and r.get("is_primary", 1)]
    return {
        "assignment": _assignment_view(get_assignment(user["assignment_id"])) if user.get("assignment_id") else None,
        "deadline": _user_deadline(user),
        "can_self_grade": bool(user.get("can_self_grade")),
        "submissions": _enrich_submissions_for_student(submissions, all_reports),
        "reports": published,
        "index": get_index_status(),
    }


@app.get("/api/reports")
async def api_list_reports(search: str = "", queue: bool = False, user=Depends(require_staff)):
    db_rows = [
        row
        for row in list_reports(
            limit=500,
            search=search,
            status="pending_review" if queue else None,
        )
        if row.get("is_primary", 1)
    ]
    by_pdf = {r["pdf_filename"]: r for r in db_rows}
    disk = []
    for item in list_files("reports"):
        row = by_pdf.get(item["name"])
        if queue and not row:
            continue
        if search and not row:
            continue
        disk.append({**item, "record": row})
    return {"reports": disk}


@app.get("/api/reports/{report_id}")
async def api_get_report(report_id: int, user=Depends(require_staff)):
    row = get_report(report_id)
    if not row:
        raise HTTPException(status_code=404, detail="Report not found.")
    json_name = row.get("json_path")
    payload = None
    if json_name:
        path = os.path.abspath(os.path.join(REPORT_DIR, os.path.basename(json_name)))
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
    return {"report": row, "grading_result": payload}


@app.patch("/api/reports/{report_id}/grading")
async def api_amend_grading(report_id: int, payload: ReviewSaveRequest, user=Depends(require_staff)):
    row = get_report(report_id)
    if not row:
        raise HTTPException(status_code=404, detail="Report not found.")
    result = payload.grading_result or {}
    pdf_path = os.path.abspath(os.path.join(REPORT_DIR, os.path.basename(row["pdf_filename"])))
    if not os.path.isfile(pdf_path):
        raise HTTPException(status_code=404, detail="PDF missing on disk.")
    json_path = write_result_json(pdf_path, result)
    updated = update_report(
        report_id,
        json_path=os.path.basename(json_path),
        student_name=(result.get("student_name") or row.get("student_name") or "").strip() or row.get("student_name"),
        total_score_percent=result.get("total_score_percent"),
    )
    return {"report": updated, "grading_result": result}


@app.put("/api/reports/{report_id}/review")
async def api_save_review(report_id: int, payload: ReviewSaveRequest, user=Depends(require_staff)):
    row = get_report(report_id)
    if not row:
        raise HTTPException(status_code=404, detail="Report not found.")
    if row.get("status") == "published":
        raise HTTPException(status_code=409, detail="This report is already approved.")
    result = payload.grading_result or {}
    try:
        return finalize_report(report_id, result, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/reports/{report_id}/verify")
async def api_verify_report(report_id: int, user=Depends(require_staff)):
    row = get_report(report_id)
    if not row:
        raise HTTPException(status_code=404, detail="Report not found.")
    path = os.path.abspath(os.path.join(REPORT_DIR, os.path.basename(row["pdf_filename"])))
    return {"report_id": report_id, "filename": row["pdf_filename"], **verify_pdf(path, row.get("sha256"))}


@app.get("/reports/{filename}")
async def download_report(filename: str, request: Request, download: bool = False):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required.")
    name = os.path.basename(filename)
    path = os.path.abspath(os.path.join(REPORT_DIR, name))
    root = os.path.abspath(REPORT_DIR)
    if not path.startswith(root) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Report not found.")
    if user["role"] == "student":
        row = get_report_by_pdf(name)
        if not row or row.get("student_user_id") != user["id"]:
            raise HTTPException(status_code=404, detail="Report not found.")
        if row.get("status") not in ("published", "pending_review"):
            raise HTTPException(status_code=404, detail="Report not found.")
        if not row.get("is_primary", 1):
            raise HTTPException(status_code=404, detail="Report not found.")
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{name}"'},
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
