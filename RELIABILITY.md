# Reliability & Observability Report — StudyOS

## 1. Health Checks

Every service exposes `GET /health`. The API Gateway aggregates them:

```json
// GET http://localhost:8000/health
{
  "gateway": "healthy",
  "services": {
    "tasks":         { "status": "up",   "code": 200 },
    "auth":          { "status": "up",   "code": 200 },
    "notifications": { "status": "down", "code": 503 }
  },
  "timestamp": "2025-05-01T12:00:00Z"
}
```

**Kubernetes probes:**
- `livenessProbe`: if service is stuck, K8s restarts pod
- `readinessProbe`: removes pod from load balancer rotation during startup

---

## 2. Metrics (Prometheus)

| Metric | Type | Labels | Description |
|---|---|---|---|
| `task_requests_total` | Counter | method, endpoint, status | HTTP request count |
| `task_request_duration_seconds` | Histogram | endpoint | Request latency (p50/p95/p99) |
| `tasks_created_total` | Counter | — | Business metric |
| `tasks_completed_total` | Counter | — | Business metric |
| `auth_login_attempts_total` | Counter | status | Security metric |
| `auth_tokens_issued_total` | Counter | — | Auth throughput |
| `notifications_sent_total` | Counter | type | Notification volume |
| `gateway_requests_total` | Counter | service, status | Gateway routing |
| `gateway_proxy_duration_seconds` | Histogram | service | Upstream latency |
| `gateway_rate_limited_total` | Counter | — | Security metric |

**Grafana Alerts (recommended):**
```
ALERT HighErrorRate
  WHEN rate(task_requests_total{status=~"5.."}[5m]) > 0.05
  FOR 2m → notify on-call

ALERT AuthFailureSpike
  WHEN rate(auth_login_attempts_total{status="failed"}[5m]) > 10
  FOR 1m → potential brute force

ALERT ServiceDown
  WHEN up{job="task-service"} == 0
  FOR 1m → critical alert
```

---

## 3. Centralized Logging

All services emit structured JSON to stdout. Loki collects and indexes by:
- `service`: filter by microservice
- `level`: ERROR/WARN/INFO
- `method` + `path`: API patterns
- `status`: HTTP response codes

**Sample Grafana LogQL queries:**
```
# All errors across services
{level="ERROR"} |= ""

# Task service 5xx responses
{service="task-service"} |= "status=5"

# Rate limit events
{service="api-gateway"} |= "Rate limited"

# Slow requests (>500ms)
{service="task-service"} | json | duration > 0.5
```

---

## 4. Distributed Tracing

Every request gets an `X-Request-ID` UUID header (generated at gateway). This ID flows through all downstream service logs, enabling:

```
Request ID: abc-123-def
  Gateway     → forwarded to task-service
  Task Service → processed, returned 200
  Gateway     → returned to client
  All logs tagged with abc-123-def → fully traceable
```

**Full OpenTelemetry** integration (production upgrade):
```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
```
Send traces to **Jaeger** or **Tempo** (Grafana-native).

---

## 5. Failure Handling Strategies

### Circuit Breaker (Gateway → Downstream)
Current: `httpx` timeout (15s) returns `503 Service Unavailable`

Production upgrade with `circuitbreaker` library:
```python
@circuit(failure_threshold=5, recovery_timeout=30)
async def call_task_service(path, request):
    ...
```
States: CLOSED → OPEN (fail fast) → HALF-OPEN (probe) → CLOSED

### Retry Logic
```python
# Exponential backoff on transient failures
async with httpx.AsyncClient() as client:
    for attempt in range(3):
        try:
            return await client.get(url, timeout=5)
        except httpx.TimeoutException:
            await asyncio.sleep(2 ** attempt)
```

### Graceful Degradation
If the notification service is down:
- Task service still creates/updates tasks successfully
- Notifications are queued and retried when service recovers
- Users see tasks without notification delivery

### Kubernetes Self-Healing
- Pod crashes → K8s restarts automatically (liveness probe)
- Deployment rollout fails → automatic rollback (`kubectl rollout undo`)
- Node failure → pods rescheduled on healthy nodes
- HPA scales out under load, scales in when quiet

---

## 6. Monitoring Dashboard Layout (Grafana)

**Row 1 — System Health**
- Service up/down status
- Request rate (req/s) per service
- Error rate (%) per service

**Row 2 — Performance**
- p50/p95/p99 latency per endpoint
- Gateway proxy duration by upstream service

**Row 3 — Business Metrics**
- Tasks created per hour
- Tasks completed per hour
- Active users (login events)

**Row 4 — Security**
- Auth failure rate
- Rate-limited requests
- JWT token issuance rate

**Row 5 — Logs**
- Live log stream (Loki datasource)
- Error log panel
