import os
import requests

NOTION_VERSION = "2022-06-28"


def _base():
    return os.environ.get("NOTION_API_BASE", "https://api.notion.com/v1").rstrip("/")


def _headers():
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def create_page(database_id, properties):
    resp = requests.post(
        f"{_base()}/pages",
        headers=_headers(),
        json={"parent": {"database_id": database_id}, "properties": properties},
    )
    resp.raise_for_status()
    return resp.json()


def page_exists_by_source_id(db_id, source_id):
    resp = requests.post(
        f"{_base()}/databases/{db_id}/query",
        headers=_headers(),
        json={"filter": {"property": "Source ID", "rich_text": {"equals": source_id}}},
    )
    resp.raise_for_status()
    return len(resp.json().get("results", [])) > 0


def page_exists_by_date(db_id, date_str):
    resp = requests.post(
        f"{_base()}/databases/{db_id}/query",
        headers=_headers(),
        json={"filter": {"property": "Date", "date": {"equals": date_str}}},
    )
    resp.raise_for_status()
    return len(resp.json().get("results", [])) > 0
