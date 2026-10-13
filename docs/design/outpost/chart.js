/* TimeChart: stacked panels on one time axis (never two y-scales on one panel).
   Hover or touch reads every series at that moment; wheel, pinch or +/- zooms; drag or arrows pan;
   double-click or 0 resets. Event spans shade every panel; markers (trader entries) carry their own tooltip. */
class TimeChart {
  constructor(el, cfg) {
    this.el = el; this.cfg = cfg;
    this.cv = document.createElement("canvas");
    el.appendChild(this.cv);
    el.tabIndex = 0;
    el.setAttribute("role", "img");
    el.setAttribute("aria-label", cfg.label || "Chart");
    this.g = this.cv.getContext("2d");
    const ts = cfg.panels.flatMap((p) => p.series.flatMap((s) => [s.t[0], s.t[s.t.length - 1]]));
    this.ext = [Math.min(...ts), Math.max(...ts)];
    this.setRange(cfg.initial || this.ext[1] - this.ext[0], true);
    this.hover = null; this.ptrs = new Map();
    this.bind();
    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(el);
  }
  css(v) { return v && v.startsWith("--") ? getComputedStyle(document.documentElement).getPropertyValue(v).trim() : v; }
  setRange(span, init) {
    const end = this.ext[1];
    this.view = [Math.max(this.ext[0], end - span), end];
    if (init) this.initial = this.view.slice();
    this.draw();
  }
  reset() { this.view = this.initial.slice(); this.draw(); }
  clampView(a, b) {
    const minSpan = this.cfg.minSpan || 3 * 3600, full = this.ext[1] - this.ext[0];
    let span = Math.min(full, Math.max(minSpan, b - a));
    a = Math.max(this.ext[0], Math.min(a, this.ext[1] - span));
    this.view = [a, a + span];
  }
  zoomAt(t, f) { const [a, b] = this.view; this.clampView(t - (t - a) * f, t + (b - t) * f); this.draw(); }
  pan(dt) { const [a, b] = this.view; this.clampView(a + dt, b + dt); this.draw(); }
  geo() {
    const w = this.el.clientWidth, h = this.el.clientHeight, axR = this.cfg.axisWidth || 76, axB = 24, gap = 22, top = (this.cfg.events || []).length ? 36 : 18;
    const totalW = this.cfg.panels.reduce((s, p) => s + (p.weight || 1), 0), usable = h - axB - top - gap * (this.cfg.panels.length - 1);
    let y = top;
    const panels = this.cfg.panels.map((p) => { const ph = usable * (p.weight || 1) / totalW, r = { y0: y, y1: y + ph }; y += ph + gap; return r; });
    return { w, h, x0: 2, x1: w - axR, panels, axB };
  }
  tx(t, G) { const [a, b] = this.view; return G.x0 + (t - a) / (b - a) * (G.x1 - G.x0); }
  ti(x, G) { const [a, b] = this.view; return a + (x - G.x0) / (G.x1 - G.x0) * (b - a); }
  static idx(arr, t) { let lo = 0, hi = arr.length - 1; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < t) lo = m + 1; else hi = m; } return lo; }
  yRange(p) {
    if (p.fixed) return p.fixed;
    let lo = Infinity, hi = -Infinity;
    const [a, b] = this.view;
    for (const s of p.series) {
      if (s.hidden) continue;
      const i0 = Math.max(0, TimeChart.idx(s.t, a) - 1), i1 = Math.min(s.t.length - 1, TimeChart.idx(s.t, b) + 1);
      for (let i = i0; i <= i1; i++) { const v = s.v[i]; if (v == null) continue; if (v < lo) lo = v; if (v > hi) hi = v; }
    }
    for (const m of this.cfg.markers || []) if (m.panel === this.cfg.panels.indexOf(p) && m.t >= a && m.t <= b) { lo = Math.min(lo, m.v); hi = Math.max(hi, m.v); }
    for (const r of p.refs || []) if (r.keep) { lo = Math.min(lo, r.y); hi = Math.max(hi, r.y); }
    if (p.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    if (!isFinite(lo)) return [0, 1];
    const pad = (hi - lo || Math.abs(hi) || 1) * 0.1;
    return [lo - pad, hi + pad];
  }
  static nice(lo, hi, n = 4) {
    const raw = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
    const step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
    const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toPrecision(12));
    return out;
  }
  draw() {
    const G = this.geo(), dpr = window.devicePixelRatio || 1;
    if (!G.w || !G.h) return;
    if (this.cv.width !== Math.round(G.w * dpr) || this.cv.height !== Math.round(G.h * dpr)) { this.cv.width = Math.round(G.w * dpr); this.cv.height = Math.round(G.h * dpr); }
    const g = this.g; g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, G.w, G.h);
    const ink2 = this.css("--ink-2"), ink3 = this.css("--ink-3"), grid = this.css("--grid"), surf = this.css("--win"), acc = this.css("--accent");
    const mono = getComputedStyle(document.documentElement).getPropertyValue("--mono"), body = getComputedStyle(document.documentElement).getPropertyValue("--body");
    this.G = G; this.yr = [];
    const [a, b] = this.view;
    // event spans across every panel
    for (const e of this.cfg.events || []) {
      if (e.t1 < a || e.t0 > b) continue;
      const xa = Math.max(G.x0, this.tx(e.t0, G)), xb = Math.min(G.x1, this.tx(e.t1, G));
      g.fillStyle = this.css(e.color || "--accent"); g.globalAlpha = 0.13;
      g.fillRect(xa, G.panels[0].y0, Math.max(2, xb - xa), G.panels[G.panels.length - 1].y1 - G.panels[0].y0);
      g.fillRect(xa, 2, Math.max(2, xb - xa), 14);
      g.globalAlpha = 1; g.fillStyle = ink2; g.font = `12px ${body}`; g.textBaseline = "top";
      g.fillText(e.label, Math.max(G.x0, Math.min(xa + 4, G.x1 - g.measureText(e.label).width - 2)), 2);
    }
    this.cfg.panels.forEach((p, pi) => {
      const R = G.panels[pi], yr = this.yRange(p); this.yr[pi] = yr;
      const ty = (v) => R.y1 - (v - yr[0]) / (yr[1] - yr[0]) * (R.y1 - R.y0);
      // y grid + right axis
      g.font = `12px ${mono}`; g.textBaseline = "middle";
      for (const v of TimeChart.nice(yr[0], yr[1], p.ticks || 4)) {
        const y = Math.round(ty(v)) + 0.5;
        if (y < R.y0 - 1 || y > R.y1 + 1) continue;
        g.strokeStyle = grid; g.lineWidth = 1; g.beginPath(); g.moveTo(G.x0, y); g.lineTo(G.x1, y); g.stroke();
        g.fillStyle = ink3; g.fillText((p.yfmt || String)(v), G.x1 + 8, y);
      }
      // y bands and reference lines
      for (const bd of p.bands || []) {
        const ya = ty(Math.min(bd.y1, yr[1])), yb = ty(Math.max(bd.y0, yr[0]));
        if (yb <= ya) continue;
        g.fillStyle = this.css(bd.color); g.globalAlpha = 0.1; g.fillRect(G.x0, ya, G.x1 - G.x0, yb - ya); g.globalAlpha = 1;
        g.fillStyle = ink2; g.font = `12px ${body}`; g.textBaseline = "top"; g.fillText(bd.label, G.x0 + 6, ya + 4);
      }
      for (const r of p.refs || []) {
        if (r.y < yr[0] || r.y > yr[1]) continue;
        const y = Math.round(ty(r.y)) + 0.5;
        g.strokeStyle = this.css(r.color || "--ink-3"); g.setLineDash([4, 4]); g.lineWidth = 1;
        g.beginPath(); g.moveTo(G.x0, y); g.lineTo(G.x1, y); g.stroke(); g.setLineDash([]);
        if (r.label) { g.fillStyle = ink2; g.font = `12px ${body}`; g.textBaseline = "bottom"; g.fillText(r.label, G.x0 + 6, y - 3); }
      }
      // panel title
      g.fillStyle = ink2; g.font = `12.5px ${body}`; g.textBaseline = "top";
      if (p.title) g.fillText(p.title, G.x0 + 2, R.y0 - 16 + (pi === 0 && (this.cfg.events || []).some((e) => e.t1 >= a && e.t0 <= b) ? -0 : 0));
      // series
      g.save(); g.beginPath(); g.rect(G.x0, R.y0 - 2, G.x1 - G.x0, R.y1 - R.y0 + 4); g.clip();
      for (const s of p.series) {
        if (s.hidden) continue;
        const col = this.css(s.color), i0 = Math.max(0, TimeChart.idx(s.t, a) - 1), i1 = Math.min(s.t.length - 1, TimeChart.idx(s.t, b) + 1);
        const pts = []; for (let i = i0; i <= i1; i++) if (s.v[i] != null) pts.push([this.tx(s.t[i], G), ty(s.v[i])]);
        if (pts.length < 2) continue;
        if (s.kind === "area" || s.kind === "bars") {
          if (s.kind === "bars") {
            const zero = ty(Math.max(yr[0], Math.min(yr[1], 0))), bw = Math.max(1, (G.x1 - G.x0) / (i1 - i0 + 1) - 1);
            g.fillStyle = col;
            for (let i = i0; i <= i1; i++) { if (s.v[i] == null) continue; const x = this.tx(s.t[i], G), y = ty(s.v[i]); g.fillStyle = this.css(s.v[i] >= 0 ? s.color : s.negColor || s.color); g.fillRect(x - bw / 2, Math.min(y, zero), bw, Math.abs(zero - y)); }
            continue;
          }
          const grad = g.createLinearGradient(0, R.y0, 0, R.y1); grad.addColorStop(0, col); grad.addColorStop(1, "transparent");
          g.globalAlpha = 0.16; g.fillStyle = grad; g.beginPath(); g.moveTo(pts[0][0], R.y1);
          pts.forEach(([x, y]) => g.lineTo(x, y)); g.lineTo(pts[pts.length - 1][0], R.y1); g.closePath(); g.fill(); g.globalAlpha = 1;
        }
        g.strokeStyle = col; g.lineWidth = s.width || 2; g.lineJoin = "round"; g.lineCap = "round";
        if (s.kind === "dash") g.setLineDash([5, 4]);
        g.beginPath(); pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.stroke(); g.setLineDash([]);
      }
      // markers (entries)
      for (const m of this.cfg.markers || []) {
        if (m.panel !== pi || m.t < a || m.t > b) continue;
        const x = this.tx(m.t, G), y = ty(m.v);
        g.fillStyle = surf; g.beginPath(); g.arc(x, y, m.r + 2, 0, Math.PI * 2); g.fill();
        g.fillStyle = this.css(m.color); g.beginPath(); g.arc(x, y, m.r, 0, Math.PI * 2); g.fill();
      }
      g.restore();
      // end value tag
      const s0 = p.series.find((s) => !s.hidden && s.tag !== false);
      if (s0) {
        const i = Math.min(s0.t.length - 1, TimeChart.idx(s0.t, b)), v = s0.v[i];
        if (v != null && s0.t[i] <= b + 3600) {
          const y = ty(v), txt = (s0.fmt || String)(v);
          g.font = `600 12px ${mono}`; const tw = g.measureText(txt).width + 10;
          g.fillStyle = this.css(s0.color); g.beginPath(); g.roundRect ? g.roundRect(G.x1 + 3, y - 10, tw, 20, 4) : g.rect(G.x1 + 3, y - 10, tw, 20); g.fill();
          g.fillStyle = TimeChart.onColor(this.css(s0.color)); g.textBaseline = "middle"; g.fillText(txt, G.x1 + 8, y + 0.5);
          g.fillStyle = this.css(s0.color); g.beginPath(); g.arc(this.tx(s0.t[i], G), y, 4, 0, Math.PI * 2); g.fill();
        }
      }
    });
    // x axis
    const span = b - a, steps = [3600, 7200, 10800, 21600, 43200, 86400, 172800, 345600, 604800];
    const step = steps.find((s) => span / s <= Math.max(3, G.w / 90)) || 604800;
    g.fillStyle = ink3; g.font = `12px ${mono}`; g.textBaseline = "top";
    const yb = G.panels[G.panels.length - 1].y1 + 7;
    let last = -1e9;
    for (let t = Math.ceil(a / step) * step; t <= b; t += step) {
      const x = this.tx(t, G), d = new Date(t * 1000), lab = step < 86400 && d.getUTCHours() !== 0
        ? String(d.getUTCHours()).padStart(2, "0") + ":00"
        : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
      const tw = g.measureText(lab).width;
      if (x - tw / 2 < last + 12 || x + tw / 2 > G.x1) continue;
      g.fillText(lab, x - tw / 2, yb); last = x + tw / 2;
    }
    // crosshair
    if (this.hover != null) {
      const x = this.tx(this.hover, G);
      g.strokeStyle = ink2; g.lineWidth = 1; g.beginPath(); g.moveTo(Math.round(x) + 0.5, G.panels[0].y0 - 4); g.lineTo(Math.round(x) + 0.5, G.panels[G.panels.length - 1].y1); g.stroke();
      this.cfg.panels.forEach((p, pi) => {
        const R = G.panels[pi], yr = this.yr[pi], ty = (v) => R.y1 - (v - yr[0]) / (yr[1] - yr[0]) * (R.y1 - R.y0);
        for (const s of p.series) {
          if (s.hidden) continue;
          const i = TimeChart.idx(s.t, this.hover), v = s.v[i]; if (v == null) continue;
          g.fillStyle = surf; g.beginPath(); g.arc(this.tx(s.t[i], G), ty(v), 6, 0, Math.PI * 2); g.fill();
          g.fillStyle = this.css(s.color); g.beginPath(); g.arc(this.tx(s.t[i], G), ty(v), 4, 0, Math.PI * 2); g.fill();
        }
      });
    }
    void acc;
  }
  static onColor(c) {
    const m = c.match(/#([0-9a-f]{6})/i); if (!m) return "#fff";
    const n = parseInt(m[1], 16), l = (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
    return l > 0.6 ? "#0a0b22" : "#ffffff";
  }
  markerAt(x, y) {
    const G = this.G; if (!G) return null;
    let best = null, bd = 14;
    for (const m of this.cfg.markers || []) {
      const R = G.panels[m.panel], yr = this.yr[m.panel];
      if (!R || !yr || m.t < this.view[0] || m.t > this.view[1]) continue;
      const mx = this.tx(m.t, G), my = R.y1 - (m.v - yr[0]) / (yr[1] - yr[0]) * (R.y1 - R.y0);
      const d = Math.hypot(mx - x, my - y);
      if (d < Math.max(bd, m.r + 6)) { bd = d; best = m; }
    }
    return best;
  }
  readout(t) {
    const d = new Date(t * 1000);
    const when = d.toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC";
    let h = `<div class="lab" style="margin-bottom:4px">${when}</div>`;
    for (const p of this.cfg.panels) for (const s of p.series) {
      if (s.hidden) continue;
      const i = TimeChart.idx(s.t, t), v = s.v[i]; if (v == null) continue;
      h += `<div><span class="k" style="border-color:${this.css(s.color)}${s.kind === "dash" ? ";border-top-style:dashed" : ""}"></span><span class="v">${(s.fmt || String)(v)}</span> <span class="lab">${s.label}</span></div>`;
    }
    for (const e of this.cfg.events || []) if (t >= e.t0 && t <= e.t1) h += `<div class="lab" style="margin-top:4px">${e.label}</div>`;
    return h;
  }
  bind() {
    const el = this.el;
    const local = (e) => { const r = el.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    const hoverAt = (e) => {
      const [x, y] = local(e), G = this.G; if (!G) return;
      const m = this.markerAt(x, y);
      if (m) { this.hover = null; this.draw(); Tip.show(m.html, e.clientX, e.clientY); el.style.cursor = "pointer"; return; }
      el.style.cursor = "";
      if (x < G.x0 || x > G.x1) { this.leave(); return; }
      const s = this.cfg.panels[0].series.find((q) => !q.hidden), t = this.ti(x, G);
      const i = TimeChart.idx(s.t, t), tt = i > 0 && Math.abs(s.t[i - 1] - t) < Math.abs(s.t[i] - t) ? s.t[i - 1] : s.t[i];
      this.hover = tt; this.draw();
      Tip.show(this.readout(tt), e.clientX, e.clientY);
      this.cfg.onHover && this.cfg.onHover(tt);
    };
    el.addEventListener("pointerdown", (e) => {
      el.setPointerCapture(e.pointerId);
      this.ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY, sx: e.clientX, view: this.view.slice() });
      if (this.ptrs.size === 2) { const [p1, p2] = [...this.ptrs.values()]; this.pinch = { d: Math.abs(p1.x - p2.x) || 1, view: this.view.slice() }; }
      if (e.pointerType !== "mouse") hoverAt(e);
    });
    el.addEventListener("pointermove", (e) => {
      const p = this.ptrs.get(e.pointerId);
      if (p) {
        p.x = e.clientX; p.y = e.clientY;
        if (this.ptrs.size === 2 && this.pinch) {
          const [p1, p2] = [...this.ptrs.values()], d = Math.abs(p1.x - p2.x) || 1, f = this.pinch.d / d;
          const r = el.getBoundingClientRect(), mid = this.ti((p1.x + p2.x) / 2 - r.left, this.G);
          const [a, b] = this.pinch.view; this.clampView(mid - (mid - a) * f, mid + (b - mid) * f); this.draw(); return;
        }
        if (e.pointerType === "mouse" || Math.abs(e.clientX - p.sx) > 8) {
          const G = this.G, dt = (p.sx - e.clientX) / (G.x1 - G.x0) * (p.view[1] - p.view[0]);
          if (Math.abs(e.clientX - p.sx) > 3) { this.clampView(p.view[0] + dt, p.view[1] + dt); this.dragged = true; Tip.hide(); this.hover = null; this.draw(); el.style.cursor = "grabbing"; return; }
        }
      }
      hoverAt(e);
    });
    const up = (e) => {
      const p = this.ptrs.get(e.pointerId); this.ptrs.delete(e.pointerId); if (this.ptrs.size < 2) this.pinch = null;
      el.style.cursor = "";
      if (p && !this.dragged) { const [x, y] = local(e), m = this.markerAt(x, y); if (m && m.onClick) m.onClick(); }
      this.dragged = false;
      if (e.pointerType !== "mouse") { this.leave(); }
    };
    el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up);
    el.addEventListener("pointerleave", () => { if (!this.ptrs.size) this.leave(); });
    el.addEventListener("wheel", (e) => {
      e.preventDefault();
      const [x] = local(e);
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) { this.pan(e.deltaX / (this.G.x1 - this.G.x0) * (this.view[1] - this.view[0])); return; }
      this.zoomAt(this.ti(x, this.G), Math.exp(e.deltaY * 0.0016)); hoverAt(e);
    }, { passive: false });
    el.addEventListener("dblclick", () => this.reset());
    el.addEventListener("keydown", (e) => {
      const span = this.view[1] - this.view[0], mid = (this.view[0] + this.view[1]) / 2;
      if (e.key === "ArrowLeft") this.pan(-span * 0.15);
      else if (e.key === "ArrowRight") this.pan(span * 0.15);
      else if (e.key === "+" || e.key === "=") this.zoomAt(mid, 0.8);
      else if (e.key === "-") this.zoomAt(mid, 1.25);
      else if (e.key === "0") this.reset();
      else return;
      e.preventDefault();
    });
  }
  leave() { this.hover = null; Tip.hide(); this.draw(); this.cfg.onHover && this.cfg.onHover(null); }
  destroy() { this.ro.disconnect(); }
}

