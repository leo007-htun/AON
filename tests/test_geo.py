import json, pytest, httpx
from agent import geo
from agent.geo import (normalize_map, Geo, auto_view, great_circle, parse_map_command, build_map)

# ---- planner output normalisation ----

def test_normalize_map_rejects_junk_and_fills_defaults():
    assert normalize_map(None) is None
    assert normalize_map({"mode": "dance", "places": ["x"]}) is None
    assert normalize_map({"mode": "target", "places": []}) is None          # target needs a place
    m = normalize_map({"mode": "target", "places": ["  Albert Dock  ", "", 5]})
    assert m == {"mode": "target", "places": ["Albert Dock"], "travel": "drive", "view": "auto", "orbit": None}
    u = normalize_map({"mode": "update", "view": "globe", "orbit": False})
    assert u["mode"] == "update" and u["places"] == [] and u["view"] == "globe" and u["orbit"] is False
    assert len(normalize_map({"mode": "area", "places": [f"p{i}" for i in range(20)]})["places"]) == geo.MAX_PLACES
    assert normalize_map({"mode": "route", "places": ["a", "b"], "travel": "boat"})["travel"] == "drive"

# ---- geocoding ----

NOMINATIM_HIT = [{"lat": "53.3995", "lon": "-2.9925", "display_name": "Royal Albert Dock, Liverpool, England",
                  "name": "Royal Albert Dock", "class": "tourism", "type": "attraction", "addresstype": "tourism",
                  "boundingbox": ["53.398", "53.401", "-2.994", "-2.991"]}]

def _geo(handler):
    seen = []
    def h(req):
        seen.append(req); return handler(req)
    g = Geo(transport=httpx.MockTransport(h), min_interval=0)
    return g, seen

@pytest.mark.asyncio
async def test_geocode_parses_hit_identifies_itself_and_caches():
    g, seen = _geo(lambda r: httpx.Response(200, json=NOMINATIM_HIT))
    p = await g.geocode("Albert Dock")
    assert p["name"] == "Royal Albert Dock" and abs(p["lat"] - 53.3995) < 1e-9 and abs(p["lon"] + 2.9925) < 1e-9
    assert p["kind"] == "attraction" and p["query"] == "Albert Dock"
    assert "nominatim" in str(seen[0].url) and "AON" in seen[0].headers["user-agent"]
    await g.geocode("albert dock ")
    assert len(seen) == 1                                    # cached, case/space-insensitive

@pytest.mark.asyncio
async def test_geocode_none_on_no_match_or_error():
    g, _ = _geo(lambda r: httpx.Response(200, json=[]))
    assert await g.geocode("Python") is None
    g, _ = _geo(lambda r: httpx.Response(503))
    assert await g.geocode("x") is None

# ---- routing ----

OSRM = {"code": "Ok", "routes": [{"distance": 1830.4, "duration": 1320.0,
        "geometry": {"type": "LineString", "coordinates": [[-2.97, 53.40], [-2.98, 53.401], [-2.9925, 53.3995]]}}]}

@pytest.mark.asyncio
async def test_route_uses_profile_server_and_parses():
    g, seen = _geo(lambda r: httpx.Response(200, json=OSRM))
    r = await g.route((-2.97, 53.40), (-2.9925, 53.3995), "walk")
    assert "routed-foot" in str(seen[0].url)
    assert r["distance"] == 1830.4 and r["duration"] == 1320.0 and r["geometry"]["type"] == "LineString"

@pytest.mark.asyncio
async def test_route_flight_is_great_circle_without_http():
    g, seen = _geo(lambda r: httpx.Response(500))
    r = await g.route((-2.27, 53.35), (139.78, 35.55), "flight")
    assert not seen and r["duration"] is None and r["distance"] > 9_000_000
    c = r["geometry"]["coordinates"]
    assert c[0] == [-2.27, 53.35] and c[-1] == [139.78, 35.55] and len(c) >= 32

@pytest.mark.asyncio
async def test_route_none_when_no_route():
    g, _ = _geo(lambda r: httpx.Response(200, json={"code": "NoRoute", "routes": []}))
    assert await g.route((0, 0), (1, 1), "drive") is None

