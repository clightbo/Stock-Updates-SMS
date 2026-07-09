"""Small Notion API helpers shared by the planner and market pipelines."""

from __future__ import annotations

import os
import re
import sys

import requests

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def notion_request(method: str, path: str, payload: dict | None = None) -> dict:
    response = requests.request(
        method,
        f"{NOTION_API}{path}",
        headers={
            "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if response.status_code >= 400:
        sys.exit(
            f"error: Notion API {method} {path} failed "
            f"({response.status_code}): {response.text[:300]}"
        )
    return response.json()


def normalize_notion_id(value: str) -> str:
    """Accept a bare id, a dashed id, or a full Notion URL."""
    compact = value.split("?")[0].replace("-", "")
    runs = re.findall(r"[0-9a-f]{32,}", compact, flags=re.IGNORECASE)
    if not runs:
        sys.exit(
            "error: NOTION_PARENT_PAGE_ID doesn't look like a Notion page id or URL."
        )
    return runs[-1][-32:]


def find_database(title: str) -> str | None:
    data = notion_request("POST", "/search", {
        "query": title,
        "filter": {"value": "database", "property": "object"},
        "page_size": 20,
    })
    for result in data.get("results", []):
        db_title = "".join(
            t.get("plain_text", "") for t in result.get("title", [])
        )
        if db_title.strip() == title:
            return result["id"]
    return None


def rich_text_chunks(text: str, limit: int = 1900) -> list[dict]:
    """Split long text into Notion rich_text segments."""
    chunks = []
    while text:
        chunks.append({"type": "text", "text": {"content": text[:limit]}})
        text = text[limit:]
    return chunks or [{"type": "text", "text": {"content": ""}}]


def key_exists(db_id: str, key: str) -> bool:
    data = notion_request("POST", f"/databases/{db_id}/query", {
        "filter": {"property": "Key", "rich_text": {"equals": key}},
        "page_size": 1,
    })
    return bool(data.get("results"))


def get_or_create_database(title: str, properties: dict) -> str:
    db_id = find_database(title)
    if db_id:
        return db_id
    parent = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()
    if not parent:
        sys.exit(
            f'error: no "{title}" database found and NOTION_PARENT_PAGE_ID is not '
            "set, so I can't create one. Add the secret (see README) and re-run."
        )
    print(f'Creating Notion database "{title}"...')
    data = notion_request("POST", "/databases", {
        "parent": {"type": "page_id", "page_id": normalize_notion_id(parent)},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": properties,
    })
    return data["id"]
