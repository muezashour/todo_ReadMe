# 📚 StudyOS — Production-Grade Study Planner Microservice Stack

A full-stack microservice system demonstrating Clean Architecture, REST API Design, Containerization, CI/CD, Observability, Security, and Distributed Systems patterns.

---

## 🏗 Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Client / Browser                     │
│              frontend/index.html (HTML+CSS+JS)          │
└──────────────────────┬──────────────────────────────────┘
                       │  HTTP
┌──────────────────────▼──────────────────────────────────┐
│             API Gateway  :8000                          │
│   • JWT verification     • Rate limiting (100/min/IP)   │
│   • Request logging      • Service routing (proxy)      │
│   • X-Request-ID tracing • X-User-* header injection    │
└────────┬─────────────┬──────────────┬───────────────────┘
         │ REST        │ REST         │ REST
┌────────▼──┐  ┌───────▼──┐  ┌───────▼──────────────────┐
│  Task Svc │  │ Auth Svc │  │  Notification Svc         │
│   :8001   │  │  :8002   │  │       :8003               │
│  Tasks    │  │  JWT     │  │  In-app notifications     │
│  Subjects │  │  Users   │  │  Async event queue        │
│  Stats    │  │  Tokens  │  │  (RabbitMQ in prod)       │
└───────────┘  └──────────┘  └───────────────────────────┘
      │ publishes events (async messaging)
      └──────────────────────────► Notification Queue

┌───────────────────────────────────────────────────────┐
│              Observability Stack                      │
│   Prometheus :9090  →  Grafana :3000                  │
│   Loki :3100 (log aggregation)                        │
│   JSON structured logs from all services              │
└───────────────────────────────────────────────────────┘
```

---

## 📁 Project Structure

```
study-planner/
├── services/
│   ├── task-service/          # Core CRUD service (FastAPI)
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── auth-service/          # JWT auth & user management
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── notification-service/  # Alerts + async event queue
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── api-gateway/           # Routing, auth, rate limiting
│       ├── main.py
│       ├── requirements.txt
│       └── Dockerfile
├── frontend/
│   └── index.html             # Single-file SPA (HTML + CSS + JS)
├── monitoring/
│   ├── prometheus.yml         # Scrape config for all services
│   └── grafana/
│       └── provisioning/
│           └── datasources/
│               └── prometheus.yaml
├── k8s/
│   └── deployment.yaml        # K8s Deployments, Services, HPA
├── tests/
│   └── test_all_services.py   # 20+ pytest test cases
├── .github/
│   └── workflows/
│       └── cicd.yml           # 6-stage GitHub Actions pipeline
├── docker-compose.yml
└── README.md
```

---

## 🚀 Quick Start

### Option A — Docker Compose (Recommended)

```bash
git clone https://github.com/yourorg/study-planner
cd study-planner

docker-compose up --build

# Services:
# API Gateway:          http://localhost:8000
# Task Service:         http://localhost:8001
# Auth Service:         http://localhost:8002
# Notification Service: http://localhost:8003
# Prometheus:           http://localhost:9090
# Grafana:              http://localhost:3000  (admin/admin)
```

### Option B — Run locally (dev)

```bash
pip install fastapi uvicorn httpx bcrypt PyJWT prometheus-client pydantic

# Each in a separate terminal:
cd services/task-service         && uvicorn main:app --reload --port 8001
cd services/auth-service         && uvicorn main:app --reload --port 8002
cd services/notification-service && uvicorn main:app --reload --port 8003
cd services/api-gateway          && uvicorn main:app --reload --port 8000

