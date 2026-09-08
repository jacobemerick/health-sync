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


def update_page(page_id, properties):
    resp = requests.patch(
        f"{_base()}/pages/{page_id}",
        headers=_headers(),
        json={"properties": properties},
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


_TEXT_TYPES = ("title", "rich_text")


def _comparable(prop):
    """Reduce a Notion property to a scalar we can compare.

    Query results come back in Notion's read shape and the properties we build
    are in its write shape, so the two never compare equal as raw dicts — a
    title is `[{"plain_text": ...}]` coming out and `[{"text": {"content": ...}}]`
    going in. Flattening both to the underlying value is what lets us tell an
    actual change from a round trip.
    """
    if not isinstance(prop, dict):
        return None

    kind = prop.get("type") or next(iter(prop), None)
    value = prop.get(kind)
    if value is None:
        return None

    if kind in _TEXT_TYPES:
        return (
            "".join(
                part.get("plain_text") or part.get("text", {}).get("content", "")
                for part in value
            )
            or None
        )
    if kind == "date":
        return value.get("start")
    if kind == "select":
        return value.get("name")
    return value


def _changed_properties(page, properties):
    """The subset of `properties` that differs from what `page` already holds."""
    current = page.get("properties")
    if current is None:
        # Without the current values we cannot tell a change from a no-op, and
        # guessing would mean overwriting blind. Real query results always carry
        # properties, so this is a defensive branch rather than a live one.
        return {}

    return {
        name: value
        for name, value in properties.items()
        if _comparable(current.get(name)) != _comparable(value)
    }


def upsert_page(db_id, properties, find_existing):
    """Create the row for this key, or bring the existing one up to date.

    Health Auto Export sends a running snapshot of the current day, so the first
    payload of a morning lands a row holding only what had synced by then. Under
    the old create-or-skip rule every later payload for that date was dropped, and
    the row kept the morning's numbers for good: 2026-09-07 was created at 04:07
    local with 41 minutes of sleep and nothing else, and stayed that way through a
    15-hour hike.

    Only properties whose value actually differs are written, so a re-sent payload
    is a no-op rather than a pointless write, and a date that has stopped changing
    settles down to zero writes.

    Concurrency: two invocations racing on the same date compute the same diff and
    PATCH the same values, so the update path is naturally idempotent. The create
    path keeps the archive-the-duplicates net described in `create_page_once`.

    Returns "created", "updated", or "skipped".
    """
    existing = find_existing()

    if existing:
        # Match the keeper `create_page_once` would have settled on, so that if a
        # duplicate pair is still lurking we update the one that survives cleanup.
        target, _ = _keep_earliest(existing)
        changed = _changed_properties(target, properties)
        if not changed:
            return "skipped"
        print(f"Updating {sorted(changed)} on page {target.get('id')}")
        update_page(target["id"], changed)
        return "updated"

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
