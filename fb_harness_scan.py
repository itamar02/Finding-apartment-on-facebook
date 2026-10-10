# Runs INSIDE browser-harness (helpers such as js, cdp, scroll, new_tab are pre-imported).
# Invoked by scan_facebook.py; do not run with plain Python.
#
# Read-only except for clicking "See more" inside a post body (expands text locally).
# Config comes from the JSON file named in $FB_SCAN_CONFIG; the result is printed as
# a single line prefixed with RESULT_MARKER (ASCII-escaped JSON).
import json
import os
import re
import sys
from datetime import datetime

RESULT_MARKER = "@@FB_SCAN_RESULT@@"

cfg = json.load(open(os.environ["FB_SCAN_CONFIG"], encoding="utf-8"))
GROUP_ID = cfg["group_id"]
KNOWN = set(cfg["known_ids"])
MAX_POSTS = cfg["max_posts"]
STOP_AFTER_KNOWN = cfg["stop_after_known"]  # also used for consecutive too-old posts
CUTOFF_ISO = cfg["cutoff_iso"]              # posts older than this are past the search window
FEED_URL = f"https://www.facebook.com/groups/{GROUP_ID}/?sorting_setting=CHRONOLOGICAL"

sys.path.insert(0, cfg["project_dir"])
from fb_time import parse_fb_timestamp  # noqa: E402  (stdlib-only module in the project)

SEE_MORE_RE = r"^(See more|ראה עוד|הצג עוד|עוד)$"

# Locates post #n (aria-posinset) and reports whether it is rendered and where it is.
LOCATE_JS = r"""(() => {
  const p = document.querySelector('[role=feed] [aria-posinset="%d"]');
  if (!p) return JSON.stringify({exists: false, maxPos: Math.max(0, ...[...document.querySelectorAll('[role=feed] [aria-posinset]')].map(e => +e.getAttribute('aria-posinset')))});
  const r = p.getBoundingClientRect();
  const rendered = !!p.querySelector('[data-ad-rendering-role="profile_name"]');
  return JSON.stringify({exists: true, rendered, top: r.top, height: r.height, vh: innerHeight, vw: innerWidth});
})()"""

# Clicks "See more" buttons that live inside the post body only (never comments).
EXPAND_JS = r"""(() => {
  const p = document.querySelector('[role=feed] [aria-posinset="%d"]');
  if (!p) return 0;
  const re = new RegExp(%s);
  let n = 0;
  for (const msg of p.querySelectorAll('[data-ad-rendering-role="story_message"]')) {
    if (msg.closest('[role=article]')) continue;
    for (const b of msg.querySelectorAll('[role=button]')) {
      if (re.test(b.innerText.trim())) { b.click(); n++; }
    }
  }
  return n;
})()"""

# The visible timestamp link: first laid-out anchor after the author link that is not a
# profile/photo/hashtag link. (data-ad-rendering-role="meta" is a hidden duplicate.)
TS_ANCHOR_FN = r"""function tsAnchor(p) {
  const anchors = [...p.querySelectorAll('a')].filter(a => !a.closest('[role=article]'));
  const prof = [...p.querySelectorAll('[data-ad-rendering-role="profile_name"]')].find(e => !e.closest('[role=article]'));
  let seen = !prof;
  for (const a of anchors) {
    if (prof && prof.contains(a)) { seen = true; continue; }
    if (!seen) continue;
    const h = a.getAttribute('href') || '';
    const r = a.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && !/\/user\/|\/photo|\/hashtag|\/profile\.php/.test(h)) return a;
  }
  return null;
}"""

