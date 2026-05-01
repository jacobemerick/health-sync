from datetime import datetime


def parse_date(date_str):
    """Parse 'YYYY-MM-DD HH:MM:SS ±HHMM' (or shorter forms) to 'YYYY-MM-DD'."""
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return date_str[:10]
