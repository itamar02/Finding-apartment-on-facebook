"""The bot's own Chrome: a separate Chrome profile that keeps working in the background.

Usage:
    python bot_chrome.py      # start it (if needed) and open Facebook - log in there once

scan_facebook.py starts it automatically when it isn't running. Your normal Chrome is
never touched. Settings: `browser` in config.yaml.

Why separate: Chrome pauses pages in minimized or covered windows, and these settings
can only be switched off when Chrome starts. The bot Chrome is started with them off,
and with remote debugging on its own local port, so there are no "Allow remote
debugging?" prompts.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request

from apartment_bot.config import ConfigError, load_config

DAEMON_NAME = "apartmentbot"   # browser-harness connection name, separate from your normal Chrome's

BACKGROUND_FLAGS = [
    "--disable-backgrounding-occluded-windows",    # keep drawing when covered
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    "--disable-features=CalculateNativeWinOcclusion",
]


def endpoint(cfg):
    return f"http://127.0.0.1:{cfg['browser']['debug_port']}"


def is_running(cfg):
    try:
        with urllib.request.urlopen(endpoint(cfg) + "/json/version", timeout=2):
            return True
    except OSError:
        return False


# Off-screen window: Chrome keeps rendering it (unlike a minimized window), but it is
# out of your way. Same geometry as OFFSCREEN in fb_harness_scan.py.
OFFSCREEN_FLAGS = ["--window-position=-3000,0", "--window-size=1280,1000"]
ONSCREEN = {"left": 100, "top": 50, "width": 1280, "height": 1000}


def start(cfg, url="https://www.facebook.com/", visible=False):
    """Launch the bot Chrome (detached, so it outlives this script) and wait until it answers.

    visible=False parks the window off-screen (scans); True shows it (e.g. to log in).
    """
    b = cfg["browser"]
    profile = os.path.expandvars(b["profile_dir"])
    os.makedirs(profile, exist_ok=True)
    cmd = [os.path.expandvars(b["chrome_path"]), f"--user-data-dir={profile}",
           f"--remote-debugging-port={b['debug_port']}", "--no-first-run", "--no-default-browser-check",
           *BACKGROUND_FLAGS, *([] if visible else OFFSCREEN_FLAGS), url]
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    subprocess.Popen(cmd, creationflags=flags, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if is_running(cfg):
            return
        time.sleep(0.5)
    raise RuntimeError(f"bot Chrome did not start (no answer on {endpoint(cfg)})")


def ensure_running(cfg):
    """Start the bot Chrome if needed. Returns True if it was started now."""
    if is_running(cfg):
        return False
    start(cfg)
    return True


def harness_env(cfg):
    """Environment that makes browser-harness control the bot Chrome instead of your own."""
    return {"BU_NAME": DAEMON_NAME, "BU_CDP_URL": endpoint(cfg)}


def bring_on_screen(cfg):
    """Move a running bot Chrome window (possibly off-screen or minimized) into view."""
    script = (
        "for t in list_tabs(include_chrome=False)[:1]:\n"
        "    w = cdp('Browser.getWindowForTarget', targetId=t['targetId'])\n"
        "    cdp('Browser.setWindowBounds', windowId=w['windowId'], bounds={'windowState': 'normal'})\n"
        f"    cdp('Browser.setWindowBounds', windowId=w['windowId'], bounds={ONSCREEN!r})\n"
    )
    exe = shutil.which("browser-harness") or os.path.expanduser(r"~\.local\bin\browser-harness.exe")
    subprocess.run([exe], input=script.encode(), capture_output=True, timeout=60,
                   env=dict(os.environ, **harness_env(cfg)))


def main():
    """Start the bot Chrome visibly (or bring it on-screen) so you can log in or look at it."""
    try:
        cfg = load_config()
    except ConfigError as e:
        sys.exit(f"config error: {e}")
    if is_running(cfg):
        bring_on_screen(cfg)
        print("The bot Chrome window is now on screen. Log in to Facebook there if needed.")
    else:
        start(cfg, visible=True)
        print("Bot Chrome started. Log in to Facebook in the window that opened (only needed once).")
    print("Afterwards you can leave it, cover it or minimize it - scans park it off-screen and keep working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
