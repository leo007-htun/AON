"""Map support: place lookup (Nominatim), routes (OSRM via routing.openstreetmap.de), view choice,
and the `map` event the chat UI renders as a targeting map."""
from __future__ import annotations
import asyncio, difflib, math, re, time
from dataclasses import dataclass, field
import httpx

MAX_PLACES = 8
MODES = {"target", "route", "area", "update"}
TRAVEL = {"drive", "walk", "cycle", "flight"}
VIEWS = {"2d", "3d", "globe", "auto"}
_OSRM_PROFILE = {"drive": "car", "walk": "foot", "cycle": "bike"}
_LARGE = {"country", "continent", "state", "region", "province", "county", "ocean", "sea"}
UA = "AON/1.0 (self-hosted dev agent; map lookups)"


_ME = re.compile(r"^\s*(?:(?:my|your|the user'?s|user|current)\s+)*(?:current\s+)?(?:location|position|place)\s*$"
                 r"|^\s*(?:here|me|myself|current|gps)\s*$", re.I)


def normalize_map(d) -> dict | None:
    """Clean the planner's `map` field; None when it is absent or unusable."""
    if not isinstance(d, dict) or d.get("mode") not in MODES:
        return None
    if "to" in d:                       # explicit route fields: from (null = the user's own location) and to
        to, frm = d.get("to"), d.get("from")
        if not (isinstance(to, str) and to.strip()):
            return None
        d = {**d, "places": ([frm.strip()] if isinstance(frm, str) and frm.strip() else []) + [to.strip()]}
    places = [p.strip() for p in (d.get("places") or []) if isinstance(p, str) and p.strip()][:MAX_PLACES]
    if d["mode"] == "route" and len(places) > 1 and _ME.match(places[0]):
        places = places[1:]                 # "my location" / "YOUR Location" as a start = the user's own position
    if d["mode"] != "update" and not places:
        return None
    orbit = d.get("orbit")
    return {"mode": d["mode"], "places": places,
            "travel": d.get("travel") if d.get("travel") in TRAVEL else "drive",
            "view": d.get("view") if d.get("view") in VIEWS else "auto",
            "orbit": orbit if isinstance(orbit, bool) else None}


def haversine(a, b) -> float:
    """Metres between (lon, lat) points."""
    r = math.pi / 180
    dlat, dlon = (b[1] - a[1]) * r, (b[0] - a[0]) * r
    x = math.sin(dlat / 2) ** 2 + math.cos(a[1] * r) * math.cos(b[1] * r) * math.sin(dlon / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(x))


def great_circle(a, b, n: int = 64) -> list:
    """n points along the great circle from a to b, as [lon, lat] (endpoints exact)."""
    r = math.pi / 180
    lon1, lat1, lon2, lat2 = a[0] * r, a[1] * r, b[0] * r, b[1] * r
    d = 2 * math.asin(math.sqrt(math.sin((lat2 - lat1) / 2) ** 2 +
                                math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2))
    pts = []
    for i in range(n):
        f = i / (n - 1)
        if d == 0 or i in (0, n - 1):
            pts.append([float(a[0]), float(a[1])] if i == 0 or d == 0 else [float(b[0]), float(b[1])]); continue
        A, B = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
        x = A * math.cos(lat1) * math.cos(lon1) + B * math.cos(lat2) * math.cos(lon2)
        y = A * math.cos(lat1) * math.sin(lon1) + B * math.cos(lat2) * math.sin(lon2)
        z = A * math.sin(lat1) + B * math.sin(lat2)
        pts.append([math.atan2(y, x) / r, math.atan2(z, math.sqrt(x * x + y * y)) / r])
    return pts


