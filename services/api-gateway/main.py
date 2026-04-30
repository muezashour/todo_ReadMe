"""
API Gateway - Single entry point for all microservices
Implements: Routing, Auth verification, Rate limiting, Request logging
"""
import time, uuid, logging
from datetime import datetime
from collections import defaultdict
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "service": "api-gateway", "message": "%(message)s"}'
)
logger = logging.getLogger(__name__)

PROXY_REQUESTS  = Counter("gateway_requests_total", "Proxy requests", ["service", "status"])
PROXY_LATENCY   = Histogram("gateway_proxy_duration_seconds", "Proxy latency", ["service"])
RATE_LIMITED    = Counter("gateway_rate_limited_total", "Rate limited requests")

# Service registry (in prod: use Consul/etcd/k8s DNS)
SERVICES = {
    "tasks":         "http://task-service:8001",
    "auth":          "http://auth-service:8002",
    "notifications": "http://notification-service:8003",
}

# Rate limiting: 100 req/min per IP
rate_limit_store: dict[str, list] = defaultdict(list)
RATE_LIMIT = 100
WINDOW_SEC = 60

# Public routes (no auth required)
PUBLIC_ROUTES = {"/health", "/metrics", "/docs", "/openapi.json", "/auth/login", "/auth/register"}

security = HTTPBearer(auto_error=False)

def check_rate_limit(ip: str) -> bool:
    now = time.time()
    window_start = now - WINDOW_SEC
    requests = rate_limit_store[ip]
    rate_limit_store[ip] = [t for t in requests if t > window_start]
    if len(rate_limit_store[ip]) >= RATE_LIMIT:
        return False
    rate_limit_store[ip].append(now)
    return True

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("API Gateway started")
    yield
    logger.info("API Gateway shutting down")

app = FastAPI(
    title="Study Planner — API Gateway",
    description="Central entry point: routing, auth, rate limiting",
    version="1.0.0",
    lifespan=lifespan
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def gateway_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    request_id = str(uuid.uuid4())
    # Rate limiting
    if not check_rate_limit(client_ip):
        RATE_LIMITED.inc()
        logger.warning(f"Rate limited: ip={client_ip}")
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded", "retry_after": WINDOW_SEC})
    request.state.request_id = request_id
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    response.headers["X-Request-ID"]  = request_id
    response.headers["X-Gateway"]     = "study-planner-gateway"
    response.headers["X-Response-Time"] = f"{duration:.3f}s"
    logger.info(f"request_id={request_id} ip={client_ip} method={request.method} path={request.url.path} status={response.status_code} duration={duration:.3f}s")
    return response

async def verify_token(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify JWT with auth service (gateway-level auth)"""
    path = request.url.path
    if any(path.startswith(r) for r in PUBLIC_ROUTES):
        return None
    if not credentials:
        raise HTTPException(401, "Authentication required")
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.post(
                f"{SERVICES['auth']}/verify",
                headers={"Authorization": f"Bearer {credentials.credentials}"}
            )
            if resp.status_code != 200:
                raise HTTPException(401, "Invalid or expired token")
            return resp.json()
        except httpx.RequestError:
            raise HTTPException(503, "Auth service unavailable")

async def proxy(service: str, path: str, request: Request, user=None):
    base_url = SERVICES.get(service)
    if not base_url:
        raise HTTPException(404, f"Service '{service}' not found")
    url = f"{base_url}{path}"
    if request.url.query:
        url += f"?{request.url.query}"
    body = await request.body()
    headers = dict(request.headers)
    headers.pop("host", None)
    if user:
        headers["X-User-ID"]   = user.get("user_id", "")
        headers["X-Username"]  = user.get("username", "")
        headers["X-User-Role"] = user.get("role", "")
    start = time.time()
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.request(request.method, url, content=body, headers=headers)
            PROXY_REQUESTS.labels(service, resp.status_code).inc()
            PROXY_LATENCY.labels(service).observe(time.time() - start)
            return Response(content=resp.content, status_code=resp.status_code,
                            media_type=resp.headers.get("content-type", "application/json"))
        except httpx.RequestError as e:
            PROXY_REQUESTS.labels(service, 503).inc()
            logger.error(f"Service {service} unreachable: {e}")
            raise HTTPException(503, f"Service '{service}' is unavailable")

# ─── Gateway Routes ───────────────────────────────────────────────────────────
@app.get("/health", tags=["Gateway"])
async def health():
    statuses = {}
    async with httpx.AsyncClient(timeout=3) as client:
        for name, url in SERVICES.items():
            try:
                r = await client.get(f"{url}/health")
                statuses[name] = {"status": "up" if r.status_code == 200 else "degraded", "code": r.status_code}
            except:
                statuses[name] = {"status": "down"}
    return {"gateway": "healthy", "services": statuses, "timestamp": datetime.utcnow().isoformat()}

@app.get("/metrics", tags=["Gateway"])
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# Auth routes (public)
@app.api_route("/auth/{path:path}", methods=["GET","POST","PUT","PATCH","DELETE"])
async def auth_proxy(path: str, request: Request):
    return await proxy("auth", f"/{path}", request)

# Task routes (protected)
@app.api_route("/tasks/{path:path}", methods=["GET","POST","PUT","PATCH","DELETE"])
@app.api_route("/tasks", methods=["GET","POST"])
async def task_proxy(request: Request, user=Depends(verify_token), path: str = ""):
    full_path = f"/tasks/{path}" if path else "/tasks"
    if request.url.path.startswith("/tasks/"):
        full_path = request.url.path[len("/tasks"):]
        full_path = "/tasks" + full_path
    else:
        full_path = request.url.path
    return await proxy("tasks", full_path, request, user)

# Subjects & stats routes  
@app.api_route("/subjects/{path:path}", methods=["GET","POST","DELETE"])
@app.api_route("/subjects", methods=["GET","POST"])
@app.api_route("/stats", methods=["GET"])
async def subjects_proxy(request: Request, user=Depends(verify_token), path: str = ""):
    return await proxy("tasks", request.url.path, request, user)

# Notification routes
@app.api_route("/notifications/{path:path}", methods=["GET","POST","PUT","PATCH","DELETE"])
@app.api_route("/notifications", methods=["GET","POST","PUT","PATCH","DELETE"])
async def notif_proxy(request: Request, user=Depends(verify_token), path: str = ""):
    return await proxy("notifications", request.url.path, request, user)

@app.get("/services", tags=["Gateway"])
async def list_services(user=Depends(verify_token)):
    return {"services": list(SERVICES.keys()), "registry": SERVICES}
