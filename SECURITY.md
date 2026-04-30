# Security Design Report — StudyOS Microservices

## 1. Authentication & Authorization

### JWT (JSON Web Tokens)
- **Algorithm**: HS256 (HMAC-SHA256)
- **Access token expiry**: 60 minutes
- **Refresh token expiry**: 7 days with rotation
- **Claims**: `sub` (user ID), `username`, `role`, `exp`, `iat`, `jti`

**Token Flow:**
```
Client → POST /auth/login → { access_token, refresh_token }
Client → GET  /tasks (Authorization: Bearer <token>)
Gateway → POST /auth/verify → { valid: true, user_id, role }
Gateway → forward + X-User-ID, X-User-Role headers
```

**Refresh Token Rotation:**
- On refresh, old token is invalidated and new pair issued
- Prevents replay attacks if a refresh token is stolen

### Password Security
- **Algorithm**: bcrypt with auto-generated salt
- Cost factor: 12 rounds (~250ms hash time — resistant to brute force)
- Passwords never stored or logged in plaintext

### Role-Based Access Control
Gateway injects `X-User-Role` header. Services can enforce:
- `student`: read/write own tasks only
- `admin`: full access (future extension)

---

## 2. Rate Limiting

**Implementation**: Sliding window counter per client IP

```python
# 100 requests per 60-second window per IP
RATE_LIMIT = 100
WINDOW_SEC = 60
```

- Returns `HTTP 429 Too Many Requests` with `Retry-After` header
- Prevents DoS attacks and credential stuffing
- Counter stored in-memory (use Redis in production for distributed rate limiting)

---

## 3. Secrets Management

### Development
- Secrets in `.env.example` (never committed)
- `.env` file loaded by Docker Compose

### Staging / Production
```yaml
# Kubernetes Secrets (base64 encoded)
kubectl create secret generic app-secrets \
  --from-literal=JWT_SECRET=$(openssl rand -hex 32)
```

### Production Best Practice (recommended)
- **HashiCorp Vault** for secret rotation
- **External Secrets Operator** to sync Vault → K8s Secrets
- **AWS Secrets Manager** / **GCP Secret Manager** as alternatives
- Rotate `JWT_SECRET` every 90 days

---

## 4. Container Security

| Control | Implementation |
|---|---|
| Non-root user | `adduser --system app` in all Dockerfiles |
| Minimal base image | `python:3.12-slim` (~50MB vs 1GB full) |
| No privileged containers | Default in Compose/K8s |
| Image scanning | Trivy in CI/CD pipeline |
| Secrets not in image | All secrets via env vars |

---

## 5. Network Security

- **Internal services** communicate on Docker network `study-net` only
- **Only the gateway** is exposed on the host network
- Services are not accessible directly from outside in production (K8s ClusterIP)
- HTTPS/TLS termination at load balancer level (cert-manager + Let's Encrypt)

---

## 6. Input Validation

All inputs validated with **Pydantic v2**:
- String length limits on all fields
- Regex pattern validation (priority, status enums)
- Numeric range checks (estimated_hours ≥ 0.1)
- Type coercion and sanitization automatic

---

## 7. Vulnerability Scanning

CI/CD pipeline runs two security checks on every push:

**Trivy** — scans for:
- Known CVEs in OS packages
- Known CVEs in Python dependencies
- Secrets accidentally committed in code

**TruffleHog** — scans for:
- API keys, tokens, credentials in git history
- High-entropy strings matching secret patterns

---

## 8. Security Headers

All responses include:
- `X-Request-ID` — for audit trails
- `X-Service` — identifies which service responded
- `X-Response-Time` — performance monitoring

Production additions (nginx/ingress level):
```
Strict-Transport-Security: max-age=31536000
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Content-Security-Policy: default-src 'self'
```

---

## 9. Logging & Audit Trail

All auth events are logged in structured JSON:
```json
{"time": "...", "level": "INFO", "service": "auth-service", "message": "User logged in: username=demo"}
{"time": "...", "level": "WARN", "service": "auth-service", "message": "Failed login: username=attacker"}
{"time": "...", "level": "WARN", "service": "api-gateway",  "message": "Rate limited: ip=1.2.3.4"}
```

Prometheus tracks:
- `auth_login_attempts_total{status="failed"}` — alert on spike
- `gateway_rate_limited_total` — alert threshold
