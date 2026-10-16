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
import math
import os
import sys

# Proton's feature bitmask, from proton.vpn.session.servers.enums.
SECURE_CORE = 1
TOR = 2
P2P = 4
STREAMING = 8

CACHE = os.path.expanduser("~/.cache/Proton/VPN/serverlist.json")

# ISO 3166-1 alpha-2 code -> English country name, so the panel can say
# "United States" instead of "US". Built in rather than pulled from a package
# (pycountry) because the plugin only promises a bare Python 3.10+. Proton also
# writes the United Kingdom as "UK" in places, which is not an ISO code, so both
# spellings are listed. A code missing from here simply shows as the code.
COUNTRIES = {
    "AD": "Andorra", "AE": "United Arab Emirates", "AF": "Afghanistan",
    "AG": "Antigua and Barbuda", "AL": "Albania", "AM": "Armenia",
    "AO": "Angola", "AR": "Argentina", "AT": "Austria", "AU": "Australia",
    "AZ": "Azerbaijan", "BA": "Bosnia and Herzegovina", "BB": "Barbados",
    "BD": "Bangladesh", "BE": "Belgium", "BF": "Burkina Faso",
    "BG": "Bulgaria", "BH": "Bahrain", "BI": "Burundi", "BJ": "Benin",
    "BM": "Bermuda", "BN": "Brunei", "BO": "Bolivia", "BR": "Brazil",
    "BS": "Bahamas", "BT": "Bhutan", "BW": "Botswana", "BY": "Belarus",
    "BZ": "Belize", "CA": "Canada", "CD": "DR Congo", "CF": "Central African Republic",
    "CG": "Republic of the Congo", "CH": "Switzerland", "CI": "Ivory Coast",
    "CL": "Chile", "CM": "Cameroon", "CN": "China", "CO": "Colombia",
    "CR": "Costa Rica", "CU": "Cuba", "CV": "Cape Verde", "CY": "Cyprus",
    "CZ": "Czechia", "DE": "Germany", "DJ": "Djibouti", "DK": "Denmark",
    "DO": "Dominican Republic", "DZ": "Algeria", "EC": "Ecuador",
    "EE": "Estonia", "EG": "Egypt", "ER": "Eritrea", "ES": "Spain",
    "ET": "Ethiopia", "FI": "Finland", "FJ": "Fiji", "FR": "France",
    "GA": "Gabon", "GB": "United Kingdom", "GE": "Georgia", "GH": "Ghana",
    "GI": "Gibraltar", "GL": "Greenland", "GM": "Gambia", "GN": "Guinea",
    "GR": "Greece", "GT": "Guatemala", "GY": "Guyana", "HK": "Hong Kong",
    "HN": "Honduras", "HR": "Croatia", "HT": "Haiti", "HU": "Hungary",
    "ID": "Indonesia", "IE": "Ireland", "IL": "Israel", "IM": "Isle of Man",
    "IN": "India", "IQ": "Iraq", "IR": "Iran", "IS": "Iceland", "IT": "Italy",
    "JE": "Jersey", "JM": "Jamaica", "JO": "Jordan", "JP": "Japan",
    "KE": "Kenya", "KG": "Kyrgyzstan", "KH": "Cambodia", "KR": "South Korea",
    "KW": "Kuwait", "KZ": "Kazakhstan", "LA": "Laos", "LB": "Lebanon",
    "LI": "Liechtenstein", "LK": "Sri Lanka", "LR": "Liberia", "LT": "Lithuania",
    "LU": "Luxembourg", "LV": "Latvia", "LY": "Libya", "MA": "Morocco",
    "MC": "Monaco", "MD": "Moldova", "ME": "Montenegro", "MG": "Madagascar",
    "MK": "North Macedonia", "ML": "Mali", "MM": "Myanmar", "MN": "Mongolia",
    "MO": "Macao", "MR": "Mauritania", "MT": "Malta", "MU": "Mauritius",
    "MV": "Maldives", "MW": "Malawi", "MX": "Mexico", "MY": "Malaysia",
    "MZ": "Mozambique", "NA": "Namibia", "NE": "Niger", "NG": "Nigeria",
    "NI": "Nicaragua", "NL": "Netherlands", "NO": "Norway", "NP": "Nepal",
    "NZ": "New Zealand", "OM": "Oman", "PA": "Panama", "PE": "Peru",
    "PG": "Papua New Guinea", "PH": "Philippines", "PK": "Pakistan",
    "PL": "Poland", "PR": "Puerto Rico", "PS": "Palestine", "PT": "Portugal",
    "PY": "Paraguay", "QA": "Qatar", "RO": "Romania", "RS": "Serbia",
    "RU": "Russia", "RW": "Rwanda", "SA": "Saudi Arabia", "SC": "Seychelles",
    "SD": "Sudan", "SE": "Sweden", "SG": "Singapore", "SI": "Slovenia",
    "SK": "Slovakia", "SL": "Sierra Leone", "SM": "San Marino", "SN": "Senegal",
    "SO": "Somalia", "SR": "Suriname", "SS": "South Sudan", "SV": "El Salvador",
    "SY": "Syria", "SZ": "Eswatini", "TD": "Chad", "TG": "Togo", "TH": "Thailand",
    "TJ": "Tajikistan", "TL": "Timor-Leste", "TM": "Turkmenistan", "TN": "Tunisia",
    "TR": "Turkey", "TT": "Trinidad and Tobago", "TW": "Taiwan", "TZ": "Tanzania",
    "UA": "Ukraine", "UG": "Uganda", "UK": "United Kingdom", "US": "United States",
    "UY": "Uruguay", "UZ": "Uzbekistan", "VA": "Vatican City", "VE": "Venezuela",
    "VN": "Vietnam", "XK": "Kosovo", "YE": "Yemen", "ZA": "South Africa",
    "ZM": "Zambia", "ZW": "Zimbabwe",
}