# Then open frontend/index.html in your browser
```

---

## 🧪 Demo Credentials

| Username | Password  | Role    |
|----------|-----------|---------|
| demo     | demo1234  | student |

---

## 📡 API Reference

### Auth Service (:8002)

| Method | Endpoint   | Auth | Description              |
|--------|------------|------|--------------------------|
| POST   | /register  | No   | Register new user        |
| POST   | /login     | No   | Login → JWT tokens       |
| POST   | /verify    | JWT  | Verify access token      |
| POST   | /refresh   | No   | Refresh access token     |
| POST   | /logout    | No   | Invalidate refresh token |
| GET    | /me        | JWT  | Current user profile     |
| GET    | /health    | No   | Health check             |
| GET    | /metrics   | No   | Prometheus metrics       |

**Login example:**
```bash
curl -X POST http://localhost:8002/login \
  -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"demo1234"}'
```

### Task Service (:8001)

| Method | Endpoint       | Description              |
|--------|----------------|--------------------------|
| GET    | /tasks         | List tasks (filterable)  |
| POST   | /tasks         | Create task              |
| GET    | /tasks/{id}    | Get task by ID           |
| PATCH  | /tasks/{id}    | Update task              |
| DELETE | /tasks/{id}    | Delete task              |
| GET    | /subjects      | List subjects            |
| POST   | /subjects      | Create subject           |
| GET    | /stats         | Aggregated analytics     |
| GET    | /health        | Health check             |
| GET    | /metrics       | Prometheus metrics       |

**Create task example:**
```bash
curl -X POST http://localhost:8001/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Study Distributed Systems",
    "subject": "Computer Science",
    "due_date": "2025-06-01",
    "priority": "high",
    "estimated_hours": 5,
    "tags": ["study", "exam"]
  }'
```

**Query filters:**
```
GET /tasks?status=pending
GET /tasks?priority=critical
GET /tasks?subject=Algorithms
```

### Notification Service (:8003)

| Method | Endpoint                          | Description               |
|--------|-----------------------------------|---------------------------|
| GET    | /notifications/{user_id}          | Get user notifications    |
| POST   | /notifications                    | Create notification       |
| PATCH  | /notifications/{id}/read          | Mark as read              |
| PATCH  | /notifications/{user_id}/read-all | Mark all read             |
| POST   | /events                           | Publish async event       |
| GET    | /events/queue-status              | Queue depth & stats       |
| GET    | /health                           | Health check              |

**Publish async event:**
```bash
curl -X POST http://localhost:8003/events \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "task_due",
    "payload": {
      "user_id": "demo-user",
      "title": "Assignment Due Tomorrow",
      "message": "Complete your OS lab before midnight"
    },
    "source_service": "task-service"
  }'
```

### API Gateway (:8000)

All routes proxied through gateway with JWT auth + rate limiting:

| Prefix           | Forwarded To         | Auth Required |
|------------------|----------------------|---------------|
| /auth/*          | auth-service:8002    | No (public)   |
| /tasks*          | task-service:8001    | Yes           |
| /subjects*       | task-service:8001    | Yes           |
| /stats           | task-service:8001    | Yes           |
| /notifications/* | notification-service | Yes           |
| /health          | aggregated           | No            |
| /metrics         | gateway only         | No            |

---

## 🏛 Design Patterns

### 1. Clean Architecture

```
Routes      (HTTP layer — FastAPI handlers)
  └── Service Logic (business rules, validation)
        └── Repository  (data access — in-memory dict)
              └── Models (Pydantic domain entities)
```

### 2. API Gateway Pattern

Single ingress handling:
- **JWT Verification** — calls auth-service `/verify` before forwarding
- **Rate Limiting** — sliding window, 100 req/min per IP
- **Routing** — path-prefix → service URL mapping
- **Header Enrichment** — injects `X-User-ID`, `X-Username`, `X-User-Role` downstream

### 3. Service-to-Service Communication

**Synchronous REST:**
```
API Gateway ──HTTP──► Auth Service        (token verification)
API Gateway ──HTTP──► Task Service        (proxied CRUD)
API Gateway ──HTTP──► Notification Service (proxied alerts)
```

**Asynchronous Messaging (simulated):**
```
Task Service ──POST /events──► Notification Service
                                   └── in-process deque
                                         └── background asyncio task (consumer)