def test_great_circle_endpoints():
    pts = great_circle((0.0, 0.0), (90.0, 0.0), 10)
    assert pts[0] == [0.0, 0.0] and pts[-1] == [90.0, 0.0] and len(pts) == 10

# ---- view choice ----

def _p(kind, lat, lon):
    return {"name": kind, "kind": kind, "lat": lat, "lon": lon}

def test_auto_view_rules():
    assert auto_view("target", [_p("attraction", 53.4, -3.0)]) == ("3d", True)          # one place: 3D + orbit
    assert auto_view("target", [_p("country", 21.9, 95.9)]) == ("globe", True)
    assert auto_view("area", [_p("city", 53.4, -3.0), _p("city", 37.5, 127.0)]) == ("globe", False)
    assert auto_view("area", [_p("city", 53.4, -3.0), _p("city", 53.48, -2.24)]) == ("2d", False)
    assert auto_view("route", [_p("city", 53.4, -3.0), _p("city", 53.48, -2.24)], travel="drive") == ("2d", False)
    assert auto_view("route", [_p("city", 53.4, -3.0), _p("city", 35.5, 139.7)], travel="flight") == ("globe", False)

# ---- /map command ----

def test_parse_map_command():
    assert parse_map_command("/map Tokyo 3d orbit") == {"mode": "target", "places": ["Tokyo"], "travel": "drive", "view": "3d", "orbit": True}
    r = parse_map_command("/map Lime Street to Albert Dock walk")
    assert r["mode"] == "route" and r["places"] == ["Lime Street", "Albert Dock"] and r["travel"] == "walk"
    # a comma belongs to the address; several places are separated with ';' or ' and '
    t = parse_map_command("/map Liver Building, Liverpool")
    assert t["mode"] == "target" and t["places"] == ["Liver Building, Liverpool"]
    a = parse_map_command("/map Yangon; Seoul globe")
    assert a["mode"] == "area" and a["places"] == ["Yangon", "Seoul"] and a["view"] == "globe"
    b = parse_map_command("/map Yangon and Seoul and Tokyo")
    assert b["mode"] == "area" and b["places"] == ["Yangon", "Seoul", "Tokyo"]
    assert parse_map_command("/map") is None and parse_map_command("hello") is None

# ---- building the UI event ----

class FakeGeo:
    def __init__(self, places, route=None): self.places = places; self._route = route; self.routed = []
    async def geocode(self, q): return self.places.get(q)
    async def route(self, a, b, travel): self.routed.append((a, b, travel)); return self._route

DOCK = {"query": "Albert Dock", "name": "Royal Albert Dock", "display": "Royal Albert Dock, Liverpool", "lat": 53.3995, "lon": -2.9925, "kind": "attraction", "bbox": None}
LIME = {"query": "Lime Street", "name": "Lime Street", "display": "Lime Street, Liverpool", "lat": 53.4075, "lon": -2.9778, "kind": "station", "bbox": None}

@pytest.mark.asyncio
async def test_build_target_event_3d_orbit_and_context():
    out = await build_map({"mode": "target", "places": ["Albert Dock"], "travel": "drive", "view": "auto", "orbit": None},
                          FakeGeo({"Albert Dock": DOCK}))
    ev = out.event
    assert ev["type"] == "map" and ev["mode"] == "target" and ev["view"] == "3d" and ev["orbit"] is True
    assert ev["targets"][0]["name"] == "Royal Albert Dock" and ev["targets"][0]["lat"] == 53.3995
    assert "53.3995" in out.context and out.ask is None and not out.notices

@pytest.mark.asyncio
async def test_build_route_without_origin_asks_and_shows_target():
    out = await build_map({"mode": "route", "places": ["Albert Dock"], "travel": "walk", "view": "auto", "orbit": None},
                          FakeGeo({"Albert Dock": DOCK}))
    assert out.ask and "start" in out.ask.lower()
    assert out.event["mode"] == "target" and out.event["targets"][0]["name"] == "Royal Albert Dock"

