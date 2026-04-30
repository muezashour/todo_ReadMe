import os
import time
from datetime import date, timedelta
import json

import httpx
import psycopg2


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")

NOTIF_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://notification-service:8003").rstrip("/")
INTERVAL_SECONDS = int(os.getenv("REMINDER_INTERVAL_SECONDS", "300"))


def fetch_due_tomorrow_tasks(conn):
    due = date.today() + timedelta(days=1)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, user_id, title, subject, due_date
            FROM tasks
            WHERE due_date = %s
              AND status NOT IN ('completed', 'cancelled')
            """,
            (due,),
        )
        return due, cur.fetchall()


def main():
    print(f"[reminder-worker] starting interval={INTERVAL_SECONDS}s notif_url={NOTIF_URL}")
    while True:
        try:
            with psycopg2.connect(DATABASE_URL) as conn:
                due, rows = fetch_due_tomorrow_tasks(conn)
            if rows:
                with httpx.Client(timeout=10) as client:
                    for task_id, user_id, title, subject, due_date in rows:
                        idempotency_key = f"task_due_1d:{task_id}:{due_date.isoformat()}"
                        payload = {
                            "user_id": str(user_id),
                            "type": "task_due",
                            "title": "Task due tomorrow",
                            "message": f"\"{title}\" ({subject}) is due on {due_date.isoformat()}",
                            "metadata": {
                                "task_id": str(task_id),
                                "due_date": due_date.isoformat(),
                                "subject": subject,
                                "idempotency_key": idempotency_key,
                            },
                            "idempotency_key": idempotency_key,
                        }
                        try:
                            r = client.post(
                                f"{NOTIF_URL}/notifications",
                                headers={"X-User-ID": str(user_id)},
                                json=payload,
                            )
                            if r.status_code >= 400:
                                print(f"[reminder-worker] notif error status={r.status_code} body={r.text}")
                        except Exception as e:
                            print(f"[reminder-worker] notif exception: {e}")
            else:
                print(f"[reminder-worker] due={due.isoformat()} no tasks")
        except Exception as e:
            print(f"[reminder-worker] loop error: {e}")

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

