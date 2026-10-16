
#!/usr/bin/env python3
"""Extract one country's Proton VPN locations from the client's own cache.

The CLI has no server-list command (`protonvpn servers` just prints a URL), but
the GTK/CLI client caches the full logical server list, ~18k entries, at
~/.cache/Proton/VPN/serverlist.json, refreshed whenever it connects. Reading it
here is far cheaper than a 1s CLI round-trip and gives us load and tier per
server, which `protonvpn connect <NAME>` then accepts directly.

Results are collapsed to ONE ROW PER CITY, keeping that city's best server.
A flat score-sorted list is useless in practice: large countries carry
thousands of servers and whichever city is nearest monopolises the entire top
of the list, so you'd scroll past hundreds of near-identical entries before
seeing a second city. One row per city is the choice a person actually wants
to make.

Usage: servers.py <COUNTRY_CODE> [limit]   one country's cities, best-first
       servers.py --cities                  every city worldwide, with lat/long
       servers.py --locate <SERVER_NAME>    one server's city and coordinates
Prints compact JSON, or [] / {} when the cache is missing.
"""

import json
import os
import sys
import math

CACHE = os.path.expanduser("~/.cache/Proton/VPN/serverlist.json")

# Proton feature bitmask.
SECURE_CORE = 1
TOR = 2
P2P = 4
STREAMING = 8


def logical_servers(data):
    """Return the logical server list, tolerating missing or invalid data."""
    if not isinstance(data, dict):
        return []
    servers = data.get("LogicalServers")
    return servers if isinstance(servers, list) else []