@pytest.mark.asyncio
async def test_build_route_with_origin_has_geometry_and_summary():
    g = FakeGeo({"Lime Street": LIME, "Albert Dock": DOCK}, route=OSRM["routes"][0] | {"geometry": OSRM["routes"][0]["geometry"]})
    out = await build_map({"mode": "route", "places": ["Lime Street", "Albert Dock"], "travel": "walk", "view": "auto", "orbit": None}, g)
    ev = out.event
    assert ev["mode"] == "route" and ev["start"]["name"] == "Lime Street" and ev["targets"][0]["name"] == "Royal Albert Dock"
    assert ev["route"]["distance"] == 1830.4 and ev["route"]["geometry"]["type"] == "LineString" and ev["travel"] == "walk"
    assert g.routed and g.routed[0][2] == "walk"
    assert "1.83 km" in out.context and "22 min" in out.context

@pytest.mark.asyncio
async def test_build_route_failure_is_a_warning_but_still_maps():
    out = await build_map({"mode": "route", "places": ["Lime Street", "Albert Dock"], "travel": "drive", "view": "auto", "orbit": None},
                          FakeGeo({"Lime Street": LIME, "Albert Dock": DOCK}, route=None))
    assert out.event["mode"] == "route" and out.event["route"] is None
    assert any("no route" in n.lower() for n in out.notices)

@pytest.mark.asyncio
async def test_build_update_needs_no_lookup():
    out = await build_map({"mode": "update", "places": [], "travel": "drive", "view": "globe", "orbit": False}, FakeGeo({}))
    assert out.event == {"type": "map", "action": "update", "view": "globe", "orbit": False}

@pytest.mark.asyncio
async def test_build_nothing_found_gives_warning_no_map():
    out = await build_map({"mode": "target", "places": ["Python"], "travel": "drive", "view": "auto", "orbit": None}, FakeGeo({}))
    assert out.event is None and out.notices and "Python" in out.notices[0]

# ---- route from the user's own location, all three travel modes ----

class ModeGeo(FakeGeo):
    """returns a different route per travel mode so the summary can be checked"""
    TABLE = {"walk": (5200.0, 3840.0), "drive": (6100.0, 720.0), "cycle": (5400.0, 1260.0)}
    async def route(self, a, b, travel):
        self.routed.append((a, b, travel))
        if travel == "flight":
            return {"distance": 9_280_000.0, "duration": None, "geometry": {"type": "LineString", "coordinates": [list(a), list(b)]}}
        d, t = self.TABLE[travel]
        return {"distance": d, "duration": t, "geometry": {"type": "LineString", "coordinates": [list(a), list(b)]}}

ME = {"lat": 53.4300, "lon": -2.9500, "accuracy": 35}
FORMBY = {"query": "Formby station", "name": "Formby Station", "display": "Formby Station, Formby", "lat": 53.5544, "lon": -3.0706, "kind": "station", "bbox": None}

@pytest.mark.asyncio
async def test_route_without_origin_asks_browser_for_location():
    out = await build_map({"mode": "route", "places": ["Formby station"], "travel": "drive", "view": "auto", "orbit": None},
                          ModeGeo({"Formby station": FORMBY}))
    assert out.need_location is True and out.ask            # ask stays as the fallback if location is refused
    assert out.event["mode"] == "target"

@pytest.mark.asyncio
async def test_route_from_user_location_has_all_three_modes():
    g = ModeGeo({"Formby station": FORMBY})
    out = await build_map({"mode": "route", "places": ["Formby station"], "travel": "drive", "view": "auto", "orbit": None}, g, origin=ME)
    ev = out.event
    assert not out.need_location and out.ask is None
    assert ev["mode"] == "route" and ev["start"]["name"] == "Your location" and ev["start"]["lat"] == 53.43
    assert ev["targets"][0]["name"] == "Formby Station"
    assert set(ev["routes"]) == {"walk", "drive", "cycle"} and ev["route"] == ev["routes"]["drive"]
    assert g.routed[0][2] == "drive" and len(g.routed) == 3       # the asked-for mode first
    assert ev["intro"] == "globe" and ev["view"] == "3d"           # globe first, then zoom into 3D
    s = out.summary
    assert "Formby Station" in s
    assert "Walk" in s and "5.20 km" in s and "1 h 4 min" in s
    assert "Drive" in s and "6.10 km" in s and "12 min" in s
    assert "Cycle" in s and "5.40 km" in s and "21 min" in s
    assert s.index("Walk") < s.index("Drive") < s.index("Cycle")
    assert "6.10 km" in out.context and "5.20 km" in out.context

