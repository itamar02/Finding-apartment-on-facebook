"""Build the Android app (APK) that opens the phone app.

Usage:
    python build_apk.py --url https://my-pc.tail1234.ts.net    # the address from `tailscale serve`
    python build_apk.py                                        # no address: the app asks for it on first start

Result: dist/ApartmentBot.apk (a debug build for your own phone). Copy it to the phone and open
it; Android asks to allow installs from this source once. The APK is only a shell around the
web app on your PC - nothing is stored in it - so rebuild only when android-app/ changes.

Needs (see the README, "Android app (APK)"): Node.js 20+, a Java 17-21 JDK (Android Studio's
bundled one is found automatically) and the Android SDK (build-tools + a platform, licenses
accepted). Set JAVA_HOME / ANDROID_HOME if they are in unusual places.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE / "android-app"
DIST = HERE / "dist"
WIN = sys.platform == "win32"


def find_java():
    home = os.environ.get("JAVA_HOME")
    exe = "java.exe" if WIN else "java"
    candidates = [Path(home)] if home else []
    candidates += [Path(p) for p in (
        r"C:\Program Files\Android\Android Studio\jbr", r"C:\Program Files\Android\Android Studio1\jbr",
        "/Applications/Android Studio.app/Contents/jbr/Contents/Home", "/opt/android-studio/jbr")]
    for c in candidates:
        if (c / "bin" / exe).exists():
            return c
    found = shutil.which("java")
    return Path(found).resolve().parent.parent if found else None


def find_sdk():
    for p in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
              str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk"),
              str(Path.home() / "Library" / "Android" / "sdk"), str(Path.home() / "Android" / "Sdk")):
        if p and (Path(p) / "platforms").exists() and (Path(p) / "build-tools").exists():
            return Path(p)
    return None


def run(cmd, cwd, env=None):
    print("  $", " ".join(map(str, cmd)), flush=True)
    return subprocess.run(cmd, cwd=cwd, env=env, shell=WIN and cmd[0] in ("npm", "npx")).returncode == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", help="the app's address (https://<pc>.<tailnet>.ts.net); default: ask on first start")
    args = ap.parse_args()

    java, sdk = find_java(), find_sdk()
    problems = []
    if not shutil.which("node"):
        problems.append("Node.js 20+ is missing (https://nodejs.org)")
    if not java:
        problems.append("no Java found: install JDK 21 (winget install Microsoft.OpenJDK.21) or Android Studio")
    if not sdk:
        problems.append("Android SDK not found: install Android Studio (or the command-line tools) and set ANDROID_HOME")
    if problems:
        print("Fix this first:\n  - " + "\n  - ".join(problems))
        return 1
    print(f"Java: {java}\nAndroid SDK: {sdk}")

    url = (args.url or "").strip().rstrip("/")
    if url and not url.startswith("https://"):
        sys.exit("--url must start with https:// (use the address `tailscale serve` printed)")
    (APP / "www" / "config.js").write_text(
        f'// Written by build_apk.py. The address can also be changed inside the app.\nwindow.APP_URL = "{url + "/" if url else ""}";\n',
        encoding="utf-8")

    env = {**os.environ, "JAVA_HOME": str(java), "ANDROID_HOME": str(sdk), "ANDROID_SDK_ROOT": str(sdk)}
    env["PATH"] = str(java / "bin") + os.pathsep + env["PATH"]
    if not (APP / "node_modules").exists() and not run(["npm", "ci", "--no-audit", "--no-fund"], APP):
        return 1
    if not (APP / "android").exists() and not run(["npx", "cap", "add", "android"], APP):
        return 1
    if not run([sys.executable, "make_icons.py"], APP) or not run(["npx", "cap", "sync", "android"], APP):
        return 1
    (APP / "android" / "local.properties").write_text(f"sdk.dir={str(sdk).replace(chr(92), '/')}\n", encoding="utf-8")
    gradlew = str(APP / "android" / ("gradlew.bat" if WIN else "gradlew"))
    if not run([gradlew, "--no-daemon", "assembleDebug"], APP / "android", env):
        print("The Android build failed - see the message above.")
        return 1
    apk = APP / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    DIST.mkdir(exist_ok=True)
    shutil.copy2(apk, DIST / "ApartmentBot.apk")
    print(f"\nDone: {DIST / 'ApartmentBot.apk'} ({apk.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
