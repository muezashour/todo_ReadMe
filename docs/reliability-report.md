# Reliability & Observability Report — StudyOS

## 1. Health Checks

Every service exposes `GET /health` returning:
```json
{ "status": "healthy", "service": "task-service", "timestamp": "...", "tasks_count": 42 }
```

The API Gateway aggregates all service health checks:
```json
{
  "gateway": "healthy",
  "services": {
    "tasks":         { "status": "up", "code": 200 },
    "auth":          { "status": "up", "code": 200 },
    "notifications": { "status": "up", "code": 200 }
  }
}
```

Kubernetes liveness and readiness probes hit `/health` every 30s to detect and replace unhealthy pods automatically.

---

## 2. Metrics (Prometheus)

All services expose Prometheus metrics at `GET /metrics`.

**Key SLIs tracked:**

| SLI | Metric | Alert Threshold |
|-----|--------|----------------|
| Request rate | `task_requests_total` rate | < 1 req/min (dead service) |
| Error rate | `task_requests_total{status=~"5.."}` | > 1% of requests |
| P99 latency | `task_request_duration_seconds` histogram | > 500ms |
| Task completion | `tasks_completed_total` | Inform only |
| Auth failures | `auth_login_attempts_total{status="failed"}` | > 10/min (brute force?) |

---

## 3. Centralized Logging

All services emit structured JSON logs:
```json
{
  "time": "2025-05-01T12:00:00",
  "level": "INFO",
  "service": "auth-service",
  "message": "method=POST path=/login status=200 duration=0.023s"
}
```

**Log aggregation pipeline (production):**
```
Service stdout
    → Docker log driver
        → Fluent Bit (collector, on each node)
            → Loki (storage)
                → Grafana (query + dashboards)
```

**Log levels used:**
- `INFO` — normal request/response
- `WARNING` — rate limit hit, unexpected input
- `ERROR` — downstream service unreachable, unhandled exception

---

## 4. Distributed Tracing

Current implementation uses `X-Request-ID` headers:
- Gateway generates a UUID per request
- Injects it as `X-Request-ID` in the response
- Downstream services log it in every line

**Production upgrade path (OpenTelemetry):**
```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

tracer = trace.get_tracer("task-service")

with tracer.start_as_current_span("create-task") as span:
    span.set_attribute("task.priority", priority)
    span.set_attribute("user.id", user_id)
    # ... business logic
```

Traces flow: Task Service → Gateway → Jaeger UI (timeline view of spans)

---

## 5. Failure Handling Strategies

### 5a. Gateway — Downstream Service Unavailable

```python
try:
    resp = await client.request(method, url, ...)
except httpx.RequestError:
    # Service is down or timing out
    raise HTTPException(503, f"Service '{service}' is unavailable")
```

Returns `503 Service Unavailable` rather than hanging or crashing.

### 5b. Gateway — Auth Service Unavailable

If auth-service is down, all protected routes fail with 503. This is intentional — the system fails secure (denies access) rather than fail open (allows unauthenticated requests).

### 5c. Kubernetes — Pod Failure

Kubernetes automatically:
- Detects failed liveness probe → restarts the pod
- Detects failed readiness probe → stops sending traffic
- Replaces terminated pods to maintain `replicas` count
- HPA adds pods when CPU > 70%

### 5d. Notification Service — Queue Overflow

The in-memory deque has `maxlen=1000`. If the queue fills (e.g. notification service consumer is slow), new events are silently dropped. In production, use RabbitMQ with dead-letter queues and consumer acknowledgment to ensure delivery.

### 5e. Rolling Deployments

```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxUnavailable: 0   # Never take a pod down before a new one is ready
    maxSurge: 1         # Spin up one extra pod during the rollout
```

Zero-downtime deploys. If the new pod fails its readiness probe, the rollout stops and the old version continues serving traffic.

---

## 6. Grafana Dashboard Panels (Recommended)

Configure the following panels in Grafana pointing at Prometheus:

| Panel | Query |
|-------|-------|
| Request Rate | `rate(task_requests_total[1m])` |
| Error Rate % | `rate(task_requests_total{status=~"5.."}[1m]) / rate(task_requests_total[1m]) * 100` |
| P95 Latency | `histogram_quantile(0.95, rate(task_request_duration_seconds_bucket[5m]))` |
| Active Tasks | `tasks_created_total - tasks_completed_total` |
| Login Rate | `rate(auth_login_attempts_total[5m])` |
| Rate Limited Requests | `rate(gateway_rate_limited_total[1m])` |
| Service Health | `up{job=~"task-service|auth-service|notification-service"}` |

---

## 7. SLO Targets

| Service | Availability SLO | Latency SLO (P99) |
|---------|-----------------|-------------------|
| API Gateway | 99.9% | < 200ms |
| Task Service | 99.5% | < 100ms |
| Auth Service | 99.9% | < 150ms |
| Notification Service | 99.0% | < 300ms |

Error budget: 99.9% availability = ~8.7 hours downtime/year allowed.