@pytest.mark.asyncio
async def test_route_summary_marks_a_missing_mode():
    class NoBike(ModeGeo):
        async def route(self, a, b, travel):
            return None if travel == "cycle" else await super().route(a, b, travel)
    out = await build_map({"mode": "route", "places": ["Formby station"], "travel": "walk", "view": "auto", "orbit": None},
                          NoBike({"Formby station": FORMBY}), origin=ME)
    assert "Cycle: no route found" in out.summary and "Walk" in out.summary

@pytest.mark.asyncio
async def test_flight_route_is_only_a_flight_on_the_globe():
    g = ModeGeo({"Tokyo": {**FORMBY, "name": "Tokyo", "display": "Tokyo, Japan", "lat": 35.68, "lon": 139.76}})
    out = await build_map({"mode": "route", "places": ["Tokyo"], "travel": "flight", "view": "auto", "orbit": None}, g, origin=ME)
    assert set(out.event["routes"]) == {"flight"} and out.event["view"] == "globe"
    assert "Flight" in out.summary and "9280" in out.summary.replace(",", "")

@pytest.mark.asyncio
async def test_new_road_route_ends_in_3d_even_if_planner_said_2d():
    out = await build_map({"mode": "route", "places": ["Formby station"], "travel": "drive", "view": "2d", "orbit": None},
                          ModeGeo({"Formby station": FORMBY}), origin=ME)
    assert out.event["view"] == "3d" and out.event["intro"] == "globe"
    g = await build_map({"mode": "route", "places": ["Formby station"], "travel": "drive", "view": "globe", "orbit": None},
                        ModeGeo({"Formby station": FORMBY}), origin=ME)
    assert g.event["view"] == "globe"

# ---- verifying the model's places against real lookups; ask when unsure ----
from agent.geo import resolve_place

def _hit(name, display, lat, lon, imp, typ="station"):
    return {"lat": str(lat), "lon": str(lon), "display_name": display, "name": name, "type": typ,
            "addresstype": typ, "importance": imp, "boundingbox": [str(lat), str(lat), str(lon), str(lon)]}

CREWE = _hit("Crewe", "Crewe, Nantwich Road, Crewe, Cheshire East, England, CW2 6HR, United Kingdom", 53.0890, -2.4330, 0.457)
LPOOL = _hit("Liverpool", "Liverpool, Liverpool City Region, England, United Kingdom", 53.4072, -2.9917, 0.75, "city")
DOCKS = [_hit("Royal Albert Dock", "Royal Albert Dock, Waterfront, Liverpool, England", 53.3995, -2.9925, 0.385, "dock"),
         _hit("Albert Dock", "Albert Dock, Leith, City of Edinburgh, Scotland", 55.97, -3.17, 0.133, "dock"),
         _hit("Albert Dock", "Albert Dock, Old Town, Hull, England", 53.74, -0.33, 0.133, "dock")]
PARKS = [_hit("Victoria Park", "Victoria Park, Hackney, London, England", 51.536, -0.039, 0.40, "park"),
         _hit("Victoria Park", "Victoria Park, Manchester, England", 53.45, -2.21, 0.38, "park")]

def _search_geo(table):
    def handler(req):
        q = dict(httpx.QueryParams(req.url.query)).get("q", "").lower()
        return httpx.Response(200, json=table.get(q, []))
    return Geo(transport=httpx.MockTransport(handler), min_interval=0)

@pytest.mark.asyncio
async def test_resolve_clear_single_or_dominant_match_is_confident():
    g = _search_geo({"albert dock": DOCKS, "lime street station, liverpool": [_hit("Liverpool Lime Street", "Liverpool Lime Street, Liverpool", 53.4075, -2.9778, 0.477)]})
    r = await resolve_place("Albert Dock", g)
    assert r.status == "ok" and r.place["name"] == "Royal Albert Dock"
    r2 = await resolve_place("Lime Street station, Liverpool", g)
    assert r2.status == "ok" and r2.place["name"] == "Liverpool Lime Street"