EXTRACT_JS = r"""(() => {
  %s
  // Facebook draws emoji as <img alt="the emoji" src=".../emoji.php/...">, which innerText
  // skips. Put each alt text next to its image for the moment of reading, then remove it
  // again (nothing is left behind in the page). Only emoji images: photos have their own alt.
  const textOf = e => {
    const added = [];
    for (const img of e.querySelectorAll('img[src*="/emoji.php/"][alt]')) {
      const t = document.createTextNode(img.alt);
      img.after(t);
      added.push(t);
    }
    const s = e.innerText;
    added.forEach(t => t.remove());
    return s;
  };
  const p = document.querySelector('[role=feed] [aria-posinset="%d"]');
  if (!p) return JSON.stringify({missing: true});
  const own = sel => [...p.querySelectorAll(sel)].filter(e => !e.closest('[role=article]'));
  const prof = own('[data-ad-rendering-role="profile_name"]')[0];
  const authorA = prof ? prof.querySelector('a[href*="/user/"]') : null;
  const authorId = authorA ? ((authorA.getAttribute('href') || '').match(/\/user\/(\d+)/) || [])[1] : null;
  const msgs = own('[data-ad-rendering-role="story_message"]').map(textOf);
  const desc = own('[data-ad-rendering-role="description"]').map(textOf);
  const ts = tsAnchor(p);
  const mr = ts ? ts.getBoundingClientRect() : null;
  const ids = [...new Set(own('a[href]').map(a => {
    const m = (a.getAttribute('href') || '').match(/\/posts\/(\d+)|permalink\/(\d+)|(?:pcb|gm)\.(\d+)|story_fbid=(\d+)|multi_permalinks=(\d+)/);
    return m ? (m[1] || m[2] || m[3] || m[4] || m[5]) : null;
  }).filter(Boolean))];
  // Photos shown in the feed (the post's own photo links, not comments or avatars).
  const photos = [], seenSrc = new Set();
  let morePhotos = 0;
  for (const a of own('a[href*="/photo"]')) {
    const img = a.querySelector('img');
    const src = img ? img.currentSrc || img.src : '';
    if (src.startsWith('https://') && !seenSrc.has(src)) {
      seenSrc.add(src);
      photos.push({url: src, alt: img.getAttribute('alt') || null});
    }
    const more = (a.innerText || '').trim().match(/^\+(\d+)$/);
    if (more) morePhotos = +more[1];
  }
  return JSON.stringify({
    photos, morePhotos,
    author: prof ? prof.innerText.trim().split('\n')[0] : null,
    author_id: authorId || null,
    msgs, desc, ids,
    stillCollapsed: own('[data-ad-rendering-role="story_message"] [role=button]').some(b => new RegExp(%s).test(b.innerText.trim())),
    meta: mr && mr.height > 0 ? {x: mr.left + mr.width / 2, y: mr.top + mr.height / 2} : null
  });
})()"""

# After hovering the timestamp, Facebook swaps in the real permalink and shows a tooltip.
HOVER_READ_JS = r"""(() => {
  %s
  const p = document.querySelector('[role=feed] [aria-posinset="%d"]');
  const a = p ? tsAnchor(p) : null;
  const tips = [...document.querySelectorAll('[role=tooltip]')].map(e => e.innerText.trim()).filter(Boolean);
  return JSON.stringify({href: a ? a.href : null, tooltip: tips.length ? tips[tips.length - 1] : null});
})()"""

SORT_LABEL_JS = r"""(() => { const f = document.querySelector('[role=feed]'); return f && f.children[0] ? f.children[0].innerText : ''; })()"""


def jget(expr):
    """Evaluate a JS expression that returns JSON.stringify(...) and parse it."""
    return json.loads(js(expr))


OFFSCREEN = {"left": -3000, "top": 0, "width": 1280, "height": 1000}  # same as bot_chrome.py


def show_window():
    """Make the scanner's tab drawable without showing it to the user.

    Chrome stops rendering pages in a minimized window, and then mouse input times out.
    A minimized bot window is restored and parked off-screen, where Chrome keeps
    rendering (the bot Chrome runs with native occlusion detection off).
    """
    tab = current_tab()
    win = cdp("Browser.getWindowForTarget", targetId=tab["targetId"])
    if win["bounds"].get("windowState") == "minimized":
        cdp("Browser.setWindowBounds", windowId=win["windowId"], bounds={"windowState": "normal"})
        cdp("Browser.setWindowBounds", windowId=win["windowId"], bounds=OFFSCREEN)
        wait(0.5)
    activate_tab(tab)


