"""
Task Service - Core Study Planner Microservice
Clean Architecture: Routes → Services → Repositories → Models
"""

import os
import time
import uuid
import logging
import json
from datetime import datetime, date
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import httpx
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import psycopg2
from psycopg2.pool import SimpleConnectionPool

# ─── Logging Setup ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "service": "task-service", "message": "%(message)s"}'
)
logger = logging.getLogger(__name__)

# ─── DB ─────────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")

pool: SimpleConnectionPool | None = None

def get_pool() -> SimpleConnectionPool:
    global pool
    if pool is None:
        pool = SimpleConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)
    return pool

def db_exec(query: str, params: tuple = (), fetchone: bool = False, fetchall: bool = False):
    p = get_pool()
    conn = p.getconn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                if fetchone:
                    return cur.fetchone()
                if fetchall:
                    return cur.fetchall()
                return None
    finally:
        p.putconn(conn)

def init_db():
    db_exec(
        """
        CREATE TABLE IF NOT EXISTS subjects (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL,
            name TEXT NOT NULL,
            color TEXT NOT NULL,
            target_hours_per_week DOUBLE PRECISION NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(user_id, name)
        );
        """
    )
    db_exec("CREATE INDEX IF NOT EXISTS idx_subjects_user_id ON subjects(user_id);")
    db_exec(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL,
            title TEXT NOT NULL,
            description TEXT NULL,
            subject TEXT NOT NULL,
            due_date DATE NOT NULL,
            priority TEXT NOT NULL,
            estimated_hours DOUBLE PRECISION NULL,
            status TEXT NOT NULL,
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    db_exec("CREATE INDEX IF NOT EXISTS idx_tasks_user_due ON tasks(user_id, due_date);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_tasks_user_priority ON tasks(user_id, priority);")

def get_user_id(request: Request) -> str:
    uid = request.headers.get("x-user-id") or request.headers.get("X-User-ID")
    if not uid:
        raise HTTPException(status_code=401, detail="Missing user identity")
    return uid

# ─── Prometheus Metrics ───────────────────────────────────────────────────────
REQUEST_COUNT    = Counter("task_requests_total", "Total requests", ["method", "endpoint", "status"])
REQUEST_LATENCY  = Histogram("task_request_duration_seconds", "Request latency", ["endpoint"])
TASK_CREATED     = Counter("tasks_created_total", "Tasks created")
TASK_COMPLETED   = Counter("tasks_completed_total", "Tasks completed")

# ─── Pydantic Models ──────────────────────────────────────────────────────────
class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    subject: str = Field(..., min_length=1, max_length=100)
    due_date: str = Field(..., description="ISO date string YYYY-MM-DD")
    priority: str = Field("medium", pattern="^(low|medium|high|critical)$")
    estimated_hours: Optional[float] = Field(None, ge=0.1, le=100)
    tags: Optional[List[str]] = []

class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    subject: Optional[str] = None
    due_date: Optional[str] = None
    priority: Optional[str] = Field(None, pattern="^(low|medium|high|critical)$")
    estimated_hours: Optional[float] = None
    status: Optional[str] = Field(None, pattern="^(pending|in_progress|completed|cancelled)$")
    tags: Optional[List[str]] = None

class Task(BaseModel):
    id: str
    title: str
    description: Optional[str]
    subject: str
    due_date: str
    priority: str
    estimated_hours: Optional[float]
    status: str
    tags: List[str]
    created_at: str
    updated_at: str
    user_id: str

class SubjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    color: str = Field("#6366f1", pattern="^#[0-9a-fA-F]{6}$")
    target_hours_per_week: Optional[float] = None

# ─── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Task Service started")
    yield
    global pool
    if pool is not None:
        pool.closeall()
        pool = None
    logger.info("Task Service shutting down")

# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Study Planner — Task Service",
    description="Manages study tasks, subjects, and scheduling",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── Middleware: Logging & Metrics ────────────────────────────────────────────
@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    REQUEST_COUNT.labels(request.method, request.url.path, response.status_code).inc()
    REQUEST_LATENCY.labels(request.url.path).observe(duration)
    logger.info(f"method={request.method} path={request.url.path} status={response.status_code} duration={duration:.3f}s")
    response.headers["X-Service"] = "task-service"
    response.headers["X-Request-ID"] = str(uuid.uuid4())
    return response

# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/health", tags=["Observability"])
def health():
    row = db_exec("SELECT COUNT(*) FROM tasks", fetchone=True)
    return {
        "status": "healthy",
        "service": "task-service",
        "timestamp": datetime.utcnow().isoformat(),
        "tasks_count": int(row[0] if row else 0),
    }

@app.get("/metrics", tags=["Observability"])
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# ─── Tasks CRUD ───────────────────────────────────────────────────────────────
@app.get("/tasks", response_model=List[Task], tags=["Tasks"])
def list_tasks(request: Request, subject: Optional[str] = None, status: Optional[str] = None, priority: Optional[str] = None):
    """List all tasks with optional filters"""
    user_id = get_user_id(request)
    where = ["user_id=%s"]
    params: list = [user_id]
    if subject:
        where.append("LOWER(subject)=LOWER(%s)")
        params.append(subject)
    if status:
        where.append("status=%s")
        params.append(status)
    if priority:
        where.append("priority=%s")
        params.append(priority)
    q = (
        "SELECT id,title,description,subject,due_date,priority,estimated_hours,status,tags,created_at,updated_at,user_id "
        f"FROM tasks WHERE {' AND '.join(where)} ORDER BY due_date ASC"
    )
    rows = db_exec(q, tuple(params), fetchall=True) or []
    results = []
    from datetime import timedelta
    tomorrow = date.today() + timedelta(days=1)
    for r in rows:
        results.append(
            {
                "id": str(r[0]),
                "title": r[1],
                "description": r[2],
                "subject": r[3],
                "due_date": r[4].isoformat(),
                "priority": r[5],
                "estimated_hours": r[6],
                "status": r[7],
                "tags": r[8] or [],
                "created_at": r[9].isoformat() if r[9] else None,
                "updated_at": r[10].isoformat() if r[10] else None,
                "user_id": str(r[11]),
            }
        )
        # Send notification for tasks due tomorrow
        if r[4] == tomorrow and r[7] != "completed" and r[7] != "cancelled":
            try:
                notification_data = {
                    "event_type": "task_due",
                    "payload": {
                        "user_id": user_id,
                        "task_id": str(r[0]),
                        "title": "Task due tomorrow",
                        "message": f"'{r[1]}' is due tomorrow ({r[4].isoformat()})",
                        "idempotency_key": f"task_due_{r[0]}_{r[4]}",
                    },
                    "source_service": "task-service",
                }
                response = httpx.post("http://localhost:8003/events", json=notification_data, timeout=5.0)
                logger.info(f"Task due notification sent: status={response.status_code}")
            except Exception as e:
                logger.error(f"Failed to publish task_due notification: {e}")
    return results

@app.post("/tasks", response_model=Task, status_code=201, tags=["Tasks"])
def create_task(request: Request, task: TaskCreate):
    """Create a new study task"""
    user_id = get_user_id(request)
    tid = uuid.uuid4()
    now = datetime.utcnow()
    due = date.fromisoformat(task.due_date)
    tags_json = json.dumps(task.tags or [])
    db_exec(
        """
        INSERT INTO tasks (id,user_id,title,description,subject,due_date,priority,estimated_hours,status,tags,created_at,updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
        """,
        (
            str(tid),
            user_id,
            task.title,
            task.description,
            task.subject,
            due,
            task.priority,
            task.estimated_hours,
            "pending",
            tags_json,
            now,
            now,
        ),
    )
    TASK_CREATED.inc()
    logger.info(f"Task created: id={tid} title={task.title}")
    # Publish notification event
    try:
        notification_data = {
            "event_type": "task_created",
            "payload": {
                "user_id": user_id,
                "task_id": str(tid),
                "title": f"New task: {task.title}",
                "message": f"You created a new task '{task.title}' due on {due.isoformat()}",
                "idempotency_key": f"task_created_{tid}",
            },
            "source_service": "task-service",
        }
        response = httpx.post("http://localhost:8003/events", json=notification_data, timeout=5.0)
        logger.info(f"Notification event sent: status={response.status_code} response={response.text}")
    except Exception as e:
        logger.error(f"Failed to publish task_created notification: {e}")
    return {
        "id": str(tid),
        "title": task.title,
        "description": task.description,
        "subject": task.subject,
        "due_date": due.isoformat(),
        "priority": task.priority,
        "estimated_hours": task.estimated_hours,
        "status": "pending",
        "tags": task.tags or [],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "user_id": user_id,
    }

@app.get("/tasks/{task_id}", response_model=Task, tags=["Tasks"])
def get_task(task_id: str, request: Request):
    user_id = get_user_id(request)
    row = db_exec(
        "SELECT id,title,description,subject,due_date,priority,estimated_hours,status,tags,created_at,updated_at,user_id "
        "FROM tasks WHERE id=%s AND user_id=%s",
        (task_id, user_id),
        fetchone=True,
    )
    if not row:
        raise HTTPException(404, "Task not found")
    return {
        "id": str(row[0]),
        "title": row[1],
        "description": row[2],
        "subject": row[3],
        "due_date": row[4].isoformat(),
        "priority": row[5],
        "estimated_hours": row[6],
        "status": row[7],
        "tags": row[8] or [],
        "created_at": row[9].isoformat() if row[9] else None,
        "updated_at": row[10].isoformat() if row[10] else None,
        "user_id": str(row[11]),
    }

@app.patch("/tasks/{task_id}", response_model=Task, tags=["Tasks"])
def update_task(task_id: str, request: Request, update: TaskUpdate):
    user_id = get_user_id(request)
    row = db_exec(
        "SELECT status FROM tasks WHERE id=%s AND user_id=%s",
        (task_id, user_id),
        fetchone=True,
    )
    if not row:
        raise HTTPException(404, "Task not found")
    old_status = row[0]
    data = update.model_dump(exclude_none=True)
    fields = []
    params: list = []
    if "title" in data:
        fields.append("title=%s"); params.append(data["title"])
    if "description" in data:
        fields.append("description=%s"); params.append(data["description"])
    if "subject" in data:
        fields.append("subject=%s"); params.append(data["subject"])
    if "due_date" in data:
        fields.append("due_date=%s"); params.append(date.fromisoformat(data["due_date"]))
    if "priority" in data:
        fields.append("priority=%s"); params.append(data["priority"])
    if "estimated_hours" in data:
        fields.append("estimated_hours=%s"); params.append(data["estimated_hours"])
    if "status" in data:
        fields.append("status=%s"); params.append(data["status"])
    if "tags" in data:
        fields.append("tags=%s::jsonb"); params.append(json.dumps(data["tags"] or []))

    updated_at = datetime.utcnow()
    fields.append("updated_at=%s"); params.append(updated_at)
    params.extend([task_id, user_id])
    db_exec(f"UPDATE tasks SET {', '.join(fields)} WHERE id=%s AND user_id=%s", tuple(params))

    if data.get("status") == "completed" and old_status != "completed":
        TASK_COMPLETED.inc()
        logger.info(f"Task completed: id={task_id}")
    return get_task(task_id, request)

@app.delete("/tasks/{task_id}", status_code=204, tags=["Tasks"])
def delete_task(task_id: str, request: Request):
    user_id = get_user_id(request)
    row = db_exec("SELECT 1 FROM tasks WHERE id=%s AND user_id=%s", (task_id, user_id), fetchone=True)
    if not row:
        raise HTTPException(404, "Task not found")
    db_exec("DELETE FROM tasks WHERE id=%s AND user_id=%s", (task_id, user_id))
    logger.info(f"Task deleted: id={task_id}")

# ─── Subjects ─────────────────────────────────────────────────────────────────
@app.get("/subjects", tags=["Subjects"])
def list_subjects(request: Request):
    user_id = get_user_id(request)
    rows = db_exec(
        "SELECT id,name,color,target_hours_per_week,created_at,user_id FROM subjects WHERE user_id=%s ORDER BY name ASC",
        (user_id,),
        fetchall=True,
    ) or []
    return [
        {
            "id": str(r[0]),
            "name": r[1],
            "color": r[2],
            "target_hours_per_week": r[3],
            "created_at": r[4].isoformat() if r[4] else None,
            "user_id": str(r[5]),
        }
        for r in rows
    ]

@app.post("/subjects", status_code=201, tags=["Subjects"])
def create_subject(request: Request, subject: SubjectCreate):
    user_id = get_user_id(request)
    sid = uuid.uuid4()
    now = datetime.utcnow()
    try:
        db_exec(
            "INSERT INTO subjects (id,user_id,name,color,target_hours_per_week,created_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (str(sid), user_id, subject.name, subject.color, subject.target_hours_per_week, now),
        )
    except Exception:
        # likely UNIQUE(user_id,name)
        raise HTTPException(409, "Subject already exists")
    return {
        "id": str(sid),
        "name": subject.name,
        "color": subject.color,
        "target_hours_per_week": subject.target_hours_per_week,
        "created_at": now.isoformat(),
        "user_id": user_id,
    }

# ─── Stats ────────────────────────────────────────────────────────────────────
@app.get("/stats", tags=["Analytics"])
def get_stats(request: Request):
    user_id = get_user_id(request)
    rows = db_exec(
        "SELECT subject, status, priority, due_date FROM tasks WHERE user_id=%s",
        (user_id,),
        fetchall=True,
    ) or []
    by_status   = {}
    by_priority = {}
    by_subject  = {}
    all_tasks_count = 0
    overdue_count = 0
    today = date.today()
    for subject, status_v, priority_v, due_date_v in rows:
        all_tasks_count += 1
        by_status[status_v] = by_status.get(status_v, 0) + 1
        by_priority[priority_v] = by_priority.get(priority_v, 0) + 1
        by_subject[subject] = by_subject.get(subject, 0) + 1
        if due_date_v < today and status_v not in ("completed", "cancelled"):
            overdue_count += 1
    return {
        "total": all_tasks_count,
        "by_status": by_status,
        "by_priority": by_priority,
        "by_subject": by_subject,
        "overdue_count": overdue_count,
        "completion_rate": round(by_status.get("completed", 0) / max(all_tasks_count, 1) * 100, 1)
    }