def read_cache():
    try:
        with open(CACHE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def features_of(server):
    """Return Features as an integer, even if the cache contains bad data."""
    try:
        return int(server.get("Features") or 0)
    except (TypeError, ValueError):
        return 0


def status_value(value):
    """Normalize supported status values to True, False, or None."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None


def status_known(data):
    """Only enforce logical-server status when the cache has usable data."""
    return any(
        status_value(s.get("Status")) is True
        for s in logical_servers(data)
        if isinstance(s, dict)
    )


def usable(server, check_status=True):
    """Exclude Secure Core and, when reliable, unavailable logical servers."""
    if not isinstance(server, dict):
        return False

    if features_of(server) & SECURE_CORE:
        return False

    if check_status:
        return status_value(server.get("Status")) is True

    return True


def location_of(server, entry=False):
    """Read coordinates from the new cache, with support for the old format."""
    if entry:
        loc = server.get("EntryLocation") or {}
    else:
        loc = server.get("ExitLocation") or server.get("Location") or {}

    if not isinstance(loc, dict):
        return None, None

    # New schema: Latitude / Longitude.
    # Old schema: Lat / Long.
    lat = loc.get("Latitude", loc.get("Lat"))
    lon = loc.get("Longitude", loc.get("Long"))

    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None, None

    if not math.isfinite(lat) or not math.isfinite(lon):
        return None, None

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None

    return lat, lon


def labels(features):
    out = []
    if features & P2P:
        out.append("P2P")
    if features & TOR:
        out.append("Tor")
    if features & STREAMING:
        out.append("Streaming")
    return out


def score_of(server):
    try:
        score = float(server.get("Score"))
        return score if math.isfinite(score) else 9e9
    except (TypeError, ValueError):
        return 9e9


def load_of(server):
    try:
        load = int(server.get("Load"))
        return load if load >= 0 else 999
    except (TypeError, ValueError):
        return 999


def tier_of(server):
    return server.get("Tier")


def all_cities(data):
    """One row per country and city, retaining the best logical server."""
    out = {}
    check_status = status_known(data)

    for server in logical_servers(data):
        if not usable(server, check_status):
            continue

        lat, lon = location_of(server)
        if lat is None:
            continue

        code = str(server.get("ExitCountry") or "").strip().upper()
        city = str(server.get("City") or "").strip()

        if not code or not city:
            continue

        key = (code, city)
        score = score_of(server)
        entry = out.get(key)

        if entry is None:
            out[key] = {
                "code": code,
                "city": city,
                "lat": round(lat, 3),
                "lon": round(lon, 3),
                "name": server.get("Name") or "",
                "load": server.get("Load"),
                "tier": tier_of(server),
                "score": score,
                "count": 1,
            }
            continue

        entry["count"] += 1

        if score < entry["score"]:
            entry.update({
                "lat": round(lat, 3),
                "lon": round(lon, 3),
                "name": server.get("Name") or "",
                "load": server.get("Load"),
                "tier": tier_of(server),
                "score": score,
            })

    rows = sorted(out.values(), key=lambda r: (r["code"], r["city"]))

    for row in rows:
        del row["score"]

    return rows


def country_place(data, code):
    """Find a regular server's location in a country."""
    check_status = status_known(data)

    for server in logical_servers(data):
        if not isinstance(server, dict):
            continue

        if str(server.get("ExitCountry") or "").upper() != code:
            continue

        if not usable(server, check_status):
            continue

        lat, lon = location_of(server)
        if lat is None:
            continue

        return {
            "code": code,
            "city": str(server.get("City") or "").strip(),
            "lat": lat,
            "lon": lon,
        }

    return None


def locate(data, name):
    """Find a logical server and its exit coordinates."""
    want = name.strip().upper()
    if not want:
        return {}

    for server in logical_servers(data):
        if not isinstance(server, dict):
            continue

        server_name = str(server.get("Name") or "")
        if server_name.upper() != want:
            continue

        features = features_of(server)
        lat, lon = location_of(server)

        place = {
            "name": server_name,
            "code": str(server.get("ExitCountry") or "").upper(),
            "city": str(server.get("City") or "").strip(),
            "lat": lat,
            "lon": lon,
            "p2p": bool(features & P2P),
        }

        entry_code = str(server.get("EntryCountry") or "").upper()

        if features & SECURE_CORE and entry_code:
            entry_lat, entry_lon = location_of(server, entry=True)

            # Prefer the coordinates explicitly provided by EntryLocation.
            # Fall back to a regular server in the entry country.
            entry = country_place(data, entry_code)

            if entry_lat is not None:
                entry = entry or {
                    "code": entry_code,
                    "city": "",
                    "lat": entry_lat,
                    "lon": entry_lon,
                }
                entry["lat"] = entry_lat
                entry["lon"] = entry_lon

            if entry and entry_code != place["code"]:
                place["entry"] = entry

        return place

    return {}


def country_cities(data, code, limit=80):
    """One row per city, selecting its best server by score and load."""
    cities = {}
    check_status = status_known(data)

    for server in logical_servers(data):
        if not isinstance(server, dict):
            continue

        if not usable(server, check_status):
            continue

        if str(server.get("ExitCountry") or "").upper() != code:
            continue

        city = str(server.get("City") or "").strip() or "Other"
        score = score_of(server)
        load = load_of(server)

        entry = cities.get(city)

        if entry is None:
            cities[city] = {
                "city": city,
                "name": server.get("Name") or "",
                "load": server.get("Load"),
                "tier": tier_of(server),
                "score": score,
                "tags": labels(features_of(server)),
                "count": 1,
                "_load_sort": load,
            }
            continue

        entry["count"] += 1

        if (score, load) < (entry["score"], entry["_load_sort"]):
            entry.update({
                "name": server.get("Name") or "",
                "load": server.get("Load"),
                "tier": tier_of(server),
                "score": score,
                "tags": labels(features_of(server)),
                "_load_sort": load,
            })

    rows = sorted(cities.values(), key=lambda r: (r["score"], r["_load_sort"]))

    for row in rows:
        del row["_load_sort"]
        del row["score"]

    return rows[:limit]


def print_json(value):
    print(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def main():
    args = sys.argv[1:]

    if not args:
        print_json([])
        return

    data = read_cache()

    if data is None:
        print_json({} if args[0] == "--locate" else [])
        return

    if args[0] == "--cities":
        print_json(all_cities(data))
        return

    if args[0] == "--locate":
        print_json(locate(data, args[1]) if len(args) > 1 else {})
        return

    code = args[0].strip().upper()

    if not code:
        print_json([])
        return

    try:
        limit = int(args[1]) if len(args) > 1 else 80
    except ValueError:
        limit = 80

    # Avoid accidental huge output; zero returns no rows.
    limit = max(0, min(limit, 10000))

    print_json(country_cities(data, code, limit))


if __name__ == "__main__":
    main()
