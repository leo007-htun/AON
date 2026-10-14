/* AON map component, ported from wireframe-map.html (click to target, set start, my location, routing,
   3D, globe, orbit, palettes, layers, search). Adds: several targets, server-built routes/flight arcs,
   planner specs (new/update), state save/restore, and a compact mode for chat cards.
   Needs window.maplibregl. Colours: cyan/green; red only for warnings. */
(function () {
  'use strict';
  const PALETTES = {
    Blueprint: { bg:'#061b31', road:'#00e5ff', major:'#93ecff', bld:'#3d7bff', water:'#1e6fff', park:'#2fd18b', rail:'#2a8fa3', route:'#3dff9a', label:'#cde8f6' },
    Terminal:  { bg:'#020602', road:'#39ff6a', major:'#b6ffc8', bld:'#1fa64a', water:'#0f7a3a', park:'#1fa64a', rail:'#5fbf7a', route:'#c8ff39', label:'#b6ffc8' },
    Mono:      { bg:'#000000', road:'#d9d9d9', major:'#ffffff', bld:'#8c8c8c', water:'#6f6f6f', park:'#5a5a5a', rail:'#9a9a9a', route:'#ffffff', label:'#eeeeee' },
    Paper:     { bg:'#f3f5f7', road:'#1d2b38', major:'#000000', bld:'#5a7186', water:'#2a6fdb', park:'#2e8b57', rail:'#5a7186', route:'#00875a', label:'#1d2b38' }
  };
  const PLACES = { World:[10,25,1.6], Liverpool:[-2.9916,53.4048,15.5], London:[-0.1276,51.5072,15], Yangon:[96.1561,16.78,15],
                   Seoul:[126.978,37.5665,15], Tokyo:[139.7671,35.6812,15], Manhattan:[-73.9855,40.758,15] };
  const GROUPS = { Roads:['roads-minor','roads-major'], Buildings:['buildings','buildings-3d'], Water:['water','waterway'],
                   Borders:['boundary','graticule'], Parks:['park'], Rail:['rail'], Labels:['labels','place-labels'] };
  const PROFILE = { drive:'car', walk:'foot', cycle:'bike' };
  const DASH = [[0,4,3],[0.5,4,2.5],[1,4,2],[1.5,4,1.5],[2,4,1],[2.5,4,0.5],[3,4,0],[0,0.5,3,3.5],[0,1,3,3],[0,1.5,3,2.5],[0,2,3,2],[0,2.5,3,1.5],[0,3,3,1],[0,3.5,3,0.5]];
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const fmtKm = m => m >= 1000 ? (m/1000).toFixed(2) + ' km' : Math.round(m) + ' m';
  const fmtT = s => { const h = Math.floor(s/3600), m = Math.round((s%3600)/60); return h ? `${h} h ${m} min` : `${m} min`; };
  const esc = t => { const d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; };
  function haversine(a, b) {
    const R = 6371000, r = Math.PI/180, dLat = (b[1]-a[1])*r, dLon = (b[0]-a[0])*r;
    const x = Math.sin(dLat/2)**2 + Math.cos(a[1]*r)*Math.cos(b[1]*r)*Math.sin(dLon/2)**2;
    return 2*R*Math.asin(Math.sqrt(x));
  }
  function bearing(a, b) {
    const r = Math.PI/180, y = Math.sin((b[0]-a[0])*r)*Math.cos(b[1]*r);
    const x = Math.cos(a[1]*r)*Math.sin(b[1]*r) - Math.sin(a[1]*r)*Math.cos(b[1]*r)*Math.cos((b[0]-a[0])*r);
    return (Math.atan2(y, x)/r + 360) % 360;
  }
  function graticule(step) {
    const f = [];
    for (let lon = -180; lon <= 180; lon += step) { const c = []; for (let lat = -85; lat <= 85; lat += 2.5) c.push([lon, lat]); f.push({ type:'Feature', properties:{}, geometry:{ type:'LineString', coordinates:c } }); }
    for (let lat = -75; lat <= 75; lat += step) { const c = []; for (let lon = -180; lon <= 180; lon += 2.5) c.push([lon, lat]); f.push({ type:'Feature', properties:{}, geometry:{ type:'LineString', coordinates:c } }); }
    return { type:'FeatureCollection', features:f };
  }
  function makeStyle(pal) {
    return { version:8, glyphs:'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
      sources:{ omt:{ type:'vector', url:'https://tiles.openfreemap.org/planet' }, grat:{ type:'geojson', data:graticule(15) } },
      layers:[
        { id:'bg', type:'background', paint:{ 'background-color':pal.bg } },
        { id:'graticule', type:'line', source:'grat', maxzoom:6, paint:{ 'line-color':pal.road, 'line-opacity':0.25, 'line-width':0.6 } },
        { id:'boundary', type:'line', source:'omt', 'source-layer':'boundary', filter:['<=',['get','admin_level'],2], paint:{ 'line-color':pal.major, 'line-opacity':0.6, 'line-width':0.7, 'line-dasharray':[3,2] } },
        { id:'water', type:'line', source:'omt', 'source-layer':'water', paint:{ 'line-color':pal.water, 'line-width':1 } },
        { id:'waterway', type:'line', source:'omt', 'source-layer':'waterway', paint:{ 'line-color':pal.water, 'line-width':0.8, 'line-dasharray':[3,2] } },
        { id:'park', type:'line', source:'omt', 'source-layer':'park', paint:{ 'line-color':pal.park, 'line-width':0.8, 'line-dasharray':[1,2] } },
        { id:'roads-minor', type:'line', source:'omt', 'source-layer':'transportation', filter:['in',['get','class'],['literal',['minor','service','track','path','tertiary']]],
          paint:{ 'line-color':pal.road, 'line-opacity':.55, 'line-width':['interpolate',['linear'],['zoom'],12,0.3,17,1] } },
        { id:'roads-major', type:'line', source:'omt', 'source-layer':'transportation', filter:['in',['get','class'],['literal',['motorway','trunk','primary','secondary']]],
          paint:{ 'line-color':pal.major, 'line-width':['interpolate',['linear'],['zoom'],8,0.5,17,2] } },
        { id:'rail', type:'line', source:'omt', 'source-layer':'transportation', filter:['==',['get','class'],'rail'], paint:{ 'line-color':pal.rail, 'line-width':1, 'line-dasharray':[4,3] } },
        { id:'buildings', type:'line', source:'omt', 'source-layer':'building', minzoom:13, paint:{ 'line-color':pal.bld, 'line-width':0.7 } },
        { id:'buildings-3d', type:'fill-extrusion', source:'omt', 'source-layer':'building', minzoom:13, layout:{ visibility:'none' },
          paint:{ 'fill-extrusion-color':pal.bld, 'fill-extrusion-opacity':0.35, 'fill-extrusion-height':['coalesce',['get','render_height'],6], 'fill-extrusion-base':['coalesce',['get','render_min_height'],0] } },
        { id:'labels', type:'symbol', source:'omt', 'source-layer':'transportation_name', minzoom:14,
          layout:{ 'text-field':['get','name'], 'symbol-placement':'line', 'text-font':['Noto Sans Regular'], 'text-size':10, 'text-letter-spacing':.08 },
          paint:{ 'text-color':pal.label, 'text-halo-color':pal.bg, 'text-halo-width':1.5 } },
        { id:'place-labels', type:'symbol', source:'omt', 'source-layer':'place',
          layout:{ 'text-field':['upcase',['get','name']], 'text-font':['Noto Sans Regular'], 'text-size':['interpolate',['linear'],['zoom'],8,10,15,13], 'text-letter-spacing':.2 },
          paint:{ 'text-color':pal.label, 'text-halo-color':pal.bg, 'text-halo-width':2 } }
      ] };
  }

  function create(root, opts) {
    opts = opts || {};
    const full = !!opts.full;
    let palName = (opts.state && opts.state.palette) || 'Blueprint', pal = PALETTES[palName];
    let is3D = false, isGlobe = false, spinning = false, wantOrbit = false, raf = 0, resumeT = 0, userPaused = false;
    let targets = [], start = null, route = null, routes = null, travel = 'drive', pickStart = false, gridOn = true, dashRaf = 0, dashStep = 0, lastDash = 0;
    const tMarkers = []; let sMarker = null;
    root.classList.add('amap', full ? 'full' : 'compact'); root.innerHTML = '';
    root.insertAdjacentHTML('beforeend', '<div class="amap-canvas"></div><div class="amap-grid"></div>' +
      '<div class="amap-info"><div class="amap-read">lat <b data-r="lat">–</b> · lon <b data-r="lon">–</b> · z <b data-r="zoom">–</b></div>' +
      '<div class="amap-nav">Click the map to lock a target.</div></div>');
    const q = s => root.querySelector(s);
    const nav = q('.amap-nav'), grid = q('.amap-grid');
    const say = (html, warn) => { nav.innerHTML = html; nav.classList.toggle('warn', !!warn); };
    const changed = () => { if (opts.onState) opts.onState(state()); };

    const map = new maplibregl.Map({ container: q('.amap-canvas'), style: makeStyle(pal), center: PLACES.Liverpool.slice(0,2), zoom: 3,
      pitch: 0, maxPitch: 75, attributionControl: { compact: true,
        customAttribution: '<a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>' } });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
    if (full) map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right');

    /* ---------- controls ---------- */
    const btn = (label, title) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = label; if (title) b.title = title; return b; };
    const ui = {};
    if (full) {
      const p = document.createElement('div'); p.className = 'amap-panel';
      p.innerHTML = '<h1>Wireframe Explorer</h1>' +
        '<div><div class="lbl">Search place</div><form data-k="search"><input type="text" placeholder="e.g. Albert Dock, Liverpool" autocomplete="off"></form><div data-k="msg" style="color:var(--dim);min-height:14px"></div></div>' +
        '<div><div class="lbl">Navigate</div><div class="row" data-k="navrow"></div><div class="row" data-k="profiles" style="margin-top:6px"></div><div class="row" data-k="routerow" style="margin-top:6px"></div></div>' +
        '<div><div class="lbl">Jump to</div><div class="row" data-k="places"></div></div>' +
        '<div><div class="lbl">Layers</div><div class="toggles" data-k="toggles"></div></div>' +
        '<div><div class="lbl">View</div><div class="row" data-k="view"></div></div>' +
        '<div><div class="lbl">Palette</div><div class="row" data-k="palettes"></div></div>' +
        '<div><div class="lbl">Line weight</div><input type="range" min="0.4" max="2.5" step="0.1" value="1" data-k="weight"></div>';
      root.appendChild(p);
      const k = n => p.querySelector(`[data-k="${n}"]`);
      ui.start = btn('Set start'); ui.me = btn('My location'); k('navrow').append(ui.start, ui.me);
      ui.profiles = {}; for (const [t, l] of [['drive','Drive'],['walk','Walk'],['cycle','Cycle']]) { ui.profiles[t] = btn(l); k('profiles').append(ui.profiles[t]); }
      ui.route = btn('Route to target'); ui.clear = btn('Clear'); k('routerow').append(ui.route, ui.clear);
      for (const [name, [lon, lat, z]] of Object.entries(PLACES)) { const b = btn(name); b.onclick = () => { stopOrbit(); map.flyTo({ center:[lon, lat], zoom:z, speed:1.4 }); }; k('places').append(b); }
      for (const name of Object.keys(GROUPS)) {
        const l = document.createElement('label'); l.innerHTML = `<input type="checkbox" checked> ${name}`;
        l.querySelector('input').onchange = e => { for (const id of GROUPS[name]) { if (id === 'buildings-3d' && !is3D) continue; map.setLayoutProperty(id, 'visibility', e.target.checked ? 'visible' : 'none'); } };
        l.dataset.g = name; k('toggles').append(l);
      }
      ui.globe = btn('Globe'); ui.d3 = btn('3D blocks'); ui.grid = btn('Grid'); ui.orbit = btn('Orbit'); k('view').append(ui.globe, ui.d3, ui.grid, ui.orbit);
      ui.pals = {}; for (const name of Object.keys(PALETTES)) { const b = btn(name); b.onclick = () => applyPalette(name); ui.pals[name] = b; k('palettes').append(b); }
      k('weight').oninput = e => setWeight(+e.target.value);
      const msg = k('msg');
      k('search').onsubmit = async e => {
        e.preventDefault(); const v = e.target.querySelector('input').value.trim(); if (!v) return;
        msg.textContent = 'Searching…';
        try {
          const r = await fetch('https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(v));
          const [hit] = await r.json();
          if (!hit) { msg.textContent = 'No match. Try adding a city name.'; return; }
          msg.textContent = hit.display_name.split(',').slice(0,2).join(',');
          setTargets([{ name: hit.display_name.split(',')[0], lat: +hit.lat, lon: +hit.lon }]); focusTargets();
        } catch { msg.textContent = 'Search failed. Check your connection.'; }
      };
    } else {
      const bar = document.createElement('div'); bar.className = 'amap-bar';
      ui.d2 = btn('2D'); ui.d3 = btn('3D'); ui.globe = btn('Globe'); ui.orbit = btn('Orbit');
      ui.start = btn('Set start', 'Click the map to set your start point (or Shift+click)'); ui.me = btn('Me', 'My location');
      ui.sel = document.createElement('select'); ui.sel.title = 'Travel mode';
      ui.sel.innerHTML = '<option value="drive">Drive</option><option value="walk">Walk</option><option value="cycle">Cycle</option>';
      ui.route = btn('Route'); ui.expand = btn('⤢ Expand', 'Open the full explorer');
      const sep = () => { const s = document.createElement('span'); s.className = 'sep'; return s; };
      bar.append(ui.d2, ui.d3, ui.globe, ui.orbit, sep(), ui.start, ui.me, ui.sel, ui.route, sep(), ui.expand);
      root.appendChild(bar);
      ui.sel.onchange = () => switchTravel(ui.sel.value);
      ui.d2.onclick = () => setView('2d', true);
      if (opts.onExpand) ui.expand.onclick = () => opts.onExpand(state()); else ui.expand.remove();
    }
    if (ui.grid) ui.grid.onclick = () => { gridOn = !gridOn; grid.hidden = !gridOn; syncButtons(); };
    ui.d3.onclick = () => setView(is3D ? '2d' : '3d', true);
    ui.globe.onclick = () => setView(isGlobe ? '2d' : 'globe', true);
    ui.orbit.onclick = () => { wantOrbit = !wantOrbit; wantOrbit ? startOrbit() : stopOrbit(); syncButtons(); changed(); };
    ui.start.onclick = () => { pickStart = !pickStart; syncButtons(); say(pickStart ? 'Click the map to set your start point.' : ''); };
    ui.me.onclick = () => {
      if (!window.isSecureContext) { say('My location needs HTTPS; this page is plain HTTP. Use <b>Set start</b>, or type the start in the chat.', true); return; }
      if (!navigator.geolocation) { say('Location is not available in this browser.', true); return; }
      say('Finding you…');
      navigator.geolocation.getCurrentPosition(pos => { setStart([pos.coords.longitude, pos.coords.latitude]); map.flyTo({ center: start, zoom: 15 }); },
        () => say('Location blocked. Allow it in the browser, or use Set start.', true), { enableHighAccuracy: true, timeout: 10000 });
    };
    if (ui.profiles) for (const [t, b] of Object.entries(ui.profiles)) b.onclick = () => switchTravel(t);
    ui.route.onclick = () => routeToTarget();
    if (ui.clear) ui.clear.onclick = () => { clearAll(); say('Click the map to lock a target.'); changed(); };

    function syncButtons() {
      if (ui.d2) ui.d2.classList.toggle('on', !is3D && !isGlobe);
      ui.d3.classList.toggle('on', is3D); ui.globe.classList.toggle('on', isGlobe); ui.orbit.classList.toggle('on', wantOrbit);
      ui.start.classList.toggle('on', pickStart);
      if (ui.grid) ui.grid.classList.toggle('on', gridOn);
      if (ui.profiles) for (const [t, b] of Object.entries(ui.profiles)) b.classList.toggle('on', t === travel);
      if (ui.sel && PROFILE[travel]) ui.sel.value = travel;
      if (ui.pals) for (const [n, b] of Object.entries(ui.pals)) b.classList.toggle('on', n === palName);
    }

    /* ---------- palette / weight ---------- */
    function applyPalette(name) {
      palName = name; pal = PALETTES[name];
      const set = (l, p, v) => { if (map.getLayer(l)) map.setPaintProperty(l, p, v); };
      set('bg','background-color', pal.bg); set('graticule','line-color', pal.road); set('boundary','line-color', pal.major);
      set('water','line-color', pal.water); set('waterway','line-color', pal.water); set('park','line-color', pal.park);
      set('roads-minor','line-color', pal.road); set('roads-major','line-color', pal.major); set('rail','line-color', pal.rail);
      set('buildings','line-color', pal.bld); set('buildings-3d','fill-extrusion-color', pal.bld);
      for (const l of ['labels','place-labels']) { set(l,'text-color', pal.label); set(l,'text-halo-color', pal.bg); }
      for (const l of ['route-glow','route-line']) set(l, 'line-color', pal.route);
      root.style.background = pal.bg; root.style.setProperty('--tgt', pal.road); root.style.setProperty('--route', pal.route);
      setAtmosphere(); syncButtons(); changed();
    }
    function setWeight(w) {
      const set = (l, v) => { if (map.getLayer(l)) map.setPaintProperty(l, 'line-width', v); };
      set('water', w); set('park', 0.8*w); set('rail', w); set('buildings', 0.7*w);
      set('roads-minor', ['interpolate',['linear'],['zoom'],12,0.3*w,17,w]); set('roads-major', ['interpolate',['linear'],['zoom'],8,0.5*w,17,2*w]);
    }
    function setAtmosphere() {
      try { map.setSky({ 'sky-color': pal.bg, 'horizon-color': pal.road, 'fog-color': pal.bg, 'atmosphere-blend': ['interpolate',['linear'],['zoom'],0,0.6,5,0.3,7,0] }); } catch (e) {}
    }

    /* ---------- view / orbit ---------- */
    function setView(v, user) {
      const was3D = is3D, wasGlobe = isGlobe;
      is3D = v === '3d'; isGlobe = v === 'globe';
      if (isGlobe !== wasGlobe) map.setProjection({ type: isGlobe ? 'globe' : 'mercator' });
      const bOn = !full || q('[data-g="Buildings"] input').checked;
      if (map.getLayer('buildings-3d')) map.setLayoutProperty('buildings-3d', 'visibility', is3D && bOn ? 'visible' : 'none');
      if (user) {
        if (isGlobe && !wasGlobe) map.flyTo({ center: [map.getCenter().lng, Math.max(-60, Math.min(60, map.getCenter().lat))], zoom: 1.6, pitch: 0, bearing: 0, speed: 1.2 });
        else if (is3D !== was3D) map.easeTo({ pitch: is3D ? 60 : 0, bearing: is3D ? -20 : 0, duration: 900 });
      }
      syncButtons(); changed();
    }
    // time-based so the orbit turns at the same speed on a 60 fps GPU and a slow laptop
    let lastSpin = 0;
    function spin(t) {
      const dt = lastSpin ? Math.min(0.25, (t - lastSpin) / 1000) : 0; lastSpin = t;
      if (isGlobe && map.getZoom() < 5) { const c = map.getCenter(); map.setCenter([c.lng + 15 * dt, c.lat]); }
      else map.setBearing(map.getBearing() + 9 * dt);
      raf = requestAnimationFrame(spin);
    }
    function startOrbit() { cancelAnimationFrame(raf); spinning = false; if (!wantOrbit || reduce) return; spinning = true; lastSpin = 0; raf = requestAnimationFrame(spin); }
    function stopOrbit() { cancelAnimationFrame(raf); spinning = false; }
    // dragging pauses the orbit; it resumes a few seconds after the user lets go
    // (orbit starts when the camera settles, so it never cuts a fly-to short)
    map.on('mousedown', () => { userPaused = true; if (spinning) stopOrbit(); clearTimeout(resumeT); });
    map.on('touchstart', () => { userPaused = true; if (spinning) stopOrbit(); clearTimeout(resumeT); });
    map.on('moveend', () => {
      if (wantOrbit && !spinning) { clearTimeout(resumeT); resumeT = setTimeout(startOrbit, userPaused ? 4000 : 300); userPaused = false; }
    });

    /* ---------- readout ---------- */
    function readout() {
      const c = map.getCenter();
      q('[data-r="lat"]').textContent = c.lat.toFixed(4); q('[data-r="lon"]').textContent = c.lng.toFixed(4); q('[data-r="zoom"]').textContent = map.getZoom().toFixed(1);
    }
    map.on('move', readout);

    /* ---------- targets / start ---------- */
    function reticle() {
      const el = document.createElement('div'); el.className = 'tgt';
      el.innerHTML = '<div class="ring"></div><div class="ring r2"></div><div class="cross"></div><i class="tl"></i><i class="tr"></i><i class="bl"></i><i class="br"></i><div class="dot"></div><div class="tag"></div>';
      return el;
    }
    function tagFor(t, i) {
      let extra = '';
      if (start) extra = `<br>RNG ${fmtKm(haversine(start, [t.lon, t.lat]))} · BRG ${bearing(start, [t.lon, t.lat]).toFixed(0).padStart(3,'0')}°`;
      const id = targets.length > 1 ? `TGT-${String(i+1).padStart(2,'0')} ` : '';
      return `<b>${id}TARGET LOCKED</b>${t.name ? esc(t.name) + '<br>' : ''}${t.lat.toFixed(5)}, ${t.lon.toFixed(5)}${extra}`;
    }
    function drawTargets() {
      while (tMarkers.length > targets.length) tMarkers.pop().remove();
      targets.forEach((t, i) => {
        if (!tMarkers[i]) tMarkers[i] = new maplibregl.Marker({ element: reticle() }).setLngLat([t.lon, t.lat]).addTo(map);
        else tMarkers[i].setLngLat([t.lon, t.lat]);
        const el = tMarkers[i].getElement();
        el.querySelector('.tag').innerHTML = tagFor(t, i);
        el.classList.remove('lock'); void el.offsetWidth; el.classList.add('lock');
      });
    }
    function setTargets(list, keepRoute) {
      targets = (list || []).filter(t => isFinite(t.lat) && isFinite(t.lon)).map(t => ({ name: t.name || '', lat: +t.lat, lon: +t.lon }));
      if (!keepRoute) { clearRoute(); routes = null; }
      drawTargets();
      say(!targets.length ? 'Click the map to lock a target.' : start ? 'Target set. Press <b>Route</b>.' : 'Target locked. Set a start point to route there.');
      changed();
    }
    function setStart(ll, name) {
      start = [ll[0], ll[1]];
      if (!sMarker) { const el = document.createElement('div'); el.className = 'amap-start'; sMarker = new maplibregl.Marker({ element: el }).setLngLat(start).addTo(map); }
      else sMarker.setLngLat(start);
      if (name) sMarker.getElement().title = name;
      const lbl = name === 'Your location' ? 'YOU' : 'START';
      sMarker.getElement().innerHTML = '<span class="lbl">' + lbl + '</span>';
      routes = null;
      pickStart = false; syncButtons(); drawTargets();
      say(targets.length ? 'Start set. Press <b>Route</b>.' : 'Start set. Click the map to lock a target.');
      changed();
    }
    map.on('click', e => {
      const ll = [e.lngLat.lng, e.lngLat.lat];
      if (pickStart || e.originalEvent.shiftKey) { setStart(ll); return; }
      let name = '';
      const f = map.queryRenderedFeatures(e.point, { layers: ['buildings','buildings-3d','roads-major','roads-minor','rail','park'].filter(l => map.getLayer(l)) })[0];
      if (f) {
        const p = f.properties;
        name = p.name || (f.sourceLayer === 'transportation' ? 'road · ' + p.class : f.sourceLayer);
        if (p.render_height) name += ` · ${Math.round(p.render_height)} m`;
      }
      setTargets([{ name, lat: ll[1], lon: ll[0] }]);
    });

    /* ---------- routes ---------- */
    function animateDash(t) {
      if (t - lastDash > 50) { dashStep = (dashStep + 1) % DASH.length; lastDash = t; if (map.getLayer('route-line')) map.setPaintProperty('route-line', 'line-dasharray', DASH[dashStep]); }
      dashRaf = requestAnimationFrame(animateDash);
    }
    function ensureRouteLayers() {
      if (map.getSource('route')) return;
      map.addSource('route', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      map.addLayer({ id: 'route-glow', type: 'line', source: 'route', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': pal.route, 'line-width': 10, 'line-opacity': 0.18, 'line-blur': 4 } });
      map.addLayer({ id: 'route-line', type: 'line', source: 'route', layout: { 'line-join': 'round' }, paint: { 'line-color': pal.route, 'line-width': 3, 'line-dasharray': DASH[0] } });
    }
    function clearRoute() {
      route = null;
      if (map.getSource('route')) map.getSource('route').setData({ type: 'FeatureCollection', features: [] });
      cancelAnimationFrame(dashRaf);
    }
    function drawRoute(rt, fit) {
      route = rt; ensureRouteLayers();
      map.getSource('route').setData({ type: 'Feature', properties: {}, geometry: rt.geometry });
      cancelAnimationFrame(dashRaf); if (!reduce) dashRaf = requestAnimationFrame(animateDash);
      if (fit) {
        const b = new maplibregl.LngLatBounds(); rt.geometry.coordinates.forEach(c => b.extend(c));
        // room for the toolbar (top), readout (bottom) and the reticle tag that extends to the right
        const pad = full ? { top: 90, bottom: 110, left: 320, right: 220 } : { top: 70, bottom: 110, left: 60, right: 190 };
        map.fitBounds(b, { padding: pad, duration: 1200, pitch: 0, bearing: 0 });
      }
      const NAMES = { drive: 'Drive', walk: 'Walk', cycle: 'Cycle', flight: 'Flight' };
      const cur = rt.travel || travel;
      const t = targets[0];
      const straight = start && t ? `<br>Straight line ${fmtKm(haversine(start, [t.lon, t.lat]))}` : '';
      const line = (k, r) => `${k === cur ? '▸ ' : ''}${NAMES[k]}: ` + (r ? `<b>${fmtKm(r.distance)}</b>${r.duration ? ' · <b>' + fmtT(r.duration) + '</b>' : ''}` : 'no route');
      if (routes && Object.keys(routes).length > 1) say(['walk', 'drive', 'cycle'].filter(k => k in routes).map(k => line(k, routes[k])).join('<br>') + straight);
      else say(line(cur, rt) + straight);
      changed();
    }
    function switchTravel(t) {
      travel = t; syncButtons();
      if (routes && routes[t] && routes[t].geometry) drawRoute(Object.assign({ travel: t }, routes[t]), false);
      else if (start && targets.length) routeToTarget();
      changed();
    }
    async function routeToTarget() {
      const t = targets[0];
      if (!t) { say('Lock a target first: click the map.', true); return; }
      if (!start) { say('Set a start first: <b>Set start</b>, Shift+click, or type it in the chat.', true); return; }
      stopOrbit(); wantOrbit = false;
      if (is3D || isGlobe) setView('2d', false);
      say('Calculating route…');
      const prof = PROFILE[travel] || 'car';
      const url = `https://routing.openstreetmap.de/routed-${prof}/route/v1/driving/${start[0]},${start[1]};${t.lon},${t.lat}?overview=full&geometries=geojson`;
      try {
        const r = await fetch(url); const j = await r.json();
        if (!j.routes || !j.routes.length) { say('No route found between these points.', true); return; }
        const rt = j.routes[0];
        drawRoute({ distance: rt.distance, duration: rt.duration, geometry: rt.geometry, travel }, true);
      } catch { say('Routing failed. Check your connection and try again.', true); }
    }
    function clearAll() {
      clearRoute(); routes = null; tMarkers.forEach(m => m.remove()); tMarkers.length = 0; targets = [];
      if (sMarker) { sMarker.remove(); sMarker = null; } start = null;
    }

    /* ---------- camera ---------- */
    function focusTargets() {
      if (!targets.length) return;
      if (targets.length === 1) {
        const t = targets[0];
        map.flyTo({ center: [t.lon, t.lat], zoom: isGlobe ? 3 : 16, pitch: is3D ? 60 : 0, bearing: is3D ? -20 : 0, speed: 1.3 });
      } else {
        const b = new maplibregl.LngLatBounds(); targets.forEach(t => b.extend([t.lon, t.lat]));
        map.fitBounds(b, { padding: full ? { top: 90, bottom: 110, left: 320, right: 220 } : { top: 70, bottom: 110, left: 60, right: 190 }, duration: 1200, maxZoom: 15 });
      }
    }

    /* ---------- planner specs ---------- */
    function applySpec(s) {
      if (!s) return;
      if (s.action === 'update') {
        const moved = s.view && s.view !== 'auto' && s.view !== (isGlobe ? 'globe' : is3D ? '3d' : '2d');
        if (moved) { stopOrbit(); setView(s.view, true); }
        if (typeof s.orbit === 'boolean') { wantOrbit = s.orbit; if (!wantOrbit) stopOrbit(); else if (!moved) startOrbit(); }
        if (s.travel && PROFILE[s.travel] && s.travel !== travel) switchTravel(s.travel);
        syncButtons(); changed(); return;
      }
      clearAll();
      travel = s.travel || 'drive';
      const finalView = s.view === '3d' || s.view === 'globe' ? s.view : '2d';
      const intro = s.intro === 'globe' && s.route && s.route.geometry && !reduce;
      setView(intro ? 'globe' : finalView, false);
      if (s.start) setStart([s.start.lon, s.start.lat], s.start.name);
      setTargets(s.targets || [], true);
      routes = s.routes || null;          // after setStart/setTargets, which reset it
      wantOrbit = !!s.orbit;
      if (intro) {
        // open on the globe over the target, then fly down into the route in 3D
        const t = targets[0] || { lon: s.route.geometry.coordinates[0][0], lat: s.route.geometry.coordinates[0][1] };
        map.jumpTo({ center: [t.lon, t.lat], zoom: 1.6, pitch: 0, bearing: 0 });
        drawRoute(Object.assign({ travel }, s.route), false);
        setTimeout(() => {
          const b = new maplibregl.LngLatBounds(); s.route.geometry.coordinates.forEach(c => b.extend(c));
          const pad = full ? { top: 90, bottom: 110, left: 320, right: 220 } : { top: 70, bottom: 110, left: 60, right: 190 };
          const cam = map.cameraForBounds(b, { padding: pad }) || { center: [t.lon, t.lat], zoom: 15 };
          const v3 = finalView === '3d';
          map.flyTo({ center: cam.center, zoom: cam.zoom, pitch: v3 ? 55 : 0, bearing: v3 ? -20 : 0, duration: 4500, essential: true });
          map.once('moveend', () => setView(finalView, false));   // switch projection + 3D buildings once landed
        }, 900);
      } else if (s.route && s.route.geometry) drawRoute(Object.assign({ travel }, s.route), true);
      else focusTargets();
      if (s.mode === 'route' && !s.route && s.start) say('No route found between these points.', true);
      syncButtons();   // orbit (if wanted) starts on the 'moveend' after the camera lands
    }
    function state() {
      const c = map.getCenter();
      return { v: 1, palette: palName, view: isGlobe ? 'globe' : is3D ? '3d' : '2d', orbit: wantOrbit, travel,
               targets: targets.slice(), start: start ? { lon: start[0], lat: start[1], name: sMarker && sMarker.getElement().title || '' } : null,
               route, routes, camera: { center: [c.lng, c.lat], zoom: map.getZoom(), pitch: map.getPitch(), bearing: map.getBearing() } };
    }
    function restore(st) {
      if (!st) return;
      clearAll(); travel = st.travel || 'drive';
      if (st.palette && st.palette !== palName) applyPalette(st.palette);
      setView(st.view || '2d', false);
      if (st.start) setStart([st.start.lon, st.start.lat], st.start.name);
      setTargets(st.targets || [], true);
      routes = st.routes || null;
      if (st.route && st.route.geometry) drawRoute(st.route, false);
      if (st.camera) map.jumpTo(st.camera);
      wantOrbit = !!st.orbit; if (wantOrbit) setTimeout(startOrbit, 300); syncButtons();
    }

    let ready = false; const queue = [];
    map.on('load', () => {
      ready = true; readout(); setAtmosphere(); applyPalette(palName); syncButtons();
      if (opts.state) restore(opts.state); else if (opts.spec) applySpec(opts.spec);
      while (queue.length) queue.shift()();
    });
    const whenReady = fn => ready ? fn() : queue.push(fn);
    return {
      map, applySpec: s => whenReady(() => applySpec(s)), restore: s => whenReady(() => restore(s)), state,
      setView: v => whenReady(() => setView(v, true)), routeToTarget: () => whenReady(routeToTarget),
      destroy: () => { stopOrbit(); cancelAnimationFrame(dashRaf); clearTimeout(resumeT); map.remove(); }
    };
  }
  window.AonMap = { create, PALETTES };
})();