class Geo:
    """Nominatim + OSRM client: identifies itself, keeps to one Nominatim request per second, caches."""
    def __init__(self, transport=None, min_interval: float = 1.1):
        self._transport = transport; self._min = min_interval
        self._cache: dict = {}; self._last = 0.0; self._lock = asyncio.Lock()

    def _client(self):
        return httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}, transport=self._transport)

    @staticmethod
    def _parse(h: dict, q: str) -> dict:
        bbox = [float(x) for x in h["boundingbox"]] if h.get("boundingbox") else None
        return {"query": q, "name": h.get("name") or h["display_name"].split(",")[0], "display": h["display_name"],
                "lat": float(h["lat"]), "lon": float(h["lon"]),
                "kind": h.get("addresstype") if h.get("addresstype") in _LARGE else (h.get("type") or h.get("addresstype") or ""),
                "bbox": bbox, "importance": float(h.get("importance") or 0)}

    async def search(self, q: str, limit: int = 5, near=None) -> list:
        """Up to `limit` candidates, most prominent first (cached; one request per second).
        `near` (lon, lat) prefers results around the user (a preference, not a hard limit)."""
        nb = (round(near[0], 1), round(near[1], 1)) if near else None
        key = (" ".join(q.lower().split()), nb) if nb else " ".join(q.lower().split())
        if key in self._cache:
            return self._cache[key]
        params = {"q": q, "format": "jsonv2", "limit": limit}
        if near:
            params.update(viewbox=f"{near[0] - 0.75},{near[1] + 0.5},{near[0] + 0.75},{near[1] - 0.5}", bounded=0)
        async with self._lock:
            wait = self._min - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self._client() as c:
                    r = await c.get("https://nominatim.openstreetmap.org/search", params=params)
                self._last = time.monotonic()
                hits = r.json() if r.status_code == 200 else []
            except (httpx.HTTPError, ValueError):
                hits = []
        out = [self._parse(h, q) for h in hits if isinstance(h, dict) and "lat" in h]
        self._cache[key] = out
        return out

    async def geocode(self, q: str) -> dict | None:
        hits = await self.search(q)
        return hits[0] if hits else None

    async def suggest(self, q: str, near=None, limit: int = 5) -> list:
        """Typo-tolerant candidates from Photon (komoot), used only when Nominatim finds nothing."""
        key = ("suggest", " ".join(q.lower().split()), near)
        if key in self._cache:
            return self._cache[key]
        params = {"q": q, "limit": limit}
        if near:
            params.update(lon=near[0], lat=near[1])
        try:
            async with self._client() as c:
                r = await c.get("https://photon.komoot.io/api/", params=params)
            data = r.json() if r.status_code == 200 else {}
            feats = data.get("features", []) if isinstance(data, dict) else []
        except (httpx.HTTPError, ValueError):
            feats = []
        out = []
        for f in feats:
            pr, co = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or []
            if not pr.get("name") or len(co) < 2:
                continue
            area = [pr.get(k) for k in ("city", "county", "state", "country") if pr.get(k)]
            out.append({"query": q, "name": pr["name"], "display": ", ".join([pr["name"]] + area),
                        "lat": float(co[1]), "lon": float(co[0]), "kind": pr.get("osm_value") or "",
                        "bbox": None, "importance": 0.0})
        self._cache[key] = out
        return out

    async def route(self, a, b, travel: str) -> dict | None:
        """a, b are (lon, lat). Road routes come from OSRM; flights are a great-circle arc."""
        if travel == "flight":
            return {"distance": haversine(a, b), "duration": None,
                    "geometry": {"type": "LineString", "coordinates": great_circle(a, b, 64)}}
        prof = _OSRM_PROFILE.get(travel, "car")
        url = (f"https://routing.openstreetmap.de/routed-{prof}/route/v1/driving/"
               f"{a[0]},{a[1]};{b[0]},{b[1]}")
        try:
            async with self._client() as c:
                r = await c.get(url, params={"overview": "full", "geometries": "geojson"})
            j = r.json() if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            return None
        if not j.get("routes"):
            return None
        rt = j["routes"][0]
        return {"distance": rt["distance"], "duration": rt["duration"], "geometry": rt["geometry"]}


def auto_view(mode: str, places: list, travel: str = "drive") -> tuple[str, bool]:
    """(view, orbit) when the planner left it to us."""
    big = any((p.get("kind") or "") in _LARGE for p in places)
    span = max((haversine((a["lon"], a["lat"]), (b["lon"], b["lat"])) for a in places for b in places), default=0)
    if mode == "target":
        return ("globe", True) if big and len(places) == 1 else (("3d", True) if len(places) == 1 else ("globe" if span > 2e6 else "2d", False))
    if mode == "route":
        return ("globe" if travel == "flight" or span > 2e6 else "2d", False)
    return ("globe" if big or span > 2e6 else "2d", False)


_VIEW_WORDS = {"2d": "2d", "3d": "3d", "globe": "globe"}
_TRAVEL_WORDS = {"drive": "drive", "driving": "drive", "car": "drive", "walk": "walk", "walking": "walk", "foot": "walk",
                 "cycle": "cycle", "cycling": "cycle", "bike": "cycle", "fly": "flight", "flight": "flight"}