def country_name(code):
    """The readable name for a country code, or the code itself if unknown."""
    code = (code or "").strip().upper()
    return COUNTRIES.get(code, code)


def labels(features):
    out = []
    if features & P2P:
        out.append("P2P")
    if features & TOR:
        out.append("Tor")
    if features & STREAMING:
        out.append("Streaming")
    return out


def read_cache():
    try:
        with open(CACHE, "r") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def coords(s, entry=False):
    """A server's (latitude, longitude), or (None, None) when it has none.

    Where the coordinates live depends on the cache's schema, so every known
    shape is tried in turn:

      * current:  "ExitLocation" / "EntryLocation", each {"Latitude": ...,
                  "Longitude": ...}. A server has no plain "Location" at all
                  here, which is why looking only for that returned null for
                  every city and server. Exit and entry differ only for
                  Secure Core (CH-AR#2 exits in Buenos Aires, enters in
                  Switzerland); for a regular server they are the same point.
      * older:    a single "Location" holding {"Lat": ..., "Long": ...}
                  (what proton.vpn.session.servers.types reads), or the same
                  object spelled Latitude/Longitude.

    entry=True asks for the entry hop instead of the exit. Unusable values
    (missing, non-numeric, NaN, outside the globe) read as "no coordinates"
    rather than putting a pin in the ocean at 0,0.
    """
    if entry:
        candidates = [s.get("EntryLocation")]
    else:
        candidates = [s.get("ExitLocation"), s.get("Location")]
    for loc in candidates:
        if not isinstance(loc, dict):
            continue
        lat = loc.get("Latitude")
        if lat is None:
            lat = loc.get("Lat")
        lon = loc.get("Longitude")
        if lon is None:
            lon = loc.get("Long")
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    return None, None


def status_known(data):
    """Whether Status carries any information in this cache.

    Proton fills Status in from a *separate* "loads" refresh
    (`Status = 1 if server_load.enabled else 0` in its own types.py). Until
    that call has succeeded, or when it fails, every server in the file reads
    Status 0. That means "we don't know yet", not "all 18,000 servers are
    down", and taking it literally empties the map and every city list.

    So Status is honoured only when at least one server is marked up.
    """
    for s in data.get("LogicalServers") or []:
        if s.get("Status") == 1:
            return True
    return False


def usable(s, check_status=True):
    """Connectable, and not Secure Core (those are reached via --securecore)."""
    if check_status and s.get("Status") != 1:
        return False
    return not ((s.get("Features") or 0) & SECURE_CORE)


def all_cities(data):
    """Every city worldwide: one entry per (country, city) with its coordinates
    and best server. Feeds the panel's mini-map; ~200 rows for ~18k servers."""
    out = {}
    check_status = status_known(data)
    for s in data.get("LogicalServers") or []:
        if not usable(s, check_status):
            continue
        lat, lon = coords(s)
        if lat is None or lon is None:
            continue
        code = (s.get("ExitCountry") or "").upper()
        city = (s.get("City") or "").strip()
        if code == "" or city == "":
            continue
        score = s.get("Score")
        score = score if score is not None else 9e9
        key = (code, city)
        entry = out.get(key)
        if entry is None or score < entry["score"]:
            out[key] = {
                "code": code,
                "country": country_name(code),
                "city": city,
                "lat": round(float(lat), 3),
                "lon": round(float(lon), 3),
                "name": s.get("Name") or "",
                "load": s.get("Load"),
                "tier": s.get("Tier"),
                "score": score,
                "count": (entry["count"] + 1) if entry else 1,
            }
        else:
            entry["count"] += 1
    rows = sorted(out.values(), key=lambda r: (r["code"], r["city"]))
    for r in rows:
        del r["score"]
    return rows


