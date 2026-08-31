#!/usr/bin/env python3
"""
Moodle Deadline Organizer Agent
--------------------------------
Logs in to a Moodle instance (via Moodle's official Web Services API — the
same one the Moodle mobile app uses), pulls upcoming assignments/quizzes/
events, prioritizes them by urgency, and prints/emails a weekly overview.

Credentials are read from environment variables (or a local .env file) —
never hardcode them in this file or commit them to git.
"""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


MOODLE_URL = os.environ.get("MOODLE_URL", "").rstrip("/")
MOODLE_USERNAME = os.environ.get("MOODLE_USERNAME", "")
MOODLE_PASSWORD = os.environ.get("MOODLE_PASSWORD", "")
MOODLE_TOKEN = os.environ.get("MOODLE_TOKEN", "")  # optional: skip login if already have one
MOODLE_SERVICE = os.environ.get("MOODLE_SERVICE", "moodle_mobile_app")

LOOKAHEAD_DAYS = int(os.environ.get("LOOKAHEAD_DAYS", "30"))

# Urgency thresholds (days remaining)
HIGH_PRIORITY_DAYS = 3
MEDIUM_PRIORITY_DAYS = 7


class MoodleAuthError(RuntimeError):
    pass


class MoodleAPIError(RuntimeError):
    pass


@dataclass
class DeadlineItem:
    name: str
    course: str
    activity_type: str
    due: datetime
    url: str

    @property
    def days_remaining(self) -> float:
        delta = self.due - datetime.now(timezone.utc)
        return delta.total_seconds() / 86400

    @property
    def priority(self) -> str:
        days = self.days_remaining
        if days < 0:
            return "OVERDUE"
        if days <= HIGH_PRIORITY_DAYS:
            return "HIGH"
        if days <= MEDIUM_PRIORITY_DAYS:
            return "MEDIUM"
        return "LOW"