@pytest.mark.asyncio
async def test_resolve_qualifier_contradiction_is_unsure():
    g = _search_geo({"crewe station, liverpool": [], "crewe station": [CREWE], "liverpool": [LPOOL]})
    r = await resolve_place("crewe station, liverpool", g)
    assert r.status == "unsure" and r.candidates[0]["name"] == "Crewe"
    q = r.question.lower()
    assert "crewe" in q and "liverpool" in q and "cheshire east" in q and "km" in q and q.rstrip().endswith("?")

@pytest.mark.asyncio
async def test_resolve_several_similar_matches_is_unsure():
    g = _search_geo({"victoria park": PARKS})
    r = await resolve_place("Victoria Park", g)
    assert r.status == "unsure" and len(r.candidates) == 2
    assert "London" in r.question and "Manchester" in r.question

@pytest.mark.asyncio
async def test_resolve_nothing_found():
    r = await resolve_place("Qwxzz", _search_geo({}))
    assert r.status == "missing"

@pytest.mark.asyncio
async def test_build_map_asks_instead_of_routing_somewhere_else():
    class G:
        async def geocode(self, q): return None
        async def search(self, q, limit=5):
            return {"crewe station, liverpool": [], "crewe station": [Geo._parse(CREWE, q)], "liverpool": [Geo._parse(LPOOL, q)]}.get(q.lower(), [])
        async def route(self, *a): raise AssertionError("must not route while unsure")
    out = await build_map({"mode": "route", "places": ["crewe station, liverpool"], "travel": "drive", "view": "auto", "orbit": None}, G(), origin=ME)
    assert out.ask and "Crewe" in out.ask and not out.summary and not out.need_location
    assert out.event["mode"] == "area" and out.event["targets"][0]["name"] == "Crewe"   # candidates shown on the map

# ---- places that don't exist: suggest similarly named ones and ask ----
from agent.geo import suggest_similar

def _place(name, display, lat, lon, kind="", imp=0.3):
    return {"query": name, "name": name, "display": display, "lat": lat, "lon": lon, "kind": kind, "bbox": None, "importance": imp}

FORMBY_TOWN = _place("Formby", "Formby, Sefton, United Kingdom", 53.5536, -3.0700, "town")
FORMBY_STN = _place("Formby Station", "Formby Station, Formby, Sefton, England, United Kingdom", 53.5544, -3.0706, "station", 0.4)
CREEK = _place("Fromby Creek", "Fromby Creek, Heard Island", -53.1, 73.5, "stream")
MELTON = _place("Central Station", "Central Station, Melton, Leicestershire, United Kingdom", 52.76, -0.89, "station")

class SugGeo:
    def __init__(self, search_tbl, suggest_tbl): self.s = search_tbl; self.g = suggest_tbl; self.suggested = []
    async def geocode(self, q): r = self.s.get(q.lower(), []); return r[0] if r else None
    async def search(self, q, limit=5): return self.s.get(q.lower(), [])
    async def suggest(self, q, near=None, limit=5): self.suggested.append(q); return self.g.get(q.lower(), [])
    async def route(self, *a): raise AssertionError("must not route while unsure")

@pytest.mark.asyncio
async def test_suggest_similar_filters_by_name_and_restores_type_word():
    g = SugGeo({"formby station": [FORMBY_STN]}, {"fromby station": [MELTON], "fromby": [CREEK, FORMBY_TOWN]})
    s = await suggest_similar("Fromby station", g)
    assert [x["name"] for x in s] == ["Formby Station"]           # creek/Melton filtered; station restored

@pytest.mark.asyncio
async def test_missing_place_with_similar_names_asks_did_you_mean():
    g = SugGeo({"formby station": [FORMBY_STN]}, {"fromby station": [MELTON], "fromby": [CREEK, FORMBY_TOWN]})
    r = await resolve_place("Fromby station", g)
    assert r.status == "unsure" and r.candidates[0]["name"] == "Formby Station"
    assert "Fromby station" in r.question and "Did you mean" in r.question and "Formby Station" in r.question

@pytest.mark.asyncio
async def test_missing_place_with_nothing_similar_asks_to_check_spelling():
    r = await resolve_place("Qwxzzy Plaza", SugGeo({}, {}))
    assert r.status == "missing" and "Qwxzzy Plaza" in r.question and "spelling" in r.question