```

Upgrading to real message broker in production:
```python
# RabbitMQ (pika)
channel.basic_publish(exchange='', routing_key='notifications', body=json.dumps(event))

# Apache Kafka (confluent-kafka)
producer.produce('study-events', key=user_id, value=json.dumps(event))
```

---

## 🔐 Security Design

### Authentication & Authorization
- **JWT HS256** — configurable expiry (default 60 min)
- **Refresh token rotation** — old tokens invalidated on use
- **Password hashing** — bcrypt with auto-generated salt per user
- **Role-based claims** — `role` embedded in JWT (`student`, `admin`)
- **Stateless verification** — gateway verifies tokens without DB lookup

### Rate Limiting
- Sliding window counter: 100 requests/minute/IP
- Enforced at gateway for all services
- Returns `429 Too Many Requests` with `retry_after` field

### Secrets Management
- **Dev:** environment variables in `docker-compose.yml`
- **Staging/Prod:** Kubernetes Secrets + External Secrets Operator → HashiCorp Vault
- Never commit secrets to git (TruffleHog scan in CI)

### Container Security
- Non-root user in every Dockerfile
- Minimal `python:3.12-slim` base image
- No secrets in image layers
- Trivy CVE scanning in CI pipeline

---

## 📊 Observability

### Metrics (Prometheus → Grafana)

| Metric | Type | Description |
|--------|------|-------------|
| `task_requests_total` | Counter | Requests by method/endpoint/status |
| `task_request_duration_seconds` | Histogram | Latency distribution |
| `tasks_created_total` | Counter | New tasks created |
| `tasks_completed_total` | Counter | Tasks marked complete |
| `auth_login_attempts_total` | Counter | Logins by result |
| `auth_tokens_issued_total` | Counter | JWTs issued |
| `notifications_sent_total` | Counter | Notifications by type |
| `gateway_requests_total` | Counter | Proxied requests by service |
| `gateway_rate_limited_total` | Counter | Blocked requests |

### Structured JSON Logging

All services emit machine-parseable logs to stdout:
```json
{
  "time": "2025-05-01T12:00:00.123",
  "level": "INFO",
  "service": "task-service",
  "message": "method=POST path=/tasks status=201 duration=0.012s"
}
```
Collected by Loki, queryable in Grafana.

### Health Checks

Every service: `GET /health`
```json
{ "status": "healthy", "service": "task-service", "tasks_count": 42 }
```

Gateway aggregates all: `GET /health`
```json
{
  "gateway": "healthy",
  "services": {
    "tasks":         { "status": "up" },
    "auth":          { "status": "up" },
    "notifications": { "status": "up" }
  }
}
```

### Distributed Tracing
- Every request gets a `X-Request-ID` UUID header
- Propagated downstream through the gateway
- In production: replace with OpenTelemetry + Jaeger spans

---

## 🔄 CI/CD Pipeline

Six-stage GitHub Actions (`.github/workflows/cicd.yml`):

```
git push → main branch
     │
     ▼ Stage 1 ── Lint
     │             Ruff (PEP8 + unused imports)
     │             MyPy (type checking)
     ▼ Stage 2 ── Test
     │             pytest — 20+ test cases across all 3 services
     │             Validates: CRUD, auth flow, async events, validation errors
     ▼ Stage 3 ── Security Scan
     │             Trivy  — CVE scan (filesystem + image)
     │             TruffleHog — secrets scan (no leaked API keys)
     ▼ Stage 4 ── Build & Push (matrix: 4 services in parallel)
     │             docker build + push → ghcr.io/yourorg/study-planner-*:sha
     ▼ Stage 5 ── Deploy to Staging
     │             kubectl apply -f k8s/ -n staging
     │             rollout status wait
     │             smoke test: curl /health
     ▼ Stage 6 ── Deploy to Production
                  Rolling update (maxUnavailable=0)
                  Automatic rollback on failure
```

---

## ☸️ Kubernetes Deployment

```bash
# Create namespace
kubectl create namespace study-planner

