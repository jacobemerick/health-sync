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


def archive_page(page_id):
    resp = requests.patch(
        f"{_base()}/pages/{page_id}",
        headers=_headers(),
        json={"archived": True},
    )
    resp.raise_for_status()
    return resp.json()


def _query(db_id, filter_body):
    resp = requests.post(
        f"{_base()}/databases/{db_id}/query",
        headers=_headers(),
        json={"filter": filter_body},
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def find_pages_by_source_id(db_id, source_id):
    return _query(db_id, {"property": "Source ID", "rich_text": {"equals": source_id}})


def find_pages_by_date(db_id, date_str):
    return _query(db_id, {"property": "Date", "date": {"equals": date_str}})


def _keep_earliest(pages):
    """Split pages that should have been unique into (keeper, losers).

    Sorted deterministically so that two concurrent invocations independently
    pick the same keeper and archive the same losers.
    """
    ordered = sorted(
        pages, key=lambda p: (p.get("created_time") or "", p.get("id") or "")
    )
    return ordered[0], ordered[1:]


def create_page_once(db_id, properties, find_existing):
    """Create a page unless an equivalent one already exists.

    Health Auto Export sometimes POSTs the same payload twice, and the existence
    check and the create are not atomic, so two concurrent invocations could both
    pass the check and both insert. After creating we re-query and archive every
    duplicate but the earliest; concurrent callers sort the same way, so they
    agree on the keeper and a double archive is harmless.

    This is currently the *only* defence, and it is not airtight — a page that
    Notion's query index hasn't picked up yet won't be seen by the verification
    query. Serialising the function with reserved concurrency is the real fix;
    see the note in serverless.yml for why it isn't deployed.

    Returns "created" if this call's page is the one that survived, else
    "skipped".
    """
    if find_existing():
        return "skipped"

    created = create_page(db_id, properties)

    duplicates = find_existing()
    if len(duplicates) > 1:
        keeper, losers = _keep_earliest(duplicates)
        for page in losers:
            print(f"Archiving duplicate page {page.get('id')}")
            archive_page(page["id"])
        if keeper.get("id") != created.get("id"):
            return "skipped"

    return "created"