def parse_map_command(text: str) -> dict | None:
    """`/map <places> [2d|3d|globe] [orbit] [walk|drive|cycle|flight]`; 'A to B' is a route, 'A, B' an area."""
    m = re.match(r"^/map\b(.*)$", (text or "").strip(), re.I | re.S)
    if not m or not m.group(1).strip():
        return None
    words, view, orbit, travel = m.group(1).split(), "auto", None, None
    while words and (words[-1].lower() in _VIEW_WORDS or words[-1].lower() == "orbit" or words[-1].lower() in _TRAVEL_WORDS):
        w = words.pop().lower()
        if w == "orbit": orbit = True
        elif w in _VIEW_WORDS: view = _VIEW_WORDS[w]
        else: travel = _TRAVEL_WORDS[w]
    rest = " ".join(words).strip()
    if not rest:
        return None
    if re.search(r"\s+to\s+", rest, re.I):
        a, b = re.split(r"\s+to\s+", rest, maxsplit=1, flags=re.I)
        mode, places = "route", [a.strip(" ,"), b.strip(" ,")]
        travel = travel or "drive"
    else:
        # a comma is part of an address ("Liver Building, Liverpool"); ';' or ' and ' separates places
        places = [p.strip(" ,") for p in re.split(r"\s*;\s*|\s+and\s+", rest, flags=re.I) if p.strip(" ,")]
        mode = "target" if len(places) == 1 else "area"
    return normalize_map({"mode": mode, "places": places, "travel": travel or "drive", "view": view, "orbit": orbit})


def _fmt_km(m: float) -> str:
    return f"{m / 1000:.2f} km" if m >= 1000 else f"{round(m)} m"