# Apply all manifests
kubectl apply -f k8s/deployment.yaml -n study-planner

# Watch rollout
kubectl rollout status deployment/task-service -n study-planner

# Scale manually
kubectl scale deployment task-service --replicas=5 -n study-planner

# HPA auto-scales 2–10 replicas at 70% CPU
kubectl get hpa -n study-planner

# Tail logs
kubectl logs -l app=task-service -n study-planner --follow

# Port-forward for local testing
kubectl port-forward svc/api-gateway 8000:80 -n study-planner
```

---

## 🧪 Running Tests

```bash
pip install pytest pytest-asyncio httpx fastapi pydantic bcrypt PyJWT prometheus-client

# All tests
pytest tests/test_all_services.py -v

# With coverage report
pip install pytest-cov
pytest tests/ --cov=services --cov-report=html
open htmlcov/index.html
```

**Test coverage includes:**
- Task CRUD: create, read, update, delete, filter by status/priority
- Validation: missing fields, invalid enums, boundary values
- Auth: register, login, wrong password, duplicate user, token verify, /me endpoint
- Notifications: create, mark read, mark-all-read, async event publish
- Health: all three services

---

## 🔧 Environment Variables

| Variable          | Service       | Default                      | Description          |
|-------------------|---------------|------------------------------|----------------------|
| `JWT_SECRET`      | auth-service  | `supersecret-change-in-prod` | JWT signing key      |
| `LOG_LEVEL`       | all services  | `INFO`                       | Logging verbosity    |
| `ENV`             | all services  | `production`                 | Environment tag      |
| `GRAFANA_PASSWORD`| grafana       | `admin`                      | Grafana admin pass   |

---

## 🚧 Production Upgrade Path

| Component        | This Demo         | Production Recommendation           |
|------------------|-------------------|-------------------------------------|
| Database         | In-memory dict    | PostgreSQL + SQLAlchemy + Alembic   |
| Message Queue    | In-process deque  | Apache Kafka or RabbitMQ            |
| Secrets          | Env vars          | HashiCorp Vault + K8s ESO           |
| Auth             | Custom JWT        | Keycloak or Auth0                   |
| Tracing          | Request-ID header | OpenTelemetry + Jaeger              |
| Log Shipping     | stdout JSON       | Fluent Bit → Loki or Elasticsearch  |
| Service Discovery| Hardcoded URLs    | K8s DNS or Consul                   |
| Config           | Env vars          | ConfigMaps + Secrets                |

---

## 📐 Concepts Demonstrated

| Phase | Concept | Location |
|-------|---------|----------|
| 1 | Clean Architecture | `services/task-service/main.py` (layered) |
| 1 | REST API Design | All services (verbs, status codes, validation) |
| 1 | Containerization | All `Dockerfile`s (slim, non-root, healthcheck) |
| 1 | Observability | `/metrics`, `/health`, JSON logs |
| 1 | Security Basics | JWT, bcrypt, CORS, input validation |
| 2 | Microservice Architecture | 3 domain services + gateway |
| 2 | REST Communication | Gateway → Services (sync) |
| 2 | Async Messaging | Task → Notification via event queue |
| 2 | API Gateway Pattern | `services/api-gateway/main.py` |
| 2 | Auth & Authorization | JWT, role claims, gateway enforcement |
| 3 | CI/CD Pipeline | `.github/workflows/cicd.yml` (6 stages) |
| 3 | Container Orchestration | `k8s/deployment.yaml` (HPA, rolling) |
| 3 | Automated Tests | `tests/test_all_services.py` (20+ cases) |
| 3 | Environment Config | Env vars, K8s Secrets, ConfigMaps |
| 4 | Rate Limiting | Gateway sliding-window limiter |
| 4 | Secrets Management | Env → K8s Secrets → Vault (path) |
| 4 | Monitoring Dashboard | Prometheus + Grafana stack |
| 4 | Log Aggregation | Loki + structured JSON |
