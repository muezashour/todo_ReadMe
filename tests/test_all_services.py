"""
Automated Test Suite — Study Planner Microservices
Tests: Task Service, Auth Service, Notification Service
"""
import os
import time
import uuid
from datetime import date, timedelta

import pytest
import httpx


GATEWAY_URL = os.getenv("TEST_GATEWAY_URL", "http://localhost:8000").rstrip("/")


def gateway_up() -> bool:
    try:
        r = httpx.get(f"{GATEWAY_URL}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def ensure_gateway():
    # Give docker-compose a moment; avoid import-time skip flakiness.
    for _ in range(25):
        if gateway_up():
            return
        time.sleep(0.2)
    pytest.skip("Gateway not running on TEST_GATEWAY_URL / localhost:8000")


def register_user(client: httpx.Client, username: str, email: str, password: str):
    r = client.post(f"{GATEWAY_URL}/auth/register", json={"username": username, "email": email, "password": password})
    assert r.status_code in (201, 409)


def login_user(client: httpx.Client, username: str, password: str) -> dict:
    r = client.post(f"{GATEWAY_URL}/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()


def me(client: httpx.Client, access_token: str) -> dict:
    r = client.get(f"{GATEWAY_URL}/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert r.status_code == 200
    return r.json()


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestIntegrationGateway:
    def test_health(self):
        r = httpx.get(f"{GATEWAY_URL}/health", timeout=5)
        assert r.status_code == 200
        assert r.json()["gateway"] == "healthy"

    def test_user_isolation_tasks_and_subjects(self):
        with httpx.Client(timeout=10) as client:
            u1 = f"u{uuid.uuid4().hex[:8]}"
            u2 = f"u{uuid.uuid4().hex[:8]}"
            pw = "password123"
            register_user(client, u1, f"{u1}@test.com", pw)
            register_user(client, u2, f"{u2}@test.com", pw)

            t1 = login_user(client, u1, pw)["access_token"]
            t2 = login_user(client, u2, pw)["access_token"]

            # User1 creates subject + task
            r = client.post(f"{GATEWAY_URL}/subjects", json={"name": "Physics", "color": "#3b82f6"}, headers=auth_headers(t1))
            assert r.status_code in (201, 409)
            due = (date.today() + timedelta(days=2)).isoformat()
            r = client.post(
                f"{GATEWAY_URL}/tasks",
                json={"title": "User1 Task", "subject": "Physics", "due_date": due, "priority": "high", "tags": ["hw"]},
                headers=auth_headers(t1),
            )
            assert r.status_code == 201

            # User2 should not see User1 tasks/subjects
            r = client.get(f"{GATEWAY_URL}/tasks", headers=auth_headers(t2))
            assert r.status_code == 200
            assert all(t["title"] != "User1 Task" for t in r.json())

            r = client.get(f"{GATEWAY_URL}/subjects", headers=auth_headers(t2))
            assert r.status_code == 200
            assert all(s["name"] != "Physics" for s in r.json())

    def test_notification_idempotency(self):
        with httpx.Client(timeout=10) as client:
            u = f"u{uuid.uuid4().hex[:8]}"
            pw = "password123"
            register_user(client, u, f"{u}@test.com", pw)
            token = login_user(client, u, pw)["access_token"]
            profile = me(client, token)
            user_id = profile["id"]

            idem = f"test_idem:{uuid.uuid4()}"
            body = {
                "user_id": user_id,
                "type": "reminder",
                "title": "Idempotent Reminder",
                "message": "This should not duplicate",
                "metadata": {"idempotency_key": idem},
                "idempotency_key": idem,
            }

            r1 = client.post(f"{GATEWAY_URL}/notifications", json=body, headers=auth_headers(token))
            assert r1.status_code == 201
            r2 = client.post(f"{GATEWAY_URL}/notifications", json=body, headers=auth_headers(token))
            assert r2.status_code == 200
            assert r2.json().get("deduped") is True
