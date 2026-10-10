"""Run the phone app's server on this PC (API + the built web app).

Usage:
    python app_server.py                 # serve on 127.0.0.1:8787 (reach it through Tailscale)
    python app_server.py --pair          # print a one-time code to log in from the phone
    python app_server.py --reset-token   # log out every phone (makes a new token)

The server listens on this PC only; `tailscale serve` makes it reachable, over HTTPS, from
your own devices. See the README, "Phone app".
"""
import argparse
import sys

from apartment_bot import auth, db as dbmod
from apartment_bot.config import ConfigError, get_user, load_config


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this PC only)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--user", help="user id from config.yaml (default: the only user)")
    ap.add_argument("--pair", action="store_true", help="print a one-time login code for the phone and exit")
    ap.add_argument("--reset-token", action="store_true", help="make a new app token (logs out all phones) and exit")
    ap.add_argument("--insecure-cookie", action="store_true",
                    help="allow login over plain http (only for testing on this PC without Tailscale)")
    args = ap.parse_args()

    if args.reset_token:
        auth.get_token(reset=True)
        print("New app token created. Every phone has to log in again (python app_server.py --pair).")
        return 0
    if args.pair:
        auth.get_token()
        code = auth.new_pairing_code()
        print(f"Login code: {code}  (valid for {auth.PAIRING_MINUTES} minutes, one use)")
        print("Open the app on your phone and type this code.")
        return 0

    try:
        cfg = load_config()
        user = get_user(cfg, args.user)
    except ConfigError as e:
        sys.exit(f"config error: {e}")
    db, backups = dbmod.migrate()
    db.close()
    for b in backups:
        print(f"Database upgraded; backup saved as {b}")
    auth.get_token()

    import uvicorn
    from apartment_bot.server import create_app
    app = create_app(cfg, user["id"], secure_cookie=not args.insecure_cookie)
    print(f"ApartmentBot app on http://{args.host}:{args.port}  (Ctrl+C to stop)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
