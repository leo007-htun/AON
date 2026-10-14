from fastapi.testclient import TestClient
from agent.config import Config
from agent.persistence import Database
from agent.web import create_app

def test_static_is_served(tmp_path):
    db=Database(str(tmp_path/"d.db"))
    def ma(g,o,m): raise AssertionError
    app=create_app(Config("http://x/v1","gpt-oss",tmp_path,[],5,10,"127.0.0.1:8700"), db, ma)
    c=TestClient(app)
    r=c.get("/static/index.html")
    assert r.status_code == 200

def test_cybercore_theme_assets_are_linked_and_served(tmp_path):
    import re
    db=Database(str(tmp_path/"d.db"))
    def ma(g,o,m): raise AssertionError
    c=TestClient(create_app(Config("http://x/v1","gpt-oss",tmp_path,[],5,10,"127.0.0.1:8700"), db, ma))
    html=c.get("/").text
    for href in ["/static/lib/cybercore.min.css","/static/lib/fonts.css"]:
        assert href in html and c.get(href).status_code == 200, href
    # no external font CDN: the UI must render on the LAN without internet
    assert "fonts.googleapis.com" not in html
    for url in set(re.findall(r'url\((/static/fonts/[^)]+)\)', c.get("/static/lib/fonts.css").text)):
        assert c.get(url).status_code == 200, url
    sprite=c.get("/static/lib/cyber-icons.svg")
    assert sprite.status_code == 200
    ids=set(re.findall(r'<symbol id="([^"]+)"', sprite.text))
    used=set(re.findall(r'cyber-icons\.svg#([a-z0-9-]+)', html)) | set(re.findall(r"IC\('([a-z0-9-]+)'", html))
    assert used and used <= ids, used - ids

def test_map_assets_are_served_and_wired(tmp_path):
    db=Database(str(tmp_path/"d.db"))
    def ma(g,o,m): raise AssertionError
    c=TestClient(create_app(Config("http://x/v1","gpt-oss",tmp_path,[],5,10,"127.0.0.1:8700"), db, ma))
    html=c.get("/").text
    for path in ["/static/lib/maplibre-gl.js","/static/lib/maplibre-gl.css","/static/lib/aon-map.js","/static/lib/aon-map.css"]:
        assert path in html and c.get(path).status_code == 200, path      # self-hosted, lazily loaded
    assert "unpkg.com" not in html and "/api/map" in html and "m.type==='map'" in html
    js=c.get("/static/lib/aon-map.js").text
    assert "window.AonMap" in js and "isSecureContext" in js and "routing.openstreetmap.de" in js

def test_websocket_follows_page_protocol(tmp_path):
    # behind HTTPS (Caddy at aon.vulkron.uk) a plain ws:// socket is blocked as mixed content
    db=Database(str(tmp_path/"d.db"))
    def ma(g,o,m): raise AssertionError
    html=TestClient(create_app(Config("http://x/v1","gpt-oss",tmp_path,[],5,10,"127.0.0.1:8700"), db, ma)).get("/").text
    assert "new WebSocket(`ws://" not in html
    assert "location.protocol==='https:'?'wss':'ws'" in html

def test_map_markers_stay_on_their_coordinates(tmp_path):
    # MapLibre positions markers absolutely via an inline transform; a marker root that is
    # position:relative (or has its own transform) is shifted by earlier markers and drifts on zoom
    import re
    db=Database(str(tmp_path/"d.db"))
    def ma(g,o,m): raise AssertionError
    css=TestClient(create_app(Config("http://x/v1","gpt-oss",tmp_path,[],5,10,"127.0.0.1:8700"), db, ma)).get("/static/lib/aon-map.css").text
    for sel in [".tgt", ".amap-start"]:
        rule=re.search(re.escape(sel)+r"\{([^}]*)\}", css).group(1)
        assert "position:absolute" in rule and "transform" not in rule, (sel, rule)

def test_map_assets_are_loaded_with_a_version_tag(tmp_path):
    # without a version in the URL a browser can keep an old aon-map.css/js after an update
    import re
    db=Database(str(tmp_path/"d.db"))
    def ma(g,o,m): raise AssertionError
    html=TestClient(create_app(Config("http://x/v1","gpt-oss",tmp_path,[],5,10,"127.0.0.1:8700"), db, ma)).get("/").text
    assert re.search(r"const MAP_V='[^']+'", html)
    for f in ["aon-map.css", "aon-map.js"]:
        assert f"/static/lib/{f}?v='+MAP_V" in html, f

def test_page_answers_location_requests(tmp_path):
    db=Database(str(tmp_path/"d.db"))
    def ma(g,o,m): raise AssertionError
    c=TestClient(create_app(Config("http://x/v1","gpt-oss",tmp_path,[],5,10,"127.0.0.1:8700"), db, ma))
    html=c.get("/").text
    assert "m.type==='need_location'" in html and "function sendLocation" in html
    assert "isSecureContext" in html and "reply({error:'unavailable'})" in html   # plain http answers at once
    js=c.get("/static/lib/aon-map.js").text
    assert "function switchTravel" in js and "s.intro === 'globe'" in js and "'YOU'" in js

def test_page_has_a_stop_button(tmp_path):
    db=Database(str(tmp_path/"d.db"))
    def ma(g,o,m): raise AssertionError
    html=TestClient(create_app(Config("http://x/v1","gpt-oss",tmp_path,[],5,10,"127.0.0.1:8700"), db, ma)).get("/").text
    assert "function stopRun" in html and "type:'stop'" in html and "m.status==='stopped'" in html


def test_chat_welcome_logo_is_the_animated_loader(tmp_path):
    db=Database(str(tmp_path/"d.db"))
    def ma(g,o,m): raise AssertionError
    c=TestClient(create_app(Config("http://x/v1","gpt-oss",tmp_path,[],5,10,"127.0.0.1:8700"), db, ma))
    html=c.get("/").text
    welcome=html.split('<div id="welcome">')[1].split('</div>')[0]
    assert '<img class="wlogo" src="/static/aon-loader.svg"' in welcome and "<svg" not in welcome
    r=c.get("/static/aon-loader.svg")
    assert r.status_code==200 and "@keyframes spin" in r.text


def test_page_adapts_to_phone_screens(tmp_path):
    db=Database(str(tmp_path/"d.db"))
    def ma(g,o,m): raise AssertionError
    html=TestClient(create_app(Config("http://x/v1","gpt-oss",tmp_path,[],5,10,"127.0.0.1:8700"), db, ma)).get("/").text
    assert 'name="viewport" content="width=device-width' in html
    assert "@media(max-width:600px)" in html and 'id="sbBackdrop"' in html      # sidebar overlay + tap-out
    assert "if(narrow())document.body.classList.add('sb-collapsed')" in html     # starts closed on small screens
    assert 'class="nav-item phone-only"' in html                                 # rail entries reachable on phones
