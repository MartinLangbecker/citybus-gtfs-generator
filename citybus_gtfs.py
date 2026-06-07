"""
CityBus.gr GTFS Generator
Generates a GTFS feed from the CityBus.gr REST API for any supported Greek city.

Usage:
    python citybus_gtfs.py --subdomain katerini
    python citybus_gtfs.py --subdomain patra --token <JWT>

Validate output:
    https://gtfs-validator.mobilitydata.org/  (drag & drop the zip)

TODO:
    - Add --cache flag to save API responses locally and skip re-fetching
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import zipfile
from itertools import groupby
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE_URL = "https://rest.citybus.gr/api/v1/en"


def fetch_token(subdomain):
    """Extract Bearer token from the citybus.gr frontend HTML."""
    url = f"https://{subdomain}.citybus.gr/el/stops"
    req = Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    with urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8")
    m = re.search(r"const token = '([^']+)'", html)
    if not m:
        raise RuntimeError("Could not extract token from frontend HTML")
    return m.group(1)


def api_get(city_id, endpoint, token, subdomain, retries=3):
    url = f"{BASE_URL}/{city_id}/{quote(endpoint, safe='/')}"
    req = Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    req.add_header("Origin", f"https://{subdomain}.citybus.gr")
    req.add_header("Referer", f"https://{subdomain}.citybus.gr/")
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 404:
                return None
            raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise


def fetch_all_data(city_id, token, subdomain):
    print("Fetching stops...")
    stops = api_get(city_id, "stops", token, subdomain)
    print(f"  {len(stops)} stops")

    print("Fetching lines...")
    lines = api_get(city_id, "lines", token, subdomain)
    print(f"  {len(lines)} lines")

    print("Fetching routes...")
    routes = api_get(city_id, "routes", token, subdomain)
    print(f"  {len(routes)} routes")

    print("Fetching route details (stop sequences + shapes)...")
    route_details = {}
    for i, route in enumerate(routes):
        code = route["code"]
        detail = api_get(city_id, f"routes/{code}", token, subdomain)
        if detail:
            route_details[code] = detail
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(routes)}")
        time.sleep(0.05)
    print(f"  {len(route_details)} route details fetched")

    print("Fetching trip schedules per route...")
    trips_by_route = {}
    for i, code in enumerate(route_details.keys()):
        trips = api_get(city_id, f"trips/route/{code}", token, subdomain)
        if trips:
            trips_by_route[code] = trips
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(route_details)}")
        time.sleep(0.05)
    print(f"  {len(trips_by_route)} routes with trips")

    return stops, lines, routes, route_details, trips_by_route


def build_agency(route_details):
    """Extract agency info from first route detail."""
    for detail in route_details.values():
        a = detail.get("agency", {})
        url = a.get("companyUrl", "") or f"https://{a.get('subdomain','')}.citybus.gr"
        return {
            "agency_id": str(a.get("code", "1")),
            "agency_name": a.get("name", ""),
            "agency_url": url,
            "agency_timezone": "Europe/Athens",
            "agency_phone": a.get("companyPhone", ""),
            "agency_lang": "el",
        }
    return None


def build_stops(stops_data):
    rows = []
    seen = set()
    for s in stops_data:
        if s["code"] in seen:
            continue
        if not (s.get("name") or "").strip():
            continue
        seen.add(s["code"])
        rows.append({
            "stop_id": s["code"],
            "stop_name": s["name"],
            "stop_lat": f"{s['latitude']:.7f}",
            "stop_lon": f"{s['longitude']:.7f}",
        })
    return rows


def build_routes(lines, agency_id):
    rows = []
    for line in lines:
        if not line.get("routes"):
            continue
        rows.append({
            "route_id": line["code"],
            "agency_id": agency_id,
            "route_short_name": line["code"],
            "route_long_name": line["name"],
            "route_type": "3",  # Bus
            "route_color": line.get("color", "").lstrip("#"),
            "route_text_color": line.get("textColor", "").lstrip("#"),
        })
    return rows


def build_calendar():
    """Generate three service patterns: weekday, saturday, sunday."""
    return [
        {
            "service_id": "weekday",
            "monday": "1", "tuesday": "1", "wednesday": "1",
            "thursday": "1", "friday": "1", "saturday": "0", "sunday": "0",
            "start_date": "20260101",
            "end_date": "20261231",
        },
        {
            "service_id": "saturday",
            "monday": "0", "tuesday": "0", "wednesday": "0",
            "thursday": "0", "friday": "0", "saturday": "1", "sunday": "0",
            "start_date": "20260101",
            "end_date": "20261231",
        },
        {
            "service_id": "sunday",
            "monday": "0", "tuesday": "0", "wednesday": "0",
            "thursday": "0", "friday": "0", "saturday": "0", "sunday": "1",
            "start_date": "20260101",
            "end_date": "20261231",
        },
    ]


def day_to_service_id(day_num):
    """Map API day number to service_id. 0=Sunday, 1-5=weekday, 6(if exists)=Saturday."""
    if day_num == 0:
        return "sunday"
    elif day_num == 6:
        return "saturday"
    else:
        return "weekday"


def build_trips_and_stop_times(lines, route_details, trips_by_route):
    trips = []
    stop_times = []
    trip_id_counter = 0

    for line in lines:
        route_id = line["code"]
        for route_info in line.get("routes", []):
            route_code = route_info["code"]
            if route_code not in trips_by_route:
                continue
            if route_code not in route_details:
                continue

            all_entries = trips_by_route[route_code]

            # Group entries by (day, id) — entries sharing the same id belong to one trip
            keyed = sorted(all_entries, key=lambda x: (x["day"], x["id"]))
            for (day_num, trip_api_id), group in groupby(keyed, key=lambda x: (x["day"], x["id"])):
                # Only use weekday day=1 (avoid duplicates for days 2-5)
                if day_num in (2, 3, 4, 5):
                    continue

                service_id = day_to_service_id(day_num)
                entries = list(group)

                # Deduplicate entries with same stopCode and time
                seen = set()
                unique_entries = []
                for e in entries:
                    key = (e["stopCode"], e["tripTimeHour"], e["tripTimeMinute"])
                    if key not in seen:
                        seen.add(key)
                        unique_entries.append(e)
                entries = unique_entries

                if not entries:
                    continue

                trip_id_counter += 1
                tid = f"t{trip_id_counter}"
                direction_id = 0 if route_info.get("direction", 2) == 1 else 1

                trips.append({
                    "route_id": route_id,
                    "service_id": service_id,
                    "trip_id": tid,
                    "trip_headsign": route_info["name"],
                    "direction_id": str(direction_id),
                    "shape_id": route_code,
                })

                # Use position in API response as stop_sequence (API returns in correct order)
                for seq_idx, entry in enumerate(entries):
                    h = entry["tripTimeHour"]
                    m = entry["tripTimeMinute"]
                    time_str = f"{h:02d}:{m:02d}:00"
                    stop_times.append({
                        "trip_id": tid,
                        "arrival_time": time_str,
                        "departure_time": time_str,
                        "stop_id": entry["stopCode"],
                        "stop_sequence": str(seq_idx + 1),
                    })

    return trips, stop_times


def build_shapes(route_details):
    rows = []
    for route_code, detail in route_details.items():
        points = detail.get("points", [])
        for pt in sorted(points, key=lambda x: x["sequence"]):
            rows.append({
                "shape_id": route_code,
                "shape_pt_lat": pt["latitude"],
                "shape_pt_lon": pt["longitude"],
                "shape_pt_sequence": str(pt["sequence"]),
            })
    return rows


def write_csv(zipf, filename, fieldnames, rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    zipf.writestr(filename, buf.getvalue().encode("utf-8"))


def generate_gtfs(city_id, token, subdomain, output_path):
    stops_data, lines, routes, route_details, trips_by_route = fetch_all_data(city_id, token, subdomain)

    print("\nBuilding GTFS...")
    agency = build_agency(route_details)
    stops = build_stops(stops_data)
    gtfs_routes = build_routes(lines, agency["agency_id"] if agency else "1")
    calendar = build_calendar()
    shapes = build_shapes(route_details)
    trips, stop_times = build_trips_and_stop_times(lines, route_details, trips_by_route)

    feed_info = [{
        "feed_publisher_name": agency["agency_name"] if agency else "",
        "feed_publisher_url": agency["agency_url"] if agency else "",
        "feed_lang": "el",
        "feed_start_date": "20260101",
        "feed_end_date": "20261231",
        "feed_version": time.strftime("%Y%m%d"),
        "feed_contact_url": agency["agency_url"] if agency else "",
    }]

    print(f"  agency: 1")
    print(f"  stops: {len(stops)}")
    print(f"  routes: {len(gtfs_routes)}")
    print(f"  trips: {len(trips)}")
    print(f"  stop_times: {len(stop_times)}")
    print(f"  shapes: {len(shapes)} points")

    print(f"\nWriting {output_path}...")
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        write_csv(zipf, "agency.txt",
                  ["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_phone", "agency_lang"],
                  [agency] if agency else [])
        write_csv(zipf, "feed_info.txt",
                  ["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date", "feed_end_date", "feed_version", "feed_contact_url"],
                  feed_info)
        write_csv(zipf, "stops.txt",
                  ["stop_id", "stop_name", "stop_lat", "stop_lon"],
                  stops)
        write_csv(zipf, "routes.txt",
                  ["route_id", "agency_id", "route_short_name", "route_long_name", "route_type", "route_color", "route_text_color"],
                  gtfs_routes)
        write_csv(zipf, "calendar.txt",
                  ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"],
                  calendar)
        write_csv(zipf, "trips.txt",
                  ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "shape_id"],
                  trips)
        write_csv(zipf, "stop_times.txt",
                  ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
                  stop_times)
        write_csv(zipf, "shapes.txt",
                  ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"],
                  shapes)

    size_kb = os.path.getsize(output_path) / 1024
    print(f"Done! {size_kb:.0f} KB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate GTFS from CityBus.gr API")
    parser.add_argument("--city-id", type=int, default=None, help="Numeric city ID (auto-detected if omitted)")
    parser.add_argument("--subdomain", required=True, help="City subdomain (e.g. katerini)")
    parser.add_argument("--token", default=None, help="Bearer JWT token (auto-fetched if omitted)")
    parser.add_argument("--output", default=None, help="Output zip path (default: {subdomain}_gtfs.zip)")
    args = parser.parse_args()

    if not args.token or not args.city_id:
        print(f"Fetching config from {args.subdomain}.citybus.gr...")
        url = f"https://{args.subdomain}.citybus.gr/el/stops"
        req = Request(url)
        req.add_header("User-Agent", "Mozilla/5.0")
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")

        if not args.token:
            m = re.search(r"const token = '([^']+)'", html)
            if not m:
                parser.error("Could not extract token from frontend HTML")
            args.token = m.group(1)
            print(f"  Token: {args.token[:20]}...")

        if not args.city_id:
            m = re.search(r"const agencyCode = (\d+)", html)
            if not m:
                parser.error("Could not auto-detect city-id; provide --city-id")
            args.city_id = int(m.group(1))
            print(f"  City ID: {args.city_id}")

    output = args.output or os.path.join("output", f"{args.subdomain}_gtfs.zip")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    generate_gtfs(args.city_id, args.token, args.subdomain, output)