def country_place(data, code):
    """Where a country is, for the map: the location of its first regular
    server. Secure Core entry countries (CH, IS, SE) each have one city."""
    for s in data.get("LogicalServers") or []:
        if (s.get("ExitCountry") or "").upper() != code:
            continue
        if (s.get("Features") or 0) & SECURE_CORE:
            continue
        lat, lon = coords(s)
        if lat is None or lon is None:
            continue
        return {
            "code": code,
            "country": country_name(code),
            "city": (s.get("City") or "").strip(),
            "lat": lat,
            "lon": lon,
        }
    return None


def locate(data, name):
    """Where one server is. Used to light up the connected city on the map.
    A Secure Core server (CH-US#3: enters Switzerland, exits New York) also
    carries its entry hop so the map can draw the route."""
    want = name.strip().upper()
    for s in data.get("LogicalServers") or []:
        if (s.get("Name") or "").upper() != want:
            continue
        lat, lon = coords(s)
        features = s.get("Features") or 0
        place = {
            "name": s.get("Name") or "",
            "code": (s.get("ExitCountry") or "").upper(),
            "country": country_name(s.get("ExitCountry")),
            "city": (s.get("City") or "").strip(),
            "lat": lat,
            "lon": lon,
            # Feature bit 4 is P2P. Most servers permit it, so the panel only
            # makes a point of it when that's what you asked for.
            "p2p": bool(features & P2P),
        }
        entry_code = (s.get("EntryCountry") or "").upper()
        if (s.get("Features") or 0) & SECURE_CORE and entry_code and entry_code != place["code"]:
            # The server's own EntryLocation is the exact entry hop; a regular
            # server in the entry country is only the fallback for caches that
            # don't carry it.
            entry = country_place(data, entry_code)
            entry_lat, entry_lon = coords(s, entry=True)
            if entry_lat is not None:
                entry = entry or {"code": entry_code, "country": country_name(entry_code), "city": ""}
                entry["lat"] = entry_lat
                entry["lon"] = entry_lon
            if entry:
                place["entry"] = entry
        return place
    return {}


def main():
    if len(sys.argv) < 2:
        print("[]")
        return
    data = read_cache()
    if sys.argv[1] == "--cities":
        print(json.dumps(all_cities(data) if data else [], separators=(",", ":")))
        return
    if sys.argv[1] == "--locate":
        name = sys.argv[2] if len(sys.argv) > 2 else ""
        print(json.dumps(locate(data, name) if (data and name) else {}, separators=(",", ":")))
        return
    if data is None:
        print("[]")
        return
    code = sys.argv[1].strip().upper()
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 80

    # city -> best server seen so far, plus a count of that city's servers.
    cities = {}
    check_status = status_known(data)
    for s in data.get("LogicalServers") or []:
        # Status 0 means under maintenance, but only once we know Status has
        # been populated at all: see status_known().
        if check_status and s.get("Status") != 1:
            continue
        if (s.get("ExitCountry") or "").upper() != code:
            continue
        features = s.get("Features") or 0
        # Secure Core entries are reached via --securecore, not by name here;
        # listing them under their exit country would be misleading.
        if features & SECURE_CORE:
            continue

        # Some servers carry no city; group them together rather than dropping
        # them, so small countries don't come back empty.
        city = (s.get("City") or "").strip() or "Other"
        score = s.get("Score")
        score = score if score is not None else 9e9
        load = s.get("Load")
        load = load if load is not None else 999

        entry = cities.get(city)
        if entry is None:
            cities[city] = {
                "city": city,
                "name": s.get("Name") or "",
                "load": s.get("Load"),
                "tier": s.get("Tier"),
                "score": score,
                "tags": labels(features),
                "count": 1,
            }
            continue

        entry["count"] += 1
        # Score is Proton's own "fastest" metric (lower is better) and is what
        # the CLI sorts on; load breaks ties.
        if (score, load) < (entry["score"], entry["load"] if entry["load"] is not None else 999):
            entry.update({
                "name": s.get("Name") or "",
                "load": s.get("Load"),
                "tier": s.get("Tier"),
                "score": score,
                "tags": labels(features),
            })

    rows = sorted(cities.values(), key=lambda r: r["score"])
    print(json.dumps(rows[:limit], separators=(",", ":")))


if __name__ == "__main__":
    main()