@pytest.mark.asyncio
async def test_build_map_asks_did_you_mean_and_shows_suggestions():
    g = SugGeo({"formby station": [FORMBY_STN]}, {"fromby station": [MELTON], "fromby": [CREEK, FORMBY_TOWN]})
    out = await build_map({"mode": "route", "places": ["Fromby station"], "travel": "drive", "view": "auto", "orbit": None}, g, origin=ME)
    assert out.ask and "Did you mean" in out.ask and out.event["targets"][0]["name"] == "Formby Station"
    out2 = await build_map({"mode": "target", "places": ["Qwxzzy Plaza"], "travel": "drive", "view": "auto", "orbit": None}, SugGeo({}, {}))
    assert out2.ask and "spelling" in out2.ask and out2.event is None

@pytest.mark.asyncio
async def test_photon_suggest_parses_and_identifies_itself():
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"features": [{"geometry": {"coordinates": [-2.9925, 53.3995]},
            "properties": {"name": "Royal Albert Dock", "osm_value": "dock", "city": "Liverpool", "county": "Liverpool City Region", "country": "United Kingdom"}}]})
    g = Geo(transport=httpx.MockTransport(handler), min_interval=0)
    s = await g.suggest("Albart Dock", near=(-2.99, 53.41))
    assert s[0]["name"] == "Royal Albert Dock" and s[0]["lat"] == 53.3995 and "Liverpool" in s[0]["display"]
    assert "photon.komoot.io" in str(seen[0].url) and "AON" in seen[0].headers["user-agent"]
    assert "lat=53.41" in str(seen[0].url)

# ---- live-run fixes: duplicates, distance from the user, exact choices ----

def _n(name, lat, lon, imp=0.0, typ="station", display=None):
    return {"query": name, "name": name, "display": display or f"{name}, Somewhere", "lat": lat, "lon": lon,
            "kind": typ, "bbox": None, "importance": imp}

class TblGeo:
    def __init__(self, tbl, sugg=None): self.t = tbl; self.g = sugg or {}; self.near_seen = []
    async def geocode(self, q): r = self.t.get(q.lower(), []); return r[0] if r else None
    async def search(self, q, limit=5): return self.t.get(q.lower(), [])
    async def suggest(self, q, near=None, limit=10): self.near_seen.append(near); return self.g.get(q.lower(), [])

USER = (-2.9916, 53.4084)      # (lon, lat)

@pytest.mark.asyncio
async def test_duplicate_entries_of_one_place_count_once():
    stn = [_n("Formby Station", 53.5544, -3.0706, 0.00001), _n("Formby Station", 53.5536, -3.0707, 0.000011),
           _n("FORMBY STATION", 53.5534, -3.0707, 0.000012), _n("FORMBY STATION", 53.5533, -3.0711, 0.0000105)]
    r = await resolve_place("formby station", TblGeo({"formby station": stn}))
    assert r.status == "ok" and r.place["name"] == "Formby Station"

@pytest.mark.asyncio
async def test_big_city_wins_over_namesakes_and_near_user_wins_ties():
    lp = [_n("Liverpool", 53.3933, -2.9166, 0.73, "city"), _n("Liverpool", 53.4072, -2.9917, 0.73, "city"),
          _n("Liverpool", 29.2950, -95.2788, 0.466, "city", "Liverpool, Texas"), _n("Liverpool", 40.39, -90.0, 0.459, "city", "Liverpool, Illinois")]
    assert (await resolve_place("Liverpool", TblGeo({"liverpool": lp}))).status == "ok"
    vp = [_n("Victoria Park", 51.536, -0.039, 0.40, "park", "Victoria Park, London"), _n("Victoria Park", 53.45, -2.21, 0.38, "park", "Victoria Park, Manchester")]
    assert (await resolve_place("Victoria Park", TblGeo({"victoria park": vp}))).status == "unsure"
    r = await resolve_place("Victoria Park", TblGeo({"victoria park": vp}), near=USER)
    assert r.status == "ok" and "Manchester" in r.place["display"]          # the one near the user