/* a small inline SVG sparkline (one series, its own scale), for dossiers and widgets */
function spark(values, w, h, color, opts = {}) {
  const v = values.filter((x) => x != null);
  if (v.length < 2) return "";
  let lo = Math.min(...v), hi = Math.max(...v);
  if (opts.ref != null) { lo = Math.min(lo, opts.ref); hi = Math.max(hi, opts.ref); }
  const pad = (hi - lo) * 0.12 || 1, y = (x) => h - 2 - (x - lo + pad) / (hi - lo + 2 * pad) * (h - 4);
  const step = w / (values.length - 1);
  const pts = values.map((x, i) => (x == null ? null : `${(i * step).toFixed(1)},${y(x).toFixed(1)}`)).filter(Boolean);
  const ref = opts.ref != null ? `<line x1="0" x2="${w}" y1="${y(opts.ref).toFixed(1)}" y2="${y(opts.ref).toFixed(1)}" stroke="var(--ink-3)" stroke-dasharray="3 3" stroke-width="1" vector-effect="non-scaling-stroke"/>` : "";
  const last = pts[pts.length - 1].split(",");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">${ref}<polyline fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" vector-effect="non-scaling-stroke" points="${pts.join(" ")}"/><circle cx="${last[0]}" cy="${last[1]}" r="2.6" fill="${color}"/></svg>`;
}
