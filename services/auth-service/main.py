"""
Auth Service - JWT Authentication & Authorization
"""
import os
import time, uuid, logging
from datetime import datetime, timedelta
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, EmailStr
import jwt
import bcrypt
from starlette.responses import Response
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
import psycopg2
from psycopg2.pool import SimpleConnectionPool

logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "service": "auth-service", "message": "%(message)s"}'
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")

SECRET_KEY = os.getenv("JWT_SECRET")
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET is required")

ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
TOKEN_EXP = int(os.getenv("JWT_EXPIRY_MINUTES", "60"))  # minutes
REFRESH_EXP_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRY_DAYS", "7"))
SEED_DEMO_USER = os.getenv("SEED_DEMO_USER", "false").lower() == "true"

LOGIN_ATTEMPTS = Counter("auth_login_attempts_total", "Login attempts", ["status"])
TOKENS_ISSUED  = Counter("auth_tokens_issued_total", "JWT tokens issued")

pool: SimpleConnectionPool | None = None

class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str
    password: str = Field(..., min_length=8)
    full_name: Optional[str] = None

class UserLogin(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = TOKEN_EXP * 60

class TokenRefresh(BaseModel):
    refresh_token: str

def get_pool() -> SimpleConnectionPool:
    global pool
    if pool is None:
        # Small pool; services are low-traffic (homework).
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
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NULL,
            role TEXT NOT NULL DEFAULT 'student',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    db_exec(
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            refresh_token TEXT PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def verify_password(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())

def create_token(data: dict, expires_delta: timedelta) -> str:
    payload = {**data, "exp": datetime.utcnow() + expires_delta, "iat": datetime.utcnow(), "jti": str(uuid.uuid4())}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return decode_token(credentials.credentials)

def user_row_to_dict(row: tuple) -> dict:
    return {
        "id": str(row[0]),
        "username": row[1],
        "email": row[2],
        "password_hash": row[3],
        "full_name": row[4],
        "role": row[5],
        "created_at": row[6].isoformat() if row[6] else None,
    }

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if SEED_DEMO_USER:
        demo = db_exec("SELECT id FROM users WHERE username=%s", ("demo",), fetchone=True)
        if not demo:
            uid = uuid.uuid4()
            db_exec(
                "INSERT INTO users (id, username, email, password_hash, full_name, role) VALUES (%s,%s,%s,%s,%s,%s)",
                (str(uid), "demo", "demo@studyplanner.io", hash_password("demo1234"), "Demo Student", "student"),
            )
            logger.info("Demo user seeded (demo/demo1234)")
    logger.info("Auth Service started")
    yield
    global pool
    if pool is not None:
        pool.closeall()
        pool = None

app = FastAPI(title="Study Planner — Auth Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def log_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    logger.info(f"method={request.method} path={request.url.path} status={response.status_code} duration={time.time()-start:.3f}s")
    return response

@app.get("/health", tags=["Observability"])
def health():
    row = db_exec("SELECT COUNT(*) FROM users", fetchone=True)
    return {"status": "healthy", "service": "auth-service", "users_count": int(row[0] if row else 0)}

@app.get("/metrics", tags=["Observability"])
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/register", status_code=201, tags=["Auth"])
def register(body: UserRegister):
    existing = db_exec("SELECT 1 FROM users WHERE username=%s OR email=%s", (body.username, body.email), fetchone=True)
    if existing:
        raise HTTPException(409, "Username or email already exists")
    uid = uuid.uuid4()
    db_exec(
        "INSERT INTO users (id, username, email, password_hash, full_name, role) VALUES (%s,%s,%s,%s,%s,%s)",
        (str(uid), body.username, body.email, hash_password(body.password), body.full_name, "student"),
    )
    logger.info(f"User registered: username={body.username}")
    return {"message": "User registered successfully", "user_id": str(uid)}

@app.post("/login", response_model=TokenResponse, tags=["Auth"])
def login(body: UserLogin):
    row = db_exec(
        "SELECT id, username, email, password_hash, full_name, role, created_at FROM users WHERE username=%s",
        (body.username,),
        fetchone=True,
    )
    user = user_row_to_dict(row) if row else None
    if not user or not verify_password(body.password, user["password_hash"]):
        LOGIN_ATTEMPTS.labels("failed").inc()
        raise HTTPException(401, "Invalid credentials")
    LOGIN_ATTEMPTS.labels("success").inc()
    payload = {"sub": user["id"], "username": user["username"], "role": user["role"]}
    access  = create_token(payload, timedelta(minutes=TOKEN_EXP))
    refresh = create_token({"sub": user["id"], "type": "refresh"}, timedelta(days=REFRESH_EXP_DAYS))
    refresh_payload = decode_token(refresh)
    expires_at = datetime.utcfromtimestamp(refresh_payload["exp"])
    db_exec(
        "INSERT INTO refresh_tokens (refresh_token, user_id, expires_at) VALUES (%s,%s,%s)",
        (refresh, user["id"], expires_at),
    )
    TOKENS_ISSUED.inc()
    logger.info(f"User logged in: username={body.username}")
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "expires_in": TOKEN_EXP * 60}

@app.post("/refresh", response_model=TokenResponse, tags=["Auth"])
def refresh_token(body: TokenRefresh):
    row = db_exec(
        "SELECT user_id, expires_at FROM refresh_tokens WHERE refresh_token=%s",
        (body.refresh_token,),
        fetchone=True,
    )
    if not row:
        raise HTTPException(401, "Invalid refresh token")
    if row[1] and row[1] < datetime.utcnow():
        db_exec("DELETE FROM refresh_tokens WHERE refresh_token=%s", (body.refresh_token,))
        raise HTTPException(401, "Refresh token expired")
    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(401, "Not a refresh token")
    user_id = payload["sub"]
    urow = db_exec(
        "SELECT id, username, email, password_hash, full_name, role, created_at FROM users WHERE id=%s",
        (user_id,),
        fetchone=True,
    )
    if not urow:
        raise HTTPException(401, "User not found")
    user = user_row_to_dict(urow)
    new_payload = {"sub": user["id"], "username": user["username"], "role": user["role"]}
    access  = create_token(new_payload, timedelta(minutes=TOKEN_EXP))
    new_ref = create_token({"sub": user["id"], "type": "refresh"}, timedelta(days=REFRESH_EXP_DAYS))
    new_ref_payload = decode_token(new_ref)
    new_expires_at = datetime.utcfromtimestamp(new_ref_payload["exp"])
    db_exec("DELETE FROM refresh_tokens WHERE refresh_token=%s", (body.refresh_token,))
    db_exec(
        "INSERT INTO refresh_tokens (refresh_token, user_id, expires_at) VALUES (%s,%s,%s)",
        (new_ref, user["id"], new_expires_at),
    )
    return {"access_token": access, "refresh_token": new_ref, "token_type": "bearer", "expires_in": TOKEN_EXP * 60}

@app.post("/logout", tags=["Auth"])
def logout(body: TokenRefresh):
    db_exec("DELETE FROM refresh_tokens WHERE refresh_token=%s", (body.refresh_token,))
    return {"message": "Logged out"}

@app.post("/verify", tags=["Auth"])
def verify(credentials: HTTPAuthorizationCredentials = Depends(security)):
    payload = decode_token(credentials.credentials)
    return {"valid": True, "user_id": payload["sub"], "username": payload.get("username"), "role": payload.get("role")}

@app.get("/me", tags=["Auth"])
def me(current_user: dict = Depends(get_current_user)):
    row = db_exec(
        "SELECT id, username, email, password_hash, full_name, role, created_at FROM users WHERE id=%s",
        (current_user["sub"],),
        fetchone=True,
    )
    if not row:
        raise HTTPException(404, "User not found")
    user = user_row_to_dict(row)
    return {k: v for k, v in user.items() if k != "password_hash"}
