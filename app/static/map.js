/* Local Leaflet map + Natural Earth geography. Every shipment route is illustrative. */
(() => {
  "use strict";
  const host = document.getElementById("world-map");
  const status = document.getElementById("map-loading");
  const origin = [22.5431, 114.0579];
  const markets = {
    US: { name: "美国", city: "洛杉矶", location: [34.0522, -118.2437], position: "left" },
    GB: { name: "英国", city: "伦敦", location: [51.5072, -0.1276], position: "left" },
    DE: { name: "德国", city: "柏林", location: [52.52, 13.405], position: "right" },
    JP: { name: "日本", city: "东京", location: [35.6762, 139.6503], position: "right" },
  };
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const world = [[-75, -180], [85, 180]];
  const routes = new Map(), markers = new Map();
  let map, countries, selected = "all", dashboard = null;
  const el = (id) => document.getElementById(id);
  const pick = (code) => document.querySelector(`[data-market="${code}"]`)?.click();

  // Sample a spherical great-circle arc, splitting it at the date line so
  // Pacific routes do not draw an erroneous line across the entire map.
  const radians = Math.PI / 180;
  function vector([lat, lon]) {
    return [Math.cos(lat * radians) * Math.cos(lon * radians),
      Math.cos(lat * radians) * Math.sin(lon * radians), Math.sin(lat * radians)];
  }
  function geographicArc(start, end) {
    const a = vector(start), b = vector(end);
    const angle = Math.acos(Math.max(-1, Math.min(1, a.reduce((sum, v, i) => sum + v * b[i], 0))));
    const points = Array.from({ length: 81 }, (_, i) => {
      const t = i / 80, scale = Math.sin(angle);
      if (Math.abs(scale) < 1e-8) return start;
      const v = a.map((x, j) => (x * Math.sin((1 - t) * angle) + b[j] * Math.sin(t * angle)) / scale);
      return [Math.atan2(v[2], Math.hypot(v[0], v[1])) / radians, Math.atan2(v[1], v[0]) / radians];
    });
    const segments = [[points[0]]];
    for (let i = 1; i < points.length; i++) {
      const [lat, lon] = points[i], [prevLat, prevLon] = points[i - 1];
      if (Math.abs(lon - prevLon) > 180) {
        const edge = prevLon > 0 ? 180 : -180;
        const adjustedLon = lon + (edge > 0 ? 360 : -360);
        const fraction = (edge - prevLon) / (adjustedLon - prevLon);
        const edgeLat = prevLat + fraction * (lat - prevLat);
        segments.at(-1).push([edgeLat, edge]);
        segments.push([[edgeLat, -edge]]);
      }
      segments.at(-1).push(points[i]);
    }
    return segments;
  }

  function markerIcon(label, position, isOrigin = false) {
    const node = document.createElement("div");
    node.className = "map-marker" + (isOrigin ? " map-origin" : "");
    const dot = document.createElement("i");
    const text = document.createElement("span");
    text.className = `map-marker-label label-${position}`;
    text.textContent = label;
    node.append(dot, text);
    return L.divIcon({ className: "market-pin", html: node, iconSize: [20, 20], iconAnchor: [10, 10] });
  }
  function styleCountry(feature) {
    const active = selected !== "all" && feature.properties.code === selected;
    return { color: active ? "#be491f" : "#bdc8b6", weight: active ? 1.6 : 0.65,
      fillColor: active ? "#edaa79" : "#d7e0cd", fillOpacity: active ? 0.85 : 0.86 };
  }
  function updateInfo() {
    const m = markets[selected];
    el("map-market-code").textContent = m ? selected + " / " + m.city : "GLOBAL / 04";
    el("map-market-name").textContent = m ? m.name : "全部市场";
    const distance = m && map ? Math.round(map.distance(origin, m.location) / 1000) : null;
    el("map-market-distance").textContent = distance
      ? `深圳 → ${m.city} · 直线约 ${distance.toLocaleString("zh-CN")} km`
      : "四条演示航线 · 非实时物流";
    if (!dashboard) return;
    const products = dashboard.products.filter((p) => selected === "all" || p.markets.includes(selected));
    const orders = dashboard.orders.filter((o) => selected === "all" || o.country === selected);
    el("map-product-count").textContent = products.length;
    el("map-order-count").textContent = orders.length;
    el("map-pending-count").textContent = orders.filter((o) => o.status === "paid").length;
  }
  function setMarket(code) {
    if (code !== "all" && !markets[code]) return;
    selected = code;
    countries?.setStyle(styleCountry);
    for (const [market, route] of routes) {
      const active = selected === "all" || selected === market;
      route.setStyle({ opacity: active ? 0.94 : 0.14, weight: selected === market ? 2.9 : 1.7 });
      const pin = markers.get(market)?.getElement();
      pin?.classList.toggle("pin-dimmed", !active);
      pin?.classList.toggle("pin-selected", selected === market);
      pin?.setAttribute("aria-pressed", String(selected === market));
    }
    updateInfo();
  }
  function resize() {
    if (!map || !host.offsetWidth) return;
    requestAnimationFrame(() => map.invalidateSize({ pan: false }));
  }
  function reset() {
    if (!map || !host.offsetWidth) return;
    map.invalidateSize({ pan: false });
    map.fitBounds(world, { padding: [16, 10], animate: false });
  }
  window.borderMap = { setMarket, refresh(data) { dashboard = data; updateInfo(); }, resize };
  el("map-reset").addEventListener("click", reset);

  async function init() {
    if (!window.L) { status.textContent = "地图暂时无法加载，仍可使用下方市场按钮。"; return; }
    map = L.map(host, { crs: L.CRS.EPSG4326, minZoom: -1.5, maxZoom: 5,
      zoomSnap: 0.25, zoomDelta: 0.75, scrollWheelZoom: false, attributionControl: false,
      maxBounds: [[-120, -260], [120, 260]], maxBoundsViscosity: 0.85,
      zoomAnimation: !reducedMotion.matches, fadeAnimation: !reducedMotion.matches });
    map.zoomControl.setPosition("topleft");
    // Booting at a hidden deep link still gives the map an initial valid view.
    map.setView([14, 0], 0);
    reset();
    map.on("focus", () => map.scrollWheelZoom.enable());
    map.on("blur", () => map.scrollWheelZoom.disable());
    const observed = new ResizeObserver(() => {
      if (!host.offsetWidth) return;
      const wasHidden = observedWidth === 0;
      observedWidth = host.offsetWidth;
      if (wasHidden) reset();
      else resize();
    });
    let observedWidth = host.offsetWidth;
    observed.observe(host);

    // A faint geographic graticule makes the offline basemap useful at all zoom levels.
    map.createPane("map-grid-pane").style.zIndex = 210;
    for (let lon = -150; lon <= 150; lon += 30)
      L.polyline([[-90, lon], [90, lon]], { pane: "map-grid-pane", color: "#b7c9c4", weight: 0.75, opacity: 0.45, interactive: false }).addTo(map);
    for (let lat = -60; lat <= 60; lat += 30)
      L.polyline([[lat, -180], [lat, 180]], { pane: "map-grid-pane", color: "#b7c9c4", weight: 0.75, opacity: 0.45, interactive: false }).addTo(map);
    map.createPane("map-countries-pane").style.zIndex = 310;
    map.createPane("map-routes-pane").style.zIndex = 420;
    const focusMarker = (code, marker) => {
      const pin = marker.getElement();
      if (!pin) return;
      pin.setAttribute("role", "button");
      pin.setAttribute("aria-label", `选择${markets[code].name}市场`);
      pin.setAttribute("aria-pressed", "false");
      pin.addEventListener("keydown", (event) => {
        if (event.key === " ") { event.preventDefault(); pick(code); }
      });
    };
    for (const [code, m] of Object.entries(markets)) {
      const route = L.polyline(geographicArc(origin, m.location), { pane: "map-routes-pane",
        className: "market-route", color: "#d85b2d", weight: 1.7, dashArray: "5 7", lineCap: "round" }).addTo(map);
      route.on("click", () => pick(code));
      route.bindTooltip(`深圳 → ${m.city}（演示线路）`, { sticky: true, direction: "top" });
      routes.set(code, route);
      const marker = L.marker(m.location, { icon: markerIcon(m.name, m.position), title: `选择${m.name}市场`, riseOnHover: true }).addTo(map);
      marker.on("click", () => pick(code));
      markers.set(code, marker);
      focusMarker(code, marker);
    }
    const home = L.marker(origin, { icon: markerIcon("深圳", "bottom", true), title: "深圳 · 出发地", keyboard: false }).addTo(map);
    home.bindTooltip("深圳 · 跨境网络出发地", { direction: "top" });
    setMarket(selected);
    try {
      const response = await fetch("/static/maps/world.geojson?v=20261126-3");
      if (!response.ok) throw new Error("Basemap unavailable");
      const geography = await response.json();
      countries = L.geoJSON(geography, {
        pane: "map-countries-pane", style: styleCountry,
        onEachFeature(feature, layer) {
          const { code, name } = feature.properties;
          const label = document.createElement("span");
          label.textContent = name + (markets[code] ? " · 点击选择市场" : "");
          layer.bindTooltip(label, { sticky: true, direction: "top" });
          layer.on("mouseover", () => layer.setStyle({ fillOpacity: 1, color: "#849b86", weight: 1 }));
          layer.on("mouseout", () => countries?.resetStyle(layer));
          if (markets[code]) layer.on("click", () => pick(code));
        },
      }).addTo(map);
      status.hidden = true;
      host.dataset.ready = "true";
      setMarket(selected);
    } catch {
      status.textContent = "地理底图加载失败，可继续选择市场和航线。";
    }
  }
  // The main app resolves the initial tab before initializing the map.
  document.addEventListener("DOMContentLoaded", init);
})();
