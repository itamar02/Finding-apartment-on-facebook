# Runs INSIDE browser-harness (helpers such as js, scroll, goto_url, new_tab are pre-imported).
# Invoked by discover_groups.py; do not run with plain Python.
#
# Read-only: opens "Your groups" and Facebook's group search, scrolls, and reads the group
# links shown. It never clicks Join or anything else. Config comes from the JSON file named in
# $FB_DISCOVER_CONFIG; the result is printed as one line prefixed with RESULT_MARKER.
import json
import os
import re
import urllib.parse

RESULT_MARKER = "@@FB_DISCOVER_RESULT@@"

cfg = json.load(open(os.environ["FB_DISCOVER_CONFIG"], encoding="utf-8"))
QUERIES = cfg["queries"]
MY_GROUPS = cfg["my_groups"]
MAX_PER_QUERY = cfg["max_per_query"]

NOT_GROUPS = {"feed", "discover", "joins", "create", "search", "notifications", "you"}

# Every group link on the page with its own text and the text of the smallest box around it
# that holds a few lines (a search result: name, "Public · 60K members · ...", Join).
COLLECT_JS = r"""(() => {
  const skip = new Set(%s);
  const scopes = %s;
  const out = {};
  for (const a of document.querySelectorAll(scopes.map(s => s + ' a[href*="/groups/"]').join(', '))) {
    const m = (a.getAttribute('href') || '').match(/\/groups\/([^/?#]+)\/?(?:[?#]|$)/);
    if (!m || skip.has(m[1])) continue;
    let box = a;
    for (let i = 0; i < 8 && box.parentElement; i++) {
      box = box.parentElement;
      if (box.innerText.split('\n').filter(Boolean).length >= 3) break;
    }
    const rec = out[m[1]] || (out[m[1]] = {id: m[1], texts: [], box: ''});
    const t = (a.innerText || '').trim();
    if (t) rec.texts.push(t.slice(0, 300));
    if (!rec.box || box.innerText.length < rec.box.length) rec.box = box.innerText.slice(0, 600);
  }
  return JSON.stringify(Object.values(out));
})()"""

BUTTON_WORDS = re.compile(r"^(Join|Visit|View group|Joined|הצטרפות|הצטרף|ביקור|הצגת הקבוצה|כניסה לקבוצה)$", re.I)
ACTIVE_LINE = re.compile(r"^(Last active|You last visited|פעילות אחרונה|ביקרת לאחרונה)", re.I)
META_LINE = re.compile(r"(Public|Private|ציבורית|פרטית)\s*·", re.I)


OFFSCREEN = {"left": -3000, "top": 0, "width": 1280, "height": 1000}  # same as bot_chrome.py


def show_window():
    """Keep the tab drawable: a minimized bot window is restored and parked off-screen
    (Chrome stops rendering minimized windows, and then scrolling times out)."""
    tab = current_tab()
    win = cdp("Browser.getWindowForTarget", targetId=tab["targetId"])
    if win["bounds"].get("windowState") == "minimized":
        cdp("Browser.setWindowBounds", windowId=win["windowId"], bounds={"windowState": "normal"})
        cdp("Browser.setWindowBounds", windowId=win["windowId"], bounds=OFFSCREEN)
        wait(0.5)
    activate_tab(tab)


def page_scroll(dy):
    """Scroll with the mouse wheel; on a timeout show the window and retry once."""
    try:
        scroll(640, 500, dy=dy)
    except Exception as e:
        if "timed out" not in str(e):
            raise
        show_window()
        wait(1.5)
        scroll(640, 500, dy=dy)


def open_page(url):
    """Show `url` in a Facebook tab other than the scanner's group feed if possible."""
    tabs = [t for t in list_tabs() if "facebook.com" in t["url"] and "sorting_setting=CHRONOLOGICAL" not in t["url"]]
    if tabs:
        switch_tab(tabs[0]["targetId"])
        goto_url(url)
    else:
        new_tab(url)
    wait_for_load()
    show_window()
    wait(3)
    if js("location.pathname.startsWith('/login') || !!document.querySelector('input[name=email], input[name=pass]')"):
        raise RuntimeError("not logged in to Facebook in this browser")


def members(text):
    """'60K members' / '1.2M members' / '850 members' / '12 אלף חברים' -> int, else None."""
    m = re.search(r"([\d.,]+)\s*([KkMm]|אלף|מיליון)?\s*(members|חברים|חברות)", text)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    mult = {"k": 1e3, "m": 1e6, "אלף": 1e3, "מיליון": 1e6}.get((m.group(2) or "").lower(), 1)
    return int(n * mult)


def parse(rec, from_search):
    """Group fields from the link text and its box."""
    lines = [l.strip() for l in rec["box"].split("\n") if l.strip()]
    name = None
    for t in rec["texts"]:
        first = t.split("\n")[0].strip()
        if first and not BUTTON_WORDS.match(first):
            name = first
            break
    meta = next((l for l in lines if META_LINE.search(l)), "")
    if not name and meta:
        idx = lines.index(meta)
        name = lines[idx - 1] if idx > 0 else None
    privacy = None
    if re.search(r"Public|ציבורית", meta, re.I):
        privacy = "public"
    elif re.search(r"Private|פרטית", meta, re.I):
        privacy = "private"
    buttons = [l for l in lines if BUTTON_WORDS.match(l)]
    is_member = None
    if not from_search:
        is_member = 1
    elif buttons:
        is_member = 0 if re.match(r"^(Join|הצטרפות|הצטרף)$", buttons[-1], re.I) else 1
    description = None
    if meta:
        rest = [l for l in lines[lines.index(meta) + 1:] if not BUTTON_WORDS.match(l) and not ACTIVE_LINE.match(l)]
        description = rest[0][:300] if rest else None
    return {"id": rec["id"], "name": name, "member_count": members(meta), "privacy": privacy,
            "is_member": is_member, "description": description}


def collect(url, limit, from_search):
    """Scroll a page at a human pace and collect the groups listed on it."""
    open_page(url)
    found = {}
    stalls = 0
    for _ in range(40):
        # Search results live in the main column; "Your groups" also lists them in the side bar.
        scopes = ["[role=main]"] if from_search else ["[role=main]", "[role=navigation]"]
        for rec in json.loads(js(COLLECT_JS % (json.dumps(sorted(NOT_GROUPS)), json.dumps(scopes)))):
            g = parse(rec, from_search)
            if g["name"] and (g["id"] not in found or (g["member_count"] and not found[g["id"]]["member_count"])):
                found[g["id"]] = g
        if len(found) >= limit:
            break
        before = js("document.scrollingElement.scrollHeight")
        page_scroll(1200)
        wait(2.0)
        stalls = stalls + 1 if js("document.scrollingElement.scrollHeight") == before else 0
        if stalls >= 3:
            break
    return list(found.values())[:limit]


result = {"my_groups": [], "searches": [], "error": None}
try:
    if MY_GROUPS:
        result["my_groups"] = collect("https://www.facebook.com/groups/joins/?nav_source=tab", 500, from_search=False)
    for q in QUERIES:
        url = "https://www.facebook.com/search/groups/?q=" + urllib.parse.quote(q)
        result["searches"].append({"query": q, "groups": collect(url, MAX_PER_QUERY, from_search=True)})
        wait(3)
except Exception as e:
    result["error"] = f"{type(e).__name__}: {e}"

print(RESULT_MARKER + json.dumps(result, ensure_ascii=True))
