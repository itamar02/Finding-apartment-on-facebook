"""One-time setup of the phone app on this PC, safe to run again.

Usage:
    python setup_app.py            # check, install packages, build the web app
    python setup_app.py --pair     # ...and print a login code for the phone at the end

What it does (and nothing else):
  1. checks Python 3.11+, Node.js 20+ and config.yaml (copies config.example.yaml if missing);
  2. pip install -r requirements.txt;
  3. npm ci + npm run build in web/ (the app the phone loads);
  4. looks for Tailscale and prints the two commands still to run by hand:
     the Tailscale login (it needs you) and `tailscale serve --bg 8787`.
It never touches your database, your Facebook login or your calendar.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"


def run(cmd, cwd=HERE):
    print("  $", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=cwd, shell=sys.platform == "win32" and cmd[0] in ("npm", "npx")).returncode == 0


def step(n, text):
    print(f"\n[{n}] {text}", flush=True)


def tailscale_path():
    found = shutil.which("tailscale")
    if found:
        return found
    for p in (r"C:\Program Files\Tailscale\tailscale.exe", "/usr/bin/tailscale", "/usr/local/bin/tailscale",
              "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if Path(p).exists():
            return p
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pair", action="store_true", help="print a one-time login code for the phone at the end")
    args = ap.parse_args()
    problems = []

    step(1, "Checking prerequisites")
    if sys.version_info < (3, 11):
        problems.append(f"Python 3.11+ is needed (this is {sys.version.split()[0]})")
    node = shutil.which("node")
    if not node:
        problems.append("Node.js 20+ is missing: install it from https://nodejs.org (or: winget install OpenJS.NodeJS.LTS)")
    else:
        major = int(subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v").split(".")[0])
        if major < 20:
            problems.append(f"Node.js 20+ is needed (this is {major})")
    if not (HERE / "config.yaml").exists():
        shutil.copy(HERE / "config.example.yaml", HERE / "config.yaml")
        print("  config.yaml did not exist: copied config.example.yaml. EDIT IT (profile, budget, areas, groups).")
    else:
        print("  config.yaml found.")
    if problems:
        print("\nFix this first:\n  - " + "\n  - ".join(problems))
        return 1

    step(2, "Installing Python packages")
    if not run([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"]):
        print("pip failed - see the message above.")
        return 1

    step(3, "Building the web app")
    if not run(["npm", "ci", "--no-audit", "--no-fund"], cwd=WEB) or not run(["npm", "run", "build"], cwd=WEB):
        print("The web build failed - see the message above.")
        return 1

    step(4, "Tailscale (private HTTPS address for your phone)")
    ts = tailscale_path()
    if not ts:
        print("  Tailscale is not installed. Install it on this PC and on your phone:")
        print("    Windows: winget install Tailscale.Tailscale     (or https://tailscale.com/download)")
        print("  then log in with the same account on both.")
    else:
        print(f"  Found {ts}.")
    cmd = f'"{ts}"' if ts and " " in ts else (ts or "tailscale")
    print("\nStill to do by hand:")
    print(f"  1. {cmd} up                  (log in; opens a link)")
    print("     In the Tailscale admin console (DNS page) turn on MagicDNS and HTTPS certificates.")
    print(f"  2. {cmd} serve --bg 8787     (prints the https address of the app)")
    print("  3. python app_server.py       (start the server; the first start upgrades the database, with a backup)")
    print("  4. python app_server.py --pair   (a login code to type into the app on the phone)")
    print("\nSetup finished. See README.md, 'Phone app'.")
    if args.pair:
        from apartment_bot import auth
        auth.get_token()
        print(f"\nLogin code: {auth.new_pairing_code()}  (valid for {auth.PAIRING_MINUTES} minutes, one use)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