def _fmt_t(s) -> str:
    if s is None:
        return ""
    h, mins = int(s // 3600), round((s % 3600) / 60)
    return f"{h} h {mins} min" if h else f"{mins} min"


def _pt(p: dict) -> dict:
    return {"name": p["name"], "display": p["display"], "lat": p["lat"], "lon": p["lon"], "kind": p.get("kind") or ""}


@dataclass
class MapResult:
    event: dict | None = None
    context: str = ""
    ask: str | None = None
    notices: list = field(default_factory=list)
    need_location: bool = False   # a route with no origin: ask the browser for the user's position
    summary: str = ""             # exact route answer (distances/times per travel mode)


@dataclass
class PlaceResolution:
    status: str                      # "ok" | "unsure" | "missing"
    place: dict | None = None
    candidates: list = field(default_factory=list)
    question: str | None = None
    note: str | None = None          # e.g. "taking 'Edgehill station' as Edge Hill" 


_COUNTRYISH = {"united kingdom", "england", "scotland", "wales", "alba / scotland", "northern ireland"}


def _locality(p: dict) -> str:
    """'Crewe, Nantwich Road, Crewe, Cheshire East, England, CW2 6HR, UK' -> 'Crewe, Cheshire East'."""
    parts = [s.strip() for s in p.get("display", "").split(",")][1:]
    keep = [s for s in parts if s and not re.search(r"\d", s) and s.lower() not in _COUNTRYISH
            and not re.search(r"\b(road|street|lane|avenue|way|drive|place|close|row)\b", s, re.I)]
    seen = []
    for s in keep:
        if s not in seen:
            seen.append(s)
    return ", ".join(seen[-2:]) or p.get("display", "")


def _core(s: str) -> str:
    """The distinctive part of a place name: no ', Town' qualifier, no type words like 'station'."""
    s = s.split(",")[0].lower()
    s = re.sub(rf"\b(?:{_PLACE_TYPES}|the|of|railway|train)\b", " ", s)
    return " ".join(s.split())


async def suggest_similar(name: str, geo, near=None, limit: int = 3) -> list:
    """Similarly named real places for a name that found nothing (typos like 'Fromby', 'Albart').
    `near` (lon, lat) biases the search and ranks places near the user first."""
    if not hasattr(geo, "suggest"):
        return []
    core = _core(name)
    if not core:
        return []
    base = name.split(",")[0].strip()
    queries = [base] + ([core] if core != base.lower() else [])
    pool = [c for res in await asyncio.gather(*(geo.suggest(q, near=near) for q in queries)) for c in res]   # in parallel
    scored, seen = [], set()
    n = len(core.split())
    qtok = [w for w in re.split(r"[^\w']+", base.lower()) if w]
    for c in pool:
        cc = _core(c["name"]) or c["name"].lower()
        full = difflib.SequenceMatcher(None, core, cc).ratio()
        # word by word, type words included: "albart dok" ~ "royal ALBERT DOCK" (0.83, 0.86), while
        # "fromby station" vs "fromby creek" fails on station~creek
        ctok = [w for w in re.split(r"[^\w']+", c["name"].lower()) if w] or [""]
        tok = sum(max(difflib.SequenceMatcher(None, q, w).ratio() for w in ctok) for q in qtok) / max(1, len(qtok))
        if full >= 0.75 or tok >= 0.75:
            score = max(full, tok)
        else:
            # a matching word inside a longer name ("albart" ~ "royal ALBERT dock") only counts close
            # to the user; otherwise "Fromby Creek" on another continent would win on its first word
            words = cc.split()
            windows = [" ".join(words[i:i + n]) for i in range(max(1, len(words) - n + 1))]
            win = max(difflib.SequenceMatcher(None, core, w).ratio() for w in windows)
            nearby = near is not None and haversine(near, (c["lon"], c["lat"])) <= 300_000
            if not (win >= 0.75 and nearby):
                continue
            score = win - 0.05
        key = (c["name"].lower(), _locality(c))
        if key not in seen:
            seen.add(key); scored.append((score, c))
    if near:
        far = lambda c: haversine(near, (c["lon"], c["lat"])) > 300_000
        if any(not far(c) for _, c in scored):
            scored = [(s, c) for s, c in scored if not far(c)]      # nearby namesakes beat distant ones
        scored.sort(key=lambda sc: (-round(sc[0], 1), haversine(near, (sc[1]["lon"], sc[1]["lat"]))))
    else:
        scored.sort(key=lambda sc: -sc[0])
    typed = re.search(rf"\b({_PLACE_TYPES})\b", name, re.I)
    out = []
    for score, c in scored[:limit]:
        # "Fromby station" -> town of Formby -> prefer "Formby station" if it exists close by
        if typed and typed.group(1).lower() not in c["name"].lower():
            better = await geo.search(f"{c['name']} {typed.group(1)}")
            if better and haversine((better[0]["lon"], better[0]["lat"]), (c["lon"], c["lat"])) < 10_000:
                c = better[0]
        out.append({**c, "_score": round(score, 3)})
    if typed:
        # the user named a type ("station"): places of that type first ("Edgehill Park" is not a station)
        out.sort(key=lambda c: not _type_match(c, typed.group(1)))
    return out


def _type_match(c: dict, word: str) -> bool:
    w, kind = word.lower(), (c.get("kind") or "").lower()
    if w in c["name"].lower() or w in kind:
        return True
    return w == "station" and kind in ("train_station", "halt", "railway", "stop_position", "platform", "subway_entrance")


def _dedupe(cands: list) -> list:
    """One OSM place often appears several times (station, bus stop, taxi rank; city and boundary)."""
    out = []
    for c in cands:
        same = next((k for k in out if haversine((k["lon"], k["lat"]), (c["lon"], c["lat"])) < 10_000
                     and (_core(k["name"]) == _core(c["name"]) or k["name"].lower() == c["name"].lower())), None)
        if same is None:
            out.append(dict(c))
        elif c.get("importance", 0) > same.get("importance", 0):
            same["importance"] = c["importance"]
    return out


def _relevant(query: str, cand: dict) -> bool:
    """Does the candidate's name resemble the distinctive words of the query?"""
    qt = _core(query).split()
    if not qt:
        return True
    ct = (_core(cand["name"]) or cand["name"].lower()).split() or [""]
    score = sum(max(difflib.SequenceMatcher(None, q, w).ratio() for w in ct) for q in qt) / len(qt)
    return score >= 0.75


async def _search(geo, q, near):
    """geo.search with the user's area as a bias when the client supports it."""
    if near is not None:
        try:
            return await geo.search(q, near=near)
        except TypeError:
            pass
    return await geo.search(q)


_EXACT = re.compile(r"^(.*?)\s*@\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


async def resolve_place(name: str, geo, near=None) -> PlaceResolution:
    """Check a place the model named against real lookups. Confident when there is one place (after
    merging duplicates), a clearly dominant one, or one clearly nearest the user, and any ', Town'
    qualifier matches; otherwise unsure, with a question. 'Name @lat,lon' is an exact earlier choice."""
    m = _EXACT.match(name or "")
    if m:
        nm, lat, lon = m.group(1).strip() or "Chosen place", float(m.group(2)), float(m.group(3))
        return PlaceResolution("ok", {"query": name, "name": nm, "display": nm, "lat": lat, "lon": lon,
                                      "kind": "", "bbox": None, "importance": 1.0})
    if not hasattr(geo, "search"):                       # simple clients: single best match only
        p = await geo.geocode(name)
        return PlaceResolution("ok", p) if p else PlaceResolution("missing")
    base, qual = (name.rsplit(",", 1) + [""])[:2] if "," in name else (name, "")
    base, qual = base.strip(), qual.strip()
    # results whose names don't resemble the request are not candidates ("Edgehill station" matched a
    # Virginia fire station on the word "station"); with none left, fall through to "did you mean"
    cands = [c for c in _dedupe(await _search(geo, name, near)) if _relevant(base, c)]
    mismatch = False
    if not cands and qual:
        cands = [c for c in _dedupe(await _search(geo, base, near)) if _relevant(base, c)]
        matching = [c for c in cands if qual.lower() in c["display"].lower()]
        if matching:
            cands = matching
        else:
            mismatch = bool(cands)
    if not cands:
        # nothing by that name: offer similarly named places, or ask the user to check it
        sugg = await suggest_similar(name, geo, near=near)
        typed = re.search(rf"\b({_PLACE_TYPES})\b", name, re.I)
        of_type = [c for c in sugg if typed and _type_match(c, typed.group(1))]
        if len(of_type) == 1 and of_type[0].get("_score", 0) >= 0.85:
            # the only similarly named place of the type the user asked for ("Edgehill station" -> Edge Hill)
            c = of_type[0]
            return PlaceResolution("ok", c, note=f"taking '{name}' as {c['name']}")
        if sugg and sugg[0].get("_score", 0) >= 0.9 and (len(sugg) == 1 or sugg[1].get("_score", 0) < 0.8):
            # one near-certain spelling match ("Edgehill" ~ "Edge Hill" 0.94): use it, and say so
            c = sugg[0]
            return PlaceResolution("ok", c, note=f"taking '{name}' as {c['name']}")
        if sugg:
            opts = "; ".join(f"{i}) {c['name']}, {_locality(c)}" for i, c in enumerate(sugg, 1))
            return PlaceResolution("unsure", candidates=sugg, question=f"I couldn't find {name}. Did you mean: {opts}?")
        return PlaceResolution("missing", question=f"I couldn't find {name}. Could you check the spelling, "
                                                    f"or add the town it's in?")
    if not mismatch and qual and qual.lower() not in cands[0]["display"].lower():
        mismatch = True
    if mismatch:
        c = cands[0]
        dist = ""
        ref = await geo.search(qual)
        if ref:
            km = haversine((c["lon"], c["lat"]), (ref[0]["lon"], ref[0]["lat"])) / 1000
            dist = f" (about {km:.0f} km from {qual})"
        q = (f"I couldn't find {base} in {qual}. The closest match is {c['name']} in {_locality(c)}{dist}. "
             f"Do you mean that one, or a different place in {qual}?")
        return PlaceResolution("unsure", candidates=cands[:5], question=q)
    if len(cands) == 1:
        return PlaceResolution("ok", cands[0])
    if near:
        close = [c for c in cands if haversine(near, (c["lon"], c["lat"])) < 150_000]
        if len(close) == 1:
            return PlaceResolution("ok", close[0])            # the only one anywhere near the user
        if close:
            cands = sorted(close, key=lambda c: -c.get("importance", 0))
            if len(cands) == 1:
                return PlaceResolution("ok", cands[0])
    top = cands[0]
    if top.get("importance", 0) >= 1.5 * cands[1].get("importance", 0) and top.get("importance", 0) > 0:
        return PlaceResolution("ok", top)
    similar = [c for c in cands if c.get("importance", 0) * 1.5 >= top.get("importance", 0)][:5]
    opts = "; ".join(f"{i}) {c['name']}, {_locality(c)}" for i, c in enumerate(similar, 1))
    return PlaceResolution("unsure", candidates=similar, question=f"Which {base} do you mean? {opts}?")


_MODE_LABEL = {"walk": "Walk", "drive": "Drive", "cycle": "Cycle", "flight": "Flight"}


def _route_line(travel: str, rt) -> str:
    if not rt:
        return f"{_MODE_LABEL[travel]}: no route found"
    if travel == "flight":
        return f"Flight (great-circle): {rt['distance'] / 1000:,.0f} km"
    return f"{_MODE_LABEL[travel]}: {_fmt_km(rt['distance'])} · {_fmt_t(rt['duration'])}"


async def build_map(spec: dict, geo, origin: dict | None = None) -> MapResult:
    """Turn a normalised map spec into the UI event, a grounding note for the model, and warnings.
    `origin` is the user's own position (from the browser) for routes that name no start."""
    if spec["mode"] == "update":
        return MapResult(event={"type": "map", "action": "update", "view": spec["view"], "orbit": spec["orbit"]},
                         context="The map already shown was updated.")
    found, missing, unsure, missing_q, notes = [], [], [], None, []
    near = (float(origin["lon"]), float(origin["lat"])) if origin else None
    for q in spec["places"]:
        r = await resolve_place(q, geo, near=near)
        if r.status == "ok":
            found.append(r.place)
            if r.note:
                notes.append(r.note)
        elif r.status == "unsure":
            unsure.append(r)
        else:
            missing.append(q)
            missing_q = missing_q or r.question
    out = MapResult(notices=[f"Map: could not find '{q}'." for q in missing])
    if missing_q and not unsure:
        # a place that doesn't exist (and nothing similar): ask rather than route to something else
        out.ask = missing_q
        return out
    if unsure:
        # don't guess: show the candidates and ask (the reply resolves it on the next turn)
        u = unsure[0]
        out.ask = u.question
        frm = spec["places"][0] if spec["mode"] == "route" and len(spec["places"]) > 1 else None
        out.event = {"type": "map", "action": "new", "mode": "area", "view": auto_view("area", u.candidates)[0],
                     "orbit": False, "travel": spec["travel"], "targets": [_pt(c) for c in u.candidates],
                     # what was being asked, so "yes" / "the second one" can finish it exactly
                     "pending": {"mode": spec["mode"], "travel": spec["travel"], "from": frm}}
        out.context = "Asked the user which place they mean: " + "; ".join(c["display"] for c in u.candidates)
        return out
    if not found:
        return out
    mode = spec["mode"]
    start = dest = None
    if mode == "route":
        if len(found) >= 2:
            start, dest = found[0], found[1]
        elif origin:
            start = {"name": "Your location", "display": "your current location", "lat": float(origin["lat"]),
                     "lon": float(origin["lon"]), "kind": "you"}
            dest = found[0]
        else:
            mode = "target"
            out.need_location = True
            out.ask = (f"Where are you starting from? Type a place, or click the map and use Set start, "
                       f"to route to {found[0]['name']}.")
    view, orbit = auto_view(mode, found, spec["travel"])
    if mode == "route" and spec["travel"] != "flight":
        # road routes: start on the globe, then zoom into the 3D city. A stray "2d" from the model is
        # ignored here; only an explicit 3D/globe counts (2D is a button or a follow-up away)
        view = spec["view"] if spec["view"] in ("3d", "globe") else "3d"
    elif spec["view"] != "auto":
        view = spec["view"]
    if spec["orbit"] is not None:
        orbit = spec["orbit"]
    ev = {"type": "map", "action": "new", "mode": mode, "view": view, "orbit": orbit, "travel": spec["travel"]}
    lines = []
    if mode == "route":
        travel = spec["travel"]
        modes = ["flight"] if travel == "flight" else [travel] + [t for t in ("walk", "drive", "cycle") if t != travel]
        a, b = (start["lon"], start["lat"]), (dest["lon"], dest["lat"])
        results = await asyncio.gather(*(geo.route(a, b, t) for t in modes))
        routes = dict(zip(modes, results))
        ev.update(start=_pt(start), targets=[_pt(dest)], route=routes[travel], routes=routes, intro="globe")
        lines.append(f"Route from {start['display']} ({start['lat']:.4f}, {start['lon']:.4f}) "
                     f"to {dest['display']} ({dest['lat']:.4f}, {dest['lon']:.4f})")
        order = ["flight"] if travel == "flight" else ["walk", "drive", "cycle"]
        per_mode = [_route_line(t, routes.get(t)) for t in order]
        lines += per_mode
        assumed = f" ({'; '.join(notes)})" if notes else ""
        out.summary = (f"Route from {start['name']} to {dest['name']}{assumed}:\n"
                       + "\n".join(f"- {l}" for l in per_mode))
        if not routes[travel]:
            out.notices.append(f"Map: no route found ({travel}) between {start['name']} and {dest['name']}.")
    else:
        ev["targets"] = [_pt(p) for p in found]
        for p in found:
            lines.append(f"{p['display']}: {p['lat']:.4f}, {p['lon']:.4f}")
        # answered from the lookup (address + coordinates), not from the model's memory
        out.summary = "\n".join(f"- **{p['name']}**: {p['display']} ({p['lat']:.4f}, {p['lon']:.4f})" for p in found)
        if notes:
            out.summary += "\n(" + "; ".join(notes) + ")"
    out.event = ev
    out.context = "A map is shown to the user. " + "; ".join(lines) + "."
    return out


_DEFAULT: Geo | None = None


def default_geo() -> Geo:
    """One shared client so the cache and the one-request-per-second limit cover every chat."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Geo()
    return _DEFAULT


# ---- cheap detection of map-like requests (the planner sometimes leaves `map` empty) ----
_PLACE_Q = [r"\bwhere(?:'s| is| are)\b", r"\bhow (?:do|can|would|should) (?:i|we|you) get (?:to|from|there)\b",
            r"\bhow to (?:get|go|reach|travel)\b", r"\bway to (?:get|go) to\b",
            r"\bdirections?\b", r"\broute (?:to|from|between)\b", r"\b(?:flight|fly|flying|drive|driving|walk|walking|cycle|cycling|travel) (?:route )?(?:to|from)\b",
            r"\bflight route\b", r"\bhow far\b", r"\bdistance (?:from|between|to)\b", r"\bnear(?:by| me)?\b",
            r"\bon (?:a|the) map\b", r"\bmap of\b", r"\bcompare where\b", r"\blocation of\b"]
_NOT_PLACES = re.compile(r"\b(bug|error|file|folder|function|variable|line|config|code|log|setting|method|class|module|"
                         r"package|token|key|defined|stored|saved|repo|directory|handler|endpoint|api|test|the end|the top)\b", re.I)
_MAP_VIEW = re.compile(r"\b(3d|2d|globe|orbit\w*|spin\w*|rotat\w*|zoom\w*|walk instead|drive instead|cycle instead|route there)\b", re.I)


def map_hint(text: str, map_open: bool = False) -> bool:
    """True when a message looks like it is about places (or, with a map open, about changing it)."""
    t = text or ""
    if map_open and _MAP_VIEW.search(t):
        return True
    # with a map open, a correction naming a place ("no, I meant Lime Street station") is about the map
    if map_open and re.search(r"\b(?:i meant|i mean|instead|actually|rather)\b|^\s*no\b", t, re.I) and \
            (re.search(r"\b(?!I\b)[A-Z][a-z]+", t) or re.search(rf"\b(?:{_PLACE_TYPES})\b", t, re.I)):
        return True
    named = re.search(r"\b[Ss]how me (?:the )?[A-Z]", t)   # "show me the Liver Building": a proper noun follows
    return (named or any(re.search(p, t, re.I) for p in _PLACE_Q)) and not _NOT_PLACES.search(t)


_NAME = r"[A-Z][\w'’.&-]*"
_A_IN_B = re.compile(rf"((?:{_NAME}\s+){{0,5}}{_NAME})\s+in\s+((?:{_NAME}\s*){{1,4}})")


def merge_qualifiers(places: list, text: str) -> list:
    """'the Liver Building in Liverpool': the city qualifies the place, it is not a second place.
    Turn ['Liver Building', 'Liverpool'] (or either half alone) into ['Liver Building, Liverpool']."""
    out = list(places)
    low = lambda s: " ".join(s.lower().split())
    for m in _A_IN_B.finditer(text or ""):
        a, b = m.group(1).strip(), m.group(2).strip(" ?.!,")
        both = f"{a}, {b}"
        if any(low(p) == low(both) or low(p).startswith(low(a) + ",") for p in out):
            continue
        ia = next((i for i, p in enumerate(out) if low(p) == low(a)), None)
        ib = next((i for i, p in enumerate(out) if low(p) == low(b)), None)
        if ia is not None:
            out[ia] = both
            if ib is not None:
                out.pop(ib)
        elif ib is not None and m.start() > 0:   # a sentence-initial capital ("Restaurants in X") is not a name
            out[ib] = both
    seen, uniq = set(), []
    for p in out:
        if low(p) not in seen:
            seen.add(low(p)); uniq.append(p)
    return uniq


_PLACE_TYPES = (r"station|airport|street|st|road|rd|lane|avenue|square|park|dock|docks|pier|bridge|building|tower|"
                r"hospital|university|college|school|church|cathedral|museum|stadium|arena|market|centre|center|"
                r"mall|beach|harbour|harbor|port|castle|palace|hotel|library|gardens|garden|terminal")
_LINK = r"to|from|the|a|an|in|at|near|and|via|by|for|or|then|with"
_TYPE_TAIL = re.compile(rf"((?:\s+(?!(?:{_LINK})\b)[\w'’.&-]+){{0,3}}?\s+(?:{_PLACE_TYPES}))\b", re.I)


def expand_place_types(places: list, text: str) -> list:
    """The model sometimes drops the place-type word ("Formby" for "formby station"), which turns a
    station into a whole town. If the message continues the name with such a word, keep it."""
    out = []
    for p in places:
        best = p
        if "," not in p and not re.search(rf"\b(?:{_PLACE_TYPES})$", p, re.I):
            m = re.search(re.escape(p) + _TYPE_TAIL.pattern, text or "", re.I)
            if m:
                best = p + m.group(1)
        out.append(best)
    return out


# ---- "which place do you mean?": remember the exact choices, read the user's answer ----
import json as _json


def choices_marker(event: dict) -> str:
    """Hidden note stored with AON's question: what was being asked and the numbered candidates."""
    pend = event.get("pending") or {}
    opts = [{"name": t["name"], "lat": round(float(t["lat"]), 5), "lon": round(float(t["lon"]), 5)}
            for t in event.get("targets", [])]
    return "[choices " + _json.dumps({"mode": pend.get("mode", "target"), "travel": pend.get("travel", "drive"),
                                      "from": pend.get("from"), "options": opts}) + "]"


def parse_choices(text: str) -> dict | None:
    m = re.search(r"\[choices (\{.*\})\]", text or "", re.S)
    if not m:
        return None
    try:
        d = _json.loads(m.group(1))
    except ValueError:
        return None
    return d if d.get("options") else None


# real ordinals only: "the second one" must not match the bare word "one"
_ORD = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3, "fifth": 4, "5th": 4}
_YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|y|correct|right|sure|ok(?:ay)?|that one|that's it|that is it)\b", re.I)
_NO = re.compile(r"^\s*(?:no|nope|neither|none|not)\b", re.I)


def choice_from_reply(reply: str, choices: dict) -> int | None:
    """Index of the option the user picked ("yes" = the first one, "2", "the second one", a name),
    or None when the reply is something else ("no, Lime Street", a new question)."""
    opts = choices.get("options") or []
    r = (reply or "").strip().lower()
    if not opts or not r or _NO.match(r):
        return None
    m = re.match(r"^(?:number|no\.|option|#)?\s*(\d)\b", r)
    if m and 1 <= int(m.group(1)) <= len(opts):
        return int(m.group(1)) - 1
    for w, i in _ORD.items():
        if re.search(rf"\b{w}\b", r) and i < len(opts) and len(r.split()) <= 5:
            return i
    named = [i for i, o in enumerate(opts) if o["name"].lower() in r]
    if named:
        return max(named, key=lambda i: len(opts[i]["name"]))      # "crewe hall" beats "crewe"
    if _YES.match(r):
        return 0
    return None


_TRAVEL_TAIL = re.compile(r",?\s*(?:walking|driving|cycling|on foot|by (?:car|bike|bicycle|foot|train|bus))\s*$", re.I)


def recover_route_phrases(places: list, goal: str, mode: str) -> list:
    """The model sometimes shortens a destination ("to crewe station, liverpool" -> "Liverpool").
    For routes, if the user's own 'to ...' / 'from ...' phrase contains the model's name, use the phrase."""
    if mode != "route" or not places:
        return places
    out = list(places)
    # the user named no start ("how do I get to X?"): any start the model added is invented (it has
    # put the destination or "YOUR Location" there before), so route from the user's own position
    if len(out) > 1 and not re.search(r"\b(?:from|starting (?:at|from)|leaving|departing)\b", goal or "", re.I):
        out = out[-1:]
    # the phrase after the LAST "to", up to "from" if the start comes second
    # ("how to get to brunswick station from Edgehill station?" -> "brunswick station")
    to = re.search(r"\bto\s+(?:the\s+)?((?:(?!\bto\b|\bfrom\b).)+?)\s*(?:\bfrom\b(?:(?!\bto\b).)*)?[?!.]*\s*$",
                   goal or "", re.I | re.S)
    # "from X to ..." or "... from X" at the end
    frm = re.search(r"\bfrom\s+(?:the\s+)?((?:(?!\bto\b|\bfrom\b).)+?)\s*(?:\bto\b|[?!.]*\s*$)", goal or "", re.I | re.S)
    def better(name, phrase):
        if not phrase:
            return name
        ph = _TRAVEL_TAIL.sub("", phrase.group(1)).strip(" ,")
        if name.lower() in ph.lower() and len(ph) > len(name) and len(ph.split()) <= 6 and not re.search(r"\bto\b", ph, re.I):
            return ph
        return name
    out[-1] = better(out[-1], to)
    if len(out) > 1:
        out[0] = better(out[0], frm)
    elif frm:
        # the user named a start ("... from Edgehill station") but the model dropped it: put it back
        start = _TRAVEL_TAIL.sub("", frm.group(1)).strip(" ,")
        if start and start.lower() != out[-1].lower() and len(start.split()) <= 6:
            out.insert(0, start)
    return out


_ROUTE_Q = re.compile(r"\b(?:how (?:do|can|would|should|could) (?:i|we|you) get (?:to|there)|how to get to|"
                      r"directions? (?:to|from)|route (?:to|from)|way to|take me to|navigate to|get me to|"
                      r"how far (?:is it )?to|travel to)\b", re.I)


def force_route_mode(m: dict, goal: str) -> dict:
    """'How do I get to X?' is a route even when the model labelled it a target or an area."""
    if m and m.get("mode") in ("target", "area") and m.get("places") and _ROUTE_Q.search(goal or ""):
        return {**m, "mode": "route"}
    return m
