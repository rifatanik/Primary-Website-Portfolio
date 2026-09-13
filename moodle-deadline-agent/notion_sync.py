#!/usr/bin/env python3
"""
Notion sync for the Moodle Deadline Organizer
-----------------------------------------------
Reads the JSON produced by `agent.py --json` and upserts it into a Notion
database, deduplicating by Moodle's stable event id (stored in the
"Moodle Event ID" property).

Behavior:
  - New deadlines -> create a Notion page.
  - Existing deadlines whose due date/priority changed -> update the page.
  - Existing deadlines no longer returned by Moodle (submitted, deleted,
    event window passed) -> marked Status = Done, left in Notion as a record.

Requires NOTION_TOKEN (an internal integration's access token) and
NOTION_DATABASE_ID as environment variables.
"""

from __future__ import annotations

import json
import os
import sys

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")
NOTION_VERSION = "2022-06-28"
API_BASE = "https://api.notion.com/v1"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


class NotionSyncError(RuntimeError):
    pass


def _require_config() -> None:
    if not (NOTION_TOKEN and NOTION_DATABASE_ID):
        raise NotionSyncError(
            "Missing NOTION_TOKEN or NOTION_DATABASE_ID environment variables."
        )


def fetch_existing_pages() -> dict[str, dict]:
    """Return {moodle_event_id: {page_id, due_date, priority, status}} for all pages."""
    pages: dict[str, dict] = {}
    cursor = None

    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor

        resp = requests.post(
            f"{API_BASE}/databases/{NOTION_DATABASE_ID}/query",
            headers=HEADERS,
            json=body,
            timeout=30,
        )
        if not resp.ok:
            raise NotionSyncError(f"Failed to query Notion database: {resp.status_code} {resp.text}")
        data = resp.json()

        for page in data.get("results", []):
            props = page.get("properties", {})
            event_id_prop = props.get("Moodle Event ID", {}).get("rich_text", [])
            event_id = event_id_prop[0]["plain_text"] if event_id_prop else None
            if not event_id:
                continue
            due_prop = props.get("Due Date", {}).get("date")
            priority_prop = props.get("Priority", {}).get("select")
            status_prop = props.get("Status", {}).get("status")
            pages[event_id] = {
                "page_id": page["id"],
                "due_date": due_prop.get("start") if due_prop else None,
                "priority": priority_prop.get("name") if priority_prop else None,
                "status": status_prop.get("name") if status_prop else None,
            }

        cursor = data.get("next_cursor")
        if not data.get("has_more"):
            break

    return pages


def build_properties(item: dict) -> dict:
    priority = "Overdue" if item.get("overdue") else item["priority"].title()
    type_map = {"assign": "Assignment", "quiz": "Quiz", "forum": "Forum"}
    activity_type = type_map.get(item["activity_type"], "Other" if item["activity_type"] not in ("Event",) else "Event")

    props = {
        "Name": {"title": [{"text": {"content": item["name"]}}]},
        "Course": {"rich_text": [{"text": {"content": item["course"]}}]},
        "Due Date": {"date": {"start": item["due"]}},
        "Priority": {"select": {"name": priority}},
        "Type": {"select": {"name": activity_type}},
        "Moodle Event ID": {"rich_text": [{"text": {"content": item["event_id"]}}]},
    }
    if item.get("url"):
        props["Moodle URL"] = {"url": item["url"]}
    return props


def create_page(item: dict) -> None:
    body = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": build_properties(item),
    }
    resp = requests.post(f"{API_BASE}/pages", headers=HEADERS, json=body, timeout=30)
    if not resp.ok:
        raise NotionSyncError(f"Failed to create page for '{item['name']}': {resp.status_code} {resp.text}")


def update_page(page_id: str, item: dict) -> None:
    body = {"properties": build_properties(item)}
    resp = requests.patch(f"{API_BASE}/pages/{page_id}", headers=HEADERS, json=body, timeout=30)
    if not resp.ok:
        raise NotionSyncError(f"Failed to update page '{item['name']}': {resp.status_code} {resp.text}")


def mark_done(page_id: str) -> None:
    body = {"properties": {"Status": {"status": {"name": "Done"}}}}
    resp = requests.patch(f"{API_BASE}/pages/{page_id}", headers=HEADERS, json=body, timeout=30)
    if not resp.ok:
        raise NotionSyncError(f"Failed to mark page done: {resp.status_code} {resp.text}")


def sync(deadlines_json: dict) -> dict:
    _require_config()

    all_items = deadlines_json.get("upcoming", []) + deadlines_json.get("overdue", [])
    current_ids = {item["event_id"] for item in all_items if item.get("event_id")}

    existing = fetch_existing_pages()

    created, updated, completed, skipped = 0, 0, 0, 0

    for item in all_items:
        event_id = item.get("event_id")
        if not event_id:
            skipped += 1
            continue

        existing_page = existing.get(event_id)
        if existing_page is None:
            create_page(item)
            created += 1
            continue

        if existing_page.get("status") == "Done":
            continue  # user already finished it — don't resurrect

        new_due = item["due"]
        new_priority = "Overdue" if item.get("overdue") else item["priority"].title()
        if existing_page.get("due_date") != new_due or existing_page.get("priority") != new_priority:
            update_page(existing_page["page_id"], item)
            updated += 1

    for event_id, page in existing.items():
        if event_id not in current_ids and page.get("status") != "Done":
            mark_done(page["page_id"])
            completed += 1

    return {"created": created, "updated": updated, "auto_completed": completed, "skipped": skipped}


def main() -> int:
    raw = sys.stdin.read()
    try:
        deadlines_json = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON on stdin ({exc}). Pipe `agent.py --json` output into this script.", file=sys.stderr)
        return 1

    if "error" in deadlines_json:
        print(f"Error: upstream Moodle fetch failed: {deadlines_json['error']}", file=sys.stderr)
        return 1

    try:
        result = sync(deadlines_json)
    except NotionSyncError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Notion sync complete — created: {result['created']}, updated: {result['updated']}, "
        f"auto-completed: {result['auto_completed']}, skipped: {result['skipped']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
