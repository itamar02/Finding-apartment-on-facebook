"""Set up the local public-transport planner (OpenTripPlanner on the official Israeli timetable).

    python setup_transit.py            # download everything into transit/ and build the graph (10-20 minutes)
    python setup_transit.py --rebuild  # only refresh the timetable and rebuild (do this about monthly)

Downloads: the Ministry of Transport's GTFS timetable (about 180 MB, https://gtfs.mot.gov.il), the Israel map from
OpenStreetMap (about 120 MB, Geofabrik) and OpenTripPlanner 2.7.0 (about 170 MB, GitHub). Needs Java 21+ and about
10 GB of free memory while building. Everything lives in the git-ignored transit/ folder.
"""
import argparse
import csv
import io
import subprocess
import sys
import urllib.request
import zipfile

from apartment_bot import transit

GTFS_URL = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
OSM_URL = "https://download.geofabrik.de/asia/israel-and-palestine-latest.osm.pbf"
OTP_URL = "https://github.com/opentripplanner/OpenTripPlanner/releases/download/v2.7.0/otp-shaded-2.7.0.jar"
SKIP = {"fare_rules.txt", "fare_attributes.txt", "translations.txt", "shapes.txt"}   # not needed for travel times

BUILD_CONFIG = """{
  "transitServiceStart": "-P1D",
  "transitServiceEnd": "P45D",
  "osm": [{ "source": "israel.osm.pbf", "osmTagMapping": "default", "timeZone": "Asia/Jerusalem" }],
  "transitFeeds": [{ "type": "gtfs", "feedId": "IL", "source": "gtfs.zip" }]
}
"""
ROUTER_CONFIG = """{
  "routingDefaults": {
    "walk": { "speed": 1.25 },
    "bicycle": { "speed": 5.0 }
  }
}
"""


def download(url, path):
    print(f"Downloading {url}", flush=True)
    urllib.request.urlretrieve(url, path)
    print(f"  {path.name}: {path.stat().st_size / 1e6:.0f} MB", flush=True)


def fix_gtfs(raw, fixed):
    """The official file has an agency without a web address and a non-standard route type, which OpenTripPlanner refuses; drop what is not needed."""
    zin = zipfile.ZipFile(raw)
    with zipfile.ZipFile(fixed, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zout:
        for info in zin.infolist():
            if info.filename in SKIP:
                continue
            data = zin.read(info.filename)
            if info.filename == "agency.txt":
                rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
                for r in rows:
                    r["agency_url"] = (r.get("agency_url") or "").strip() or "https://www.gov.il"
                    r["agency_timezone"] = (r.get("agency_timezone") or "").strip() or "Asia/Jerusalem"
                out = io.StringIO()
                w = csv.DictWriter(out, fieldnames=list(rows[0].keys()), lineterminator="\n")
                w.writeheader()
                w.writerows(rows)
                data = out.getvalue().encode("utf-8")
            if info.filename == "stops.txt":   # some trips stop at "stations": make every stop a plain stop
                rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
                for r in rows:
                    r["location_type"] = "0"
                    r["parent_station"] = ""
                out = io.StringIO()
                w = csv.DictWriter(out, fieldnames=list(rows[0].keys()), lineterminator="\n")
                w.writeheader()
                w.writerows(rows)
                data = out.getvalue().encode("utf-8")
            if info.filename == "routes.txt":   # type 8 (shared taxi) is not a standard type: treat it as a bus
                rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
                for r in rows:
                    if r["route_type"] == "8":
                        r["route_type"] = "3"
                out = io.StringIO()
                w = csv.DictWriter(out, fieldnames=list(rows[0].keys()), lineterminator="\n")
                w.writeheader()
                w.writerows(rows)
                data = out.getvalue().encode("utf-8")
            zout.writestr(info.filename, data)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rebuild", action="store_true", help="refresh the timetable only")
    args = ap.parse_args()
    java = transit._java()
    if not java:
        sys.exit("Java 21+ is needed (winget install Microsoft.OpenJDK.21).")
    d = transit.TRANSIT_DIR
    d.mkdir(exist_ok=True)
    raw = d / "gtfs-download.zip"
    download(GTFS_URL, raw)
    fix_gtfs(raw, d / "gtfs.zip")
    raw.unlink()
    if not args.rebuild or not (d / "israel.osm.pbf").exists():
        download(OSM_URL, d / "israel.osm.pbf")
    if not (d / "otp.jar").exists():
        download(OTP_URL, d / "otp.jar")
    (d / "build-config.json").write_text(BUILD_CONFIG, encoding="utf-8")
    (d / "router-config.json").write_text(ROUTER_CONFIG, encoding="utf-8")
    print("Building the graph (this takes a while)...", flush=True)
    if subprocess.run([java, "-Xmx14g", "-jar", str(d / "otp.jar"), "--build", "--save", str(d)]).returncode:
        sys.exit("The build failed: see the messages above.")
    print("Done. The planner starts by itself when a trip is estimated.")


if __name__ == "__main__":
    main()