def mouse(fn, *args, **kwargs):
    """Run a mouse action (scroll / move); on timeout show the window and retry once."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        if "timed out" not in str(e):
            raise
        show_window()
        wait(1.5)
        return fn(*args, **kwargs)


def move_mouse(x, y):
    cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)


SEE_LESS_TAIL = re.compile(r"\s*(See less|ראה פחות|הצג פחות)\s*$")


def clean_text(parts):
    """Join post-body blocks, dropping duplicates and the trailing "See less" label."""
    out = []
    for t in parts:
        # The collapse button's label is appended to the expanded body's last line.
        t = SEE_LESS_TAIL.sub("", t).strip()
        if t and t not in out:
            out.append(t)
    return "\n\n".join(out)


def open_feed():
    """Open the group's "New posts" feed in the scanner's tab, bring it to the front, wait for posts.

    One working tab is reused for all groups: any tab already showing a group feed with
    sorting_setting=CHRONOLOGICAL is navigated to this group; otherwise a tab is created.
    Returns the feed's URL path, used later to detect accidental navigation.
    """
    tabs = [t for t in list_tabs()
            if "facebook.com/groups/" in t["url"] and "sorting_setting=CHRONOLOGICAL" in t["url"]]
    if tabs:
        switch_tab(tabs[0]["targetId"])
        goto_url(FEED_URL)
    else:
        new_tab(FEED_URL)
    wait_for_load()
    # The bot Chrome keeps rendering when covered or off-screen, but not when minimized.
    show_window()
    if not wait_for_element('[role=feed] [aria-posinset="1"]', timeout=20):
        if js("location.pathname.startsWith('/login') || !!document.querySelector('input[name=email], input[name=pass]')"):
            raise RuntimeError("not logged in to Facebook in this browser")
        raise RuntimeError("feed did not load (not a member, or page changed?)")
    wait(1.5)
    return js("location.pathname")


def bring_into_view(n):
    """Scroll with the mouse wheel until post n is rendered and its header is on screen."""
    stalls = 0
    last_max = -1
    for _ in range(60):
        info = jget(LOCATE_JS % n)
        if not info["exists"]:
            # Not loaded yet: scroll down to trigger loading more posts.
            if info["maxPos"] == last_max:
                stalls += 1
                if stalls >= 10:
                    return False
            else:
                stalls, last_max = 0, info["maxPos"]
            mouse(scroll, 480, 400, dy=900)
            wait(1.2)
            continue
        if info["rendered"] and 60 <= info["top"] <= info["vh"] * 0.45:
            return True
        dy = info["top"] - 120
        dy = max(-1500, min(1500, dy))
        mouse(scroll, info["vw"] / 2, info["vh"] / 2, dy=dy if abs(dy) > 20 else 40)
        wait(0.7)
    return False


def scan_post(n, feed_path):
    """Read post n (already on screen): expand it, extract fields, hover the timestamp.

    Hovering the timestamp makes Facebook reveal the real /posts/<id>/ link and a
    tooltip with the exact date. Returns None if the post vanished from the DOM.
    """
    for _ in range(3):
        if js(EXPAND_JS % (n, json.dumps(SEE_MORE_RE))) == 0:
            break
        wait(0.8)
    if js("location.pathname") != feed_path:
        raise RuntimeError("navigation away from the group detected after expanding a post")
    d = jget(EXTRACT_JS % (TS_ANCHOR_FN, n, json.dumps(SEE_MORE_RE)))
    if d.get("missing"):
        return None
    hover = {"href": None, "tooltip": None}
    if d["meta"]:
        mouse(move_mouse, d["meta"]["x"], d["meta"]["y"])
        for _ in range(8):
            wait(0.35)
            hover = jget(HOVER_READ_JS % (TS_ANCHOR_FN, n))
            if hover["tooltip"] and hover["href"] and "/posts/" in hover["href"]:
                break
        mouse(move_mouse, 5, 5)
        wait(0.3)
    post_id = None
    if hover["href"]:
        m = re.search(r"/posts/(\d+)|permalink/(\d+)", hover["href"])
        if m:
            post_id = m.group(1) or m.group(2)
    if not post_id and len(d["ids"]) == 1:
        post_id = d["ids"][0]
    text = clean_text(d["msgs"]) or clean_text(d["desc"])
    return {
        "pos": n,
        "post_id": post_id,
        "author": d["author"],
        "author_id": d["author_id"],
        "timestamp_raw": hover["tooltip"],
        "text": text,
        "truncated": d["stillCollapsed"],
        "photos": d["photos"],          # [{url, alt}] as shown in the feed
        "more_photos": d["morePhotos"],  # count hidden behind a "+N" tile
    }


result = {"posts": [], "unidentified": 0, "stop_reason": None, "sort_label": None, "error": None}
try:
    feed_path = open_feed()
    result["sort_label"] = js(SORT_LABEL_JS).replace("\n", " ").strip()
    cutoff = datetime.fromisoformat(CUTOFF_ISO)
    consecutive_known = consecutive_old = 0
    seen_this_run = set()
    n = 0
    while True:
        n += 1
        if len(result["posts"]) >= MAX_POSTS:
            result["stop_reason"] = "max_posts"
            break
        if not bring_into_view(n):
            result["stop_reason"] = "end_of_feed"
            break
        post = scan_post(n, feed_path)
        if post is None:
            continue
        if not post["post_id"]:
            result["unidentified"] += 1
            continue
        if post["post_id"] in seen_this_run:
            continue
        seen_this_run.add(post["post_id"])
        result["posts"].append(post)
        consecutive_known = consecutive_known + 1 if post["post_id"] in KNOWN else 0
        if KNOWN and consecutive_known >= STOP_AFTER_KNOWN:
            result["stop_reason"] = "reached_known_posts"
            break
        # Feed is newest-first; a few consecutive old posts (not one pinned post) end the window.
        posted = parse_fb_timestamp(post["timestamp_raw"])
        consecutive_old = consecutive_old + 1 if posted and datetime.fromisoformat(posted) < cutoff else 0
        if consecutive_old >= STOP_AFTER_KNOWN:
            result["stop_reason"] = "reached_old_posts"
            break
except Exception as e:
    result["error"] = f"{type(e).__name__}: {e}"

print(RESULT_MARKER + json.dumps(result, ensure_ascii=True))
