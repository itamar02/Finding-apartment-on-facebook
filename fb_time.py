"""Parse Facebook's timestamp tooltip text. Standard library only, because it is
imported both by scan_facebook.py and by fb_harness_scan.py (inside browser-harness)."""
import re
from datetime import datetime

EN_FORMATS = ["%A, %B %d, %Y at %I:%M %p", "%A, %d %B %Y at %H:%M", "%B %d, %Y at %I:%M %p"]
HE_MONTHS = {"ינואר": 1, "פברואר": 2, "מרץ": 3, "מרס": 3, "אפריל": 4, "מאי": 5, "יוני": 6, "יולי": 7,
             "אוגוסט": 8, "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11, "דצמבר": 12}


def parse_fb_timestamp(raw):
    """Convert Facebook's hover tooltip to ISO 8601 (local time). Returns None if unparseable.

    e.g. "Friday, October 2, 2026 at 5:50 PM" -> "2026-10-02T17:50+03:00"
    """
    if not raw:
        return None
    s = raw.replace(" ", " ").replace("‏", "").replace("‎", "").strip()
    for fmt in EN_FORMATS:
        try:
            return datetime.strptime(s, fmt).astimezone().isoformat(timespec="minutes")
        except ValueError:
            pass
    # Hebrew, e.g. "יום שישי, 2 באוקטובר 2026 בשעה 17:50"
    m = re.search(r"(\d{1,2})\s+ב?(\S+)\s+(\d{4})\D+(\d{1,2}):(\d{2})", s)
    if m and m.group(2) in HE_MONTHS:
        d = datetime(int(m.group(3)), HE_MONTHS[m.group(2)], int(m.group(1)), int(m.group(4)), int(m.group(5)))
        return d.astimezone().isoformat(timespec="minutes")
    return None
