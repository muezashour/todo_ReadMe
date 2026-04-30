"""
Notification Service - Handles in-app & email notifications
Simulates async messaging (RabbitMQ/Kafka pattern) with an in-process queue
"""
import os
import time, uuid, logging, asyncio
from datetime import datetime
from typing import Optional, List
from contextlib import asynccontextmanager
from collections import deque
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import Response
from fastapi.responses import JSONResponse
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
import psycopg2
from psycopg2.pool import SimpleConnectionPool

logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "service": "notification-service", "message": "%(message)s"}'
)
logger = logging.getLogger(__name__)

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
        CREATE TABLE IF NOT EXISTS notifications (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            idempotency_key TEXT NULL,
            read BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(user_id, idempotency_key)
        );
        """
    )
    db_exec("CREATE INDEX IF NOT EXISTS idx_notifs_user_created ON notifications(user_id, created_at DESC);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_notifs_user_read ON notifications(user_id, read);")

def require_user(request: Request, path_user_id: str | None = None) -> str:
    uid = request.headers.get("x-user-id") or request.headers.get("X-User-ID")
    if not uid:
        raise HTTPException(status_code=401, detail="Missing user identity")
    if path_user_id and path_user_id != uid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return uid

NOTIFS_SENT    = Counter("notifications_sent_total", "Notifications sent", ["type"])
NOTIFS_PENDING = Counter("notifications_queued_total", "Notifications queued")

message_queue: deque = deque(maxlen=1000)  # Simulates message broker queue

class NotificationCreate(BaseModel):
    user_id: str
    type: str = Field(..., pattern="^(task_due|task_completed|reminder|system)$")
    title: str
    message: str
    metadata: Optional[dict] = {}
    idempotency_key: Optional[str] = None

class NotificationEvent(BaseModel):
    """Message format for async events (RabbitMQ/Kafka message body)"""
    event_type: str
    payload: dict
    source_service: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

def insert_notification(*, user_id: str, ntype: str, title: str, message: str, metadata: dict | None, idempotency_key: str | None):
    nid = uuid.uuid4()
    now = datetime.utcnow()
    if idempotency_key:
        existing = db_exec(
            "SELECT 1 FROM notifications WHERE user_id=%s AND idempotency_key=%s",
            (user_id, idempotency_key),
            fetchone=True,
        )
        if existing:
            return None
    try:
        db_exec(
            """
            INSERT INTO notifications (id,user_id,type,title,message,metadata,idempotency_key,read,created_at)
            VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,false,%s)
            """,
            (str(nid), user_id, ntype, title, message, json.dumps(metadata or {}), idempotency_key, now),
        )
    except Exception:
        # Most likely UNIQUE(user_id, idempotency_key) collision; treat as no-op.
        return None
    NOTIFS_SENT.labels(ntype).inc()
    return {
        "id": str(nid),
        "user_id": user_id,
        "type": ntype,
        "title": title,
        "message": message,
        "metadata": metadata or {},
        "idempotency_key": idempotency_key,
        "read": False,
        "created_at": now.isoformat(),
    }

async def process_queue():
    """Background worker — simulates a message consumer"""
    while True:
        if message_queue:
            event = message_queue.popleft()
            created = insert_notification(
                user_id=event.get("user_id", "system"),
                ntype=event.get("event_type", "system"),
                title=event.get("title", "Notification"),
                message=event.get("message", ""),
                metadata=event.get("metadata", {}),
                idempotency_key=event.get("idempotency_key"),
            )
            if created:
                logger.info(f"Processed notification: id={created['id']} type={created['type']}")
        await asyncio.sleep(0.5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    asyncio.create_task(process_queue())
    logger.info("Notification Service started — queue processor running")
    yield
    global pool
    if pool is not None:
        pool.closeall()
        pool = None

app = FastAPI(title="Study Planner — Notification Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def log_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    logger.info(f"method={request.method} path={request.url.path} status={response.status_code} duration={time.time()-start:.3f}s")
    return response

@app.get("/health", tags=["Observability"])
def health():
    row = db_exec("SELECT COUNT(*) FROM notifications", fetchone=True)
    return {"status": "healthy", "service": "notification-service",
            "notifications_count": int(row[0] if row else 0), "queue_size": len(message_queue)}

@app.get("/metrics", tags=["Observability"])
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/notifications/{user_id}", tags=["Notifications"])
def get_notifications(user_id: str, request: Request, unread_only: bool = False):
    uid = require_user(request, user_id)
    if unread_only:
        rows = db_exec(
            "SELECT id,user_id,type,title,message,metadata,idempotency_key,read,created_at FROM notifications "
            "WHERE user_id=%s AND read=false ORDER BY created_at DESC",
            (uid,),
            fetchall=True,
        ) or []
    else:
        rows = db_exec(
            "SELECT id,user_id,type,title,message,metadata,idempotency_key,read,created_at FROM notifications "
            "WHERE user_id=%s ORDER BY created_at DESC",
            (uid,),
            fetchall=True,
        ) or []
    return [
        {
            "id": str(r[0]),
            "user_id": str(r[1]),
            "type": r[2],
            "title": r[3],
            "message": r[4],
            "metadata": r[5] or {},
            "idempotency_key": r[6],
            "read": bool(r[7]),
            "created_at": r[8].isoformat() if r[8] else None,
        }
        for r in rows
    ]

@app.post("/notifications", status_code=201, tags=["Notifications"])
def create_notification(body: NotificationCreate, request: Request):
    uid = require_user(request, body.user_id)
    created = insert_notification(
        user_id=uid,
        ntype=body.type,
        title=body.title,
        message=body.message,
        metadata=body.metadata,
        idempotency_key=body.idempotency_key,
    )
    if not created:
        return JSONResponse(status_code=200, content={"deduped": True})
    return created

@app.patch("/notifications/{notif_id}/read", tags=["Notifications"])
def mark_read(notif_id: str, request: Request):
    uid = require_user(request)
    row = db_exec("SELECT 1 FROM notifications WHERE id=%s AND user_id=%s", (notif_id, uid), fetchone=True)
    if not row:
        raise HTTPException(404, "Notification not found")
    db_exec("UPDATE notifications SET read=true WHERE id=%s AND user_id=%s", (notif_id, uid))
    return {"message": "Marked as read"}

@app.patch("/notifications/{user_id}/read-all", tags=["Notifications"])
def mark_all_read(user_id: str, request: Request):
    uid = require_user(request, user_id)
    db_exec("UPDATE notifications SET read=true WHERE user_id=%s AND read=false", (uid,))
    row = db_exec("SELECT COUNT(*) FROM notifications WHERE user_id=%s AND read=true", (uid,), fetchone=True)
    count = int(row[0] if row else 0)
    return {"message": f"Marked {count} notifications as read"}

@app.post("/events", tags=["Async Messaging"])
async def publish_event(event: NotificationEvent):
    """
    Endpoint simulating a message broker consumer.
    In production: task-service publishes to RabbitMQ/Kafka,
    this service consumes from the queue.
    """
    message_queue.append({
        "event_type": event.event_type,
        "user_id": event.payload.get("user_id", "demo-user"),
        "title": event.payload.get("title", "Event"),
        "message": event.payload.get("message", ""),
        "metadata": event.payload,
        "idempotency_key": event.payload.get("idempotency_key"),
    })
    NOTIFS_PENDING.inc()
    logger.info(f"Event queued: type={event.event_type} correlation_id={event.correlation_id}")
    return {"queued": True, "correlation_id": event.correlation_id, "queue_size": len(message_queue)}

@app.get("/events/queue-status", tags=["Async Messaging"])
def queue_status():
    row = db_exec("SELECT COUNT(*) FROM notifications", fetchone=True)
    return {"queue_size": len(message_queue), "processed_total": int(row[0] if row else 0)}