@pytest.mark.asyncio
async def test_suggestions_use_the_users_location():
    town = _n("Formby", 53.5536, -3.0700, 0.3, "town", "Formby, Sefton")
    creek = _n("Fromby Creek", 40.0, -100.0, 0.0, "stream", "Fromby Creek, Kansas")
    g = TblGeo({"formby station": [_n("Formby Station", 53.5544, -3.0706)]}, {"fromby station": [], "fromby": [creek, town]})
    r = await resolve_place("Fromby station", g, near=USER)
    assert r.status == "unsure" and r.candidates[0]["name"] == "Formby Station" and "Did you mean" in r.question
    assert USER in g.near_seen

@pytest.mark.asyncio
async def test_exact_choice_with_coordinates_needs_no_lookup():
    class NoLookup:
        async def search(self, q, limit=5): raise AssertionError("no lookup for an exact choice")
        async def geocode(self, q): raise AssertionError
    r = await resolve_place("Crewe Station @53.0890,-2.4330", NoLookup())
    assert r.status == "ok" and r.place["name"] == "Crewe Station" and r.place["lat"] == 53.089 and r.place["lon"] == -2.433

@pytest.mark.asyncio
async def test_unsure_event_remembers_what_was_being_asked():
    g = TblGeo({"crewe station, liverpool": [], "crewe station": [_n("Crewe", 53.089, -2.433, 0.457, display="Crewe, Nantwich Road, Crewe, Cheshire East")],
                "liverpool": [_n("Liverpool", 53.4072, -2.9917, 0.73, "city")]})
    out = await build_map({"mode": "route", "places": ["crewe station, liverpool"], "travel": "walk", "view": "auto", "orbit": None}, g)
    assert out.event["pending"] == {"mode": "route", "travel": "walk", "from": None}

# ---- second live run: two typos at once, local bias ----

@pytest.mark.asyncio
async def test_suggestion_matches_word_by_word_with_several_typos():
    dock = _n("Royal Albert Dock", 53.3995, -2.9925, 0.385, "dock", "Royal Albert Dock, Liverpool")
    creek = _n("Fromby Creek", 40.0, -100.0, 0.0, "stream", "Fromby Creek, Kansas")
    g = TblGeo({}, {"albart dok": [dock], "fromby station": [creek], "fromby": [creek]})
    s = await suggest_similar("Albart Dok", g, near=USER)
    assert [x["name"] for x in s] == ["Royal Albert Dock"]
    assert await suggest_similar("Fromby station", g, near=USER) == []          # station is not a creek

@pytest.mark.asyncio
async def test_search_is_biased_to_the_users_area():
    seen = []
    def handler(req):
        seen.append(dict(httpx.QueryParams(req.url.query))); return httpx.Response(200, json=[])
    g = Geo(transport=httpx.MockTransport(handler), min_interval=0)
    await g.search("Victoria Park", near=USER)
    vb = [float(x) for x in seen[0]["viewbox"].split(",")]
    assert vb[0] < USER[0] < vb[2] and vb[3] < USER[1] < vb[1] and seen[0]["bounded"] == "0"
    await g.search("Victoria Park")
    assert "viewbox" not in seen[1]

# ---- junk lookups, near-certain spelling matches, location bias for named starts ----

@pytest.mark.asyncio
async def test_unrelated_lookup_results_are_dropped_and_spelling_variant_used():
    junk = [_n("Gloucester Volunteer Fire and Rescue Station Number 1", 37.4, -76.5, 0.2, "fire_station", "…, Virginia"),
            _n("Scarborough Railway Station", 54.28, -0.40, 0.3, "station", "Scarborough Railway Station, North Yorkshire")]
    edge = _n("Edge Hill", 53.4025, -2.9466, 0.3, "station", "Edge Hill, Liverpool, L7 5LZ")
    g = TblGeo({"edgehill station": junk, "edge hill station": [edge]}, {"edgehill station": [], "edgehill": [edge]})
    r = await resolve_place("Edgehill station", g, near=USER)
    assert r.status == "ok" and r.place["name"] == "Edge Hill" and r.note and "Edge Hill" in r.note