def get_token() -> str:
    """Obtain a Moodle web service token via username/password login."""
    if MOODLE_TOKEN:
        return MOODLE_TOKEN

    if not (MOODLE_URL and MOODLE_USERNAME and MOODLE_PASSWORD):
        raise MoodleAuthError(
            "Missing credentials. Set MOODLE_URL, MOODLE_USERNAME, MOODLE_PASSWORD "
            "(or MOODLE_TOKEN) as environment variables."
        )

    resp = requests.get(
        f"{MOODLE_URL}/login/token.php",
        params={
            "username": MOODLE_USERNAME,
            "password": MOODLE_PASSWORD,
            "service": MOODLE_SERVICE,
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    if "token" not in data:
        error = data.get("error", "Unknown error")
        raise MoodleAuthError(
            f"Moodle login failed: {error}. "
            "If your institution disables the mobile web service, ask an admin "
            "to enable Web Services + a service token for your account, and set "
            "MOODLE_TOKEN instead of MOODLE_USERNAME/MOODLE_PASSWORD."
        )
    return data["token"]


def call_ws(token: str, function: str, **params) -> dict:
    """Call a Moodle Web Service function."""
    payload = {
        "wstoken": token,
        "wsfunction": function,
        "moodlewsrestformat": "json",
        **params,
    }
    resp = requests.post(f"{MOODLE_URL}/webservice/rest/server.php", data=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("exception"):
        raise MoodleAPIError(f"{function} failed: {data.get('message')}")
    return data


def fetch_deadlines(token: str) -> list[DeadlineItem]:
    """Pull upcoming action events (assignments, quizzes, etc.) with due dates."""
    now = datetime.now(timezone.utc)
    time_from = int(now.timestamp())
    time_to = int((now + timedelta(days=LOOKAHEAD_DAYS)).timestamp())

    data = call_ws(
        token,
        "core_calendar_get_action_events_by_timesort",
        timesortfrom=time_from,
        timesortto=time_to,
        limitnum=100,
    )

    items: list[DeadlineItem] = []
    for event in data.get("events", []):
        due_ts = event.get("timesort") or event.get("timestart")
        if not due_ts:
            continue
        due = datetime.fromtimestamp(due_ts, tz=timezone.utc)
        course = (event.get("course") or {}).get("fullname", "Unknown course")
        items.append(
            DeadlineItem(
                name=event.get("name", "Untitled"),
                course=course,
                activity_type=event.get("modulename", event.get("eventtype", "event")),
                due=due,
                url=event.get("url", ""),
            )
        )

    items.sort(key=lambda i: i.due)
    return items


def fetch_overdue(token: str) -> list[DeadlineItem]:
    """Pull anything already overdue (last 14 days) so nothing slips through."""
    now = datetime.now(timezone.utc)
    time_from = int((now - timedelta(days=14)).timestamp())
    time_to = int(now.timestamp())

    data = call_ws(
        token,
        "core_calendar_get_action_events_by_timesort",
        timesortfrom=time_from,
        timesortto=time_to,
        limitnum=100,
    )

    items: list[DeadlineItem] = []
    for event in data.get("events", []):
        if event.get("action", {}).get("itemcount", 0) == 0:
            continue  # nothing outstanding for this event (e.g. already submitted)
        due_ts = event.get("timesort") or event.get("timestart")
        if not due_ts:
            continue
        due = datetime.fromtimestamp(due_ts, tz=timezone.utc)
        course = (event.get("course") or {}).get("fullname", "Unknown course")
        items.append(
            DeadlineItem(
                name=event.get("name", "Untitled"),
                course=course,
                activity_type=event.get("modulename", event.get("eventtype", "event")),
                due=due,
                url=event.get("url", ""),
            )
        )
    items.sort(key=lambda i: i.due)
    return items


def build_report(deadlines: list[DeadlineItem], overdue: list[DeadlineItem]) -> str:
    lines = []
    today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    lines.append(f"WEEKLY ACADEMIC OVERVIEW — {today}")
    lines.append("=" * 50)

    if overdue:
        lines.append("\n⚠ OVERDUE (action needed now)")
        lines.append("-" * 50)
        for item in overdue:
            lines.append(f"  [{item.course}] {item.name} — was due {item.due.strftime('%d %b %Y, %H:%M UTC')}")

    if not deadlines:
        lines.append(f"\nNo upcoming deadlines in the next {LOOKAHEAD_DAYS} days. 🎉")
        return "\n".join(lines)

    buckets = {"HIGH": [], "MEDIUM": [], "LOW": []}
    for item in deadlines:
        buckets.setdefault(item.priority, []).append(item)

    labels = {
        "HIGH": f"🔴 HIGH PRIORITY (due within {HIGH_PRIORITY_DAYS} days)",
        "MEDIUM": f"🟡 MEDIUM PRIORITY (due within {MEDIUM_PRIORITY_DAYS} days)",
        "LOW": "🟢 LOW PRIORITY (further out)",
    }

    for key in ("HIGH", "MEDIUM", "LOW"):
        items = buckets.get(key, [])
        if not items:
            continue
        lines.append(f"\n{labels[key]}")
        lines.append("-" * 50)
        for item in items:
            due_str = item.due.strftime("%a %d %b, %H:%M UTC")
            days = item.days_remaining
            lines.append(
                f"  [{item.course}] {item.name} ({item.activity_type})\n"
                f"      due {due_str}  ({days:.1f} days left)"
            )

    lines.append("\n" + "=" * 50)
    lines.append(f"Total upcoming: {len(deadlines)}  |  Overdue: {len(overdue)}")
    return "\n".join(lines)


def send_email(report: str) -> None:
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    email_to = os.environ.get("EMAIL_TO")
    email_from = os.environ.get("EMAIL_FROM", smtp_user)

    if not (smtp_host and smtp_user and smtp_password and email_to):
        print("Email not configured (SMTP_HOST/SMTP_USER/SMTP_PASSWORD/EMAIL_TO) — skipping send.")
        return

    msg = MIMEText(report)
    msg["Subject"] = "Your Weekly Moodle Deadline Overview"
    msg["From"] = email_from
    msg["To"] = email_to

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(email_from, [email_to], msg.as_string())
    print(f"Report emailed to {email_to}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Moodle weekly deadline organizer")
    parser.add_argument("--email", action="store_true", help="Send the report via email (see .env.example)")
    parser.add_argument("--out", help="Write the report to this file path")
    args = parser.parse_args()

    try:
        token = get_token()
        deadlines = fetch_deadlines(token)
        overdue = fetch_overdue(token)
    except (MoodleAuthError, MoodleAPIError, requests.RequestException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    report = build_report(deadlines, overdue)
    print(report)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\nReport written to {args.out}")

    if args.email:
        send_email(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
