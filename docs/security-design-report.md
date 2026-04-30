# Security Design Report — StudyOS Microservice Stack

## Executive Summary

This document outlines the security architecture of the StudyOS platform across all four phases of the assignment. The system follows a defense-in-depth strategy with security controls at every layer: authentication, authorization, transport, secrets, container hardening, and CI/CD scanning.

---

## 1. Authentication

### JWT (JSON Web Tokens)

- **Algorithm:** HS256 (symmetric HMAC-SHA256)
- **Access token expiry:** 60 minutes (configurable)
- **Refresh token expiry:** 7 days
- **Claims:** `sub` (user ID), `username`, `role`, `iat` (issued-at), `exp` (expiry), `jti` (unique JWT ID)

**Token lifecycle:**
```
Login → Access Token (60min) + Refresh Token (7d)
         │
         ├── Use Access Token for API calls
         │
         ├── On expiry → POST /auth/refresh (rotates both tokens)
         │
         └── POST /auth/logout → Refresh token blacklisted
```

**Why HS256 and not RS256?**
HS256 is simpler for a single-tenant system where the signing and verifying party is the same service. In a multi-tenant or federated system, RS256 (asymmetric) is preferred so the public key can be shared without exposing the signing secret.

---

## 2. Password Security

- **Algorithm:** bcrypt with cost factor 12 (auto-selected by bcrypt library)
- **Salt:** auto-generated per password (never reused)
- **Storage:** only the hash is stored, never the plaintext
- **Verification:** constant-time comparison via `bcrypt.checkpw`

bcrypt is intentionally slow (designed to resist brute-force at scale), unlike MD5/SHA-1 which are cryptographic hashes not designed for passwords.

---

## 3. Authorization

### Gateway-Level Enforcement

All protected routes pass through the API Gateway, which:
1. Extracts the Bearer token from the `Authorization` header
2. Calls auth-service `POST /verify` synchronously
3. Injects `X-User-ID`, `X-Username`, `X-User-Role` headers downstream
4. Rejects with 401 if token is invalid or expired

### Public Routes (no auth required)
```
/health
/metrics
/docs
/auth/login
/auth/register
```

### Role-Based Access (future extension)
The JWT payload includes a `role` field (`student`, `admin`). Downstream services can read `X-User-Role` from the header injected by the gateway to enforce fine-grained permissions without re-verifying the token.

---

## 4. Rate Limiting

**Algorithm:** Sliding window counter per client IP

```python
# Pseudocode
window_start = now - 60 seconds
requests = [t for t in ip_requests if t > window_start]
if len(requests) >= 100:
    return 429 Too Many Requests
```

- **Limit:** 100 requests per minute per IP
- **Scope:** enforced at the gateway (all services protected)
- **Response:** `{"error": "Rate limit exceeded", "retry_after": 60}`
- **Headers:** `X-RateLimit-Remaining` (future improvement)

In production, replace the in-memory counter with Redis for distributed rate limiting across multiple gateway instances.

---

## 5. Secrets Management

### Current (Dev/Demo)
- Secrets passed as environment variables in `docker-compose.yml`
- `.env` file excluded from git via `.gitignore`

### Staging
- Kubernetes Secrets (base64-encoded, etcd-encrypted at rest)
- Mounted as environment variables into pods

### Production (Recommended)
```
Developer  →  Vault CLI / UI  →  HashiCorp Vault
                                      │
                              External Secrets Operator
                                      │
                               Kubernetes Secrets
                                      │
                                  Pod env vars
```

**Vault policies should follow least-privilege**: each service gets a Vault role that can only read its own secrets.

**Secret rotation plan:**
| Secret | Rotation Frequency | Method |
|--------|--------------------|--------|
| JWT signing key | 90 days | Rolling restart after key update |
| DB passwords | 30 days | Vault dynamic credentials |
| API keys | On compromise | Immediate revocation + rotation |

---

## 6. Container Security

All Dockerfiles implement:

```dockerfile
# Non-root user
RUN addgroup --system app && adduser --system --group app
USER app

# Minimal attack surface
FROM python:3.12-slim  # not :latest, not full Debian

# No secrets in layers
# (secrets injected at runtime via env vars, never COPY'd)

# Health checks for liveness probes
HEALTHCHECK --interval=30s --timeout=10s CMD ...
```

**Image scanning:** Trivy runs in CI on every push to `main`, checking:
- OS package CVEs
- Python dependency CVEs  
- Misconfigurations (running as root, exposed secrets)

---

## 7. CI/CD Security Controls

The GitHub Actions pipeline includes two security stages:

**Stage 3a — Trivy (CVE Scanning)**
- Scans the filesystem before build
- Uploads results as SARIF to GitHub Security tab
- Fails pipeline on HIGH/CRITICAL CVEs (configurable)

**Stage 3b — TruffleHog (Secrets Scanning)**
- Scans git history for accidentally committed secrets
- Detects API keys, passwords, tokens, private keys
- Runs on every PR and push to main

---

## 8. Transport Security

- **Docker Compose (dev):** HTTP only (acceptable for local dev)
- **Kubernetes (prod):** TLS termination at Ingress Controller (nginx/traefik)
  - Services communicate over cluster-internal HTTP (mTLS optional via Istio/Linkerd)
  - External traffic: HTTPS with auto-renewed Let's Encrypt certificates

---

## 9. Input Validation

All API inputs are validated by Pydantic v2 before reaching business logic:

```python
class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    priority: str = Field("medium", pattern="^(low|medium|high|critical)$")
    estimated_hours: Optional[float] = Field(None, ge=0.1, le=100)
```

- **Type coercion:** Pydantic rejects wrong types with 422
- **Enum validation:** regex patterns for status/priority fields
- **Length limits:** prevent excessively large payloads
- **Injection prevention:** no raw SQL (in-memory store), no shell execution

---

## 10. CORS Policy

All services allow `*` origins in development. In production, restrict to known frontend domains:

```python
app.add_middleware(CORSMiddleware,
    allow_origins=["https://studyos.yourdomain.com"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
```

---

## 11. Threat Model Summary

| Threat | Mitigation |
|--------|-----------|
| Credential theft | bcrypt hashing, JWT expiry, token rotation |
| Token forgery | HS256 signature verification, short expiry |
| Brute force login | Rate limiting (100 req/min/IP) |
| Privilege escalation | Role claims in JWT, gateway enforcement |
| Secret leakage | TruffleHog CI scan, Vault in prod |
| Container escape | Non-root user, minimal base image |
| CVE exploitation | Trivy scanning, dependency pinning |
| MITM | TLS at ingress (prod), HTTPS enforcement |
| Injection attacks | Pydantic validation, no raw SQL/shell |
| DDoS | Rate limiting, HPA auto-scaling |

---

## 12. Known Limitations (Demo Scope)

The following are acceptable simplifications for a demo environment that must be addressed before a real production deployment:

1. **In-memory user store** — survives only until service restart; use PostgreSQL in production
2. **HS256 instead of RS256** — acceptable for single-service auth; use RS256 or JWKS for federation
3. **No refresh token blacklist persistence** — blacklist lives in memory; use Redis in production
4. **No HTTPS** — add TLS termination at the Kubernetes Ingress layer
5. **No audit log** — add append-only audit trail of auth events to a separate service/DB
6. **No MFA** — add TOTP or WebAuthn as a second factor for sensitive operations