@pytest.mark.asyncio
async def test_weak_or_several_spelling_matches_still_ask():
    a = _n("Formby", 53.5536, -3.0700, 0.3, "town", "Formby, Sefton"); b = _n("Fromby Court", 53.40, -2.98, 0.0, "residential", "Fromby Court, Liverpool")
    g = TblGeo({}, {"fromby": [a, b], "fromby station": []})
    r = await resolve_place("Fromby", g, near=USER)
    assert r.status == "unsure" and len(r.candidates) == 2

@pytest.mark.asyncio
async def test_build_map_notes_assumed_spelling_in_summary():
    edge = _n("Edge Hill", 53.4025, -2.9466, 0.3, "station", "Edge Hill, Liverpool, L7 5LZ")
    bruns = _n("Brunswick", 53.3906, -2.9765, 0.3, "station", "Brunswick, Liverpool")
    class G(TblGeo):
        async def route(self, a, b, t): return {"distance": 2500.0, "duration": 600.0, "geometry": {"type": "LineString", "coordinates": [list(a), list(b)]}}
    g = G({"edgehill station": [], "edge hill station": [edge], "brunswick station": [bruns]}, {"edgehill station": [], "edgehill": [edge]})
    out = await build_map({"mode": "route", "places": ["Edgehill station", "brunswick station"], "travel": "drive", "view": "auto", "orbit": None}, g, origin=ME)
    assert out.event["start"]["name"] == "Edge Hill" and out.event["targets"][0]["name"] == "Brunswick"
    assert "Edgehill station" in out.summary and "Edge Hill" in out.summary          # the assumption is stated

@pytest.mark.asyncio
async def test_named_start_still_uses_user_location_for_ranking():
    me_near = _n("Brunswick Station", 53.3906, -2.9765, 0.1, "station", "Brunswick Station, Liverpool")
    far1 = _n("Brunswick Station", 43.91, -69.96, 0.4, "station", "Brunswick Station, Maine")
    far2 = _n("Brunswick Station", -37.77, 144.96, 0.4, "station", "Brunswick Station, Victoria")
    lime = _n("Liverpool Lime Street", 53.4075, -2.9778, 0.477, "station", "Liverpool Lime Street, Liverpool")
    class G(TblGeo):
        async def route(self, a, b, t): return {"distance": 2500.0, "duration": 600.0, "geometry": {"type": "LineString", "coordinates": [list(a), list(b)]}}
    g = G({"lime street station": [lime], "brunswick station": [far1, far2, me_near]})
    out = await build_map({"mode": "route", "places": ["Lime Street station", "brunswick station"], "travel": "drive", "view": "auto", "orbit": None}, g, origin=ME)
    assert out.event["start"]["name"] == "Liverpool Lime Street"          # named start kept, not "Your location"
    assert out.event["targets"][0]["display"].endswith("Liverpool")

@pytest.mark.asyncio
async def test_target_map_gets_a_factual_summary():
    dock = _n("Royal Albert Dock", 53.3995, -2.9925, 0.385, "dock", "Royal Albert Dock, Waterfront, Vauxhall, Liverpool, England")
    out = await build_map({"mode": "target", "places": ["Albert Dock"], "travel": "drive", "view": "auto", "orbit": None}, TblGeo({"albert dock": [dock]}))
    assert "Royal Albert Dock" in out.summary and "Liverpool" in out.summary and "53.3995" in out.summary

@pytest.mark.asyncio
async def test_named_place_type_picks_the_only_candidate_of_that_type():
    park = _n("Edgehill Park", 53.40, -3.10, 0.0, "park", "Edgehill Park, Moreton, Wirral")
    road = _n("Edgehill Road", 53.40, -3.11, 0.0, "residential", "Edgehill Road, Moreton, Wirral")
    edge_area = _n("Edge Hill", 53.401, -2.950, 0.2, "suburb", "Edge Hill, Liverpool")
    edge_stn = _n("Edge Hill", 53.4025, -2.9466, 0.3, "station", "Edge Hill, Liverpool, L7 5LZ")
    g = TblGeo({"edgehill station": [], "edge hill station": [edge_stn]},
               {"edgehill station": [park, road], "edgehill": [park, road, edge_area]})
    r = await resolve_place("Edgehill station", g, near=USER)
    assert r.status == "ok" and r.place["kind"] == "station" and r.place["name"] == "Edge Hill"
    assert r.note and "Edge Hill" in r.note
