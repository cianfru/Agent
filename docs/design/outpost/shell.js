/* The Outpost shell: storage, tooltips, the landscape, menu bar, dock, windows and the desktop. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const phone = () => innerWidth <= 760;

const Store = {
  get(k, d) { try { const v = localStorage.getItem("outpost:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("outpost:" + k, JSON.stringify(v)); } catch { /* storage off: keep going */ } },
  del(k) { try { localStorage.removeItem("outpost:" + k); } catch { /* ignore */ } },
};

const Tip = {
  el: null, pinned: false,
  show(html, x, y) {
    const t = this.el; t.innerHTML = html; t.classList.add("on");
    const r = t.getBoundingClientRect(), pad = 14;
    let lx = x + pad, ly = y + pad;
    if (lx + r.width > innerWidth - 8) lx = x - r.width - pad;
    if (ly + r.height > innerHeight - 8) ly = y - r.height - pad;
    t.style.left = Math.max(8, lx) + "px"; t.style.top = Math.max(8, ly) + "px";
  },
  hide() { this.el.classList.remove("on"); },
};
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("on"), 2200);
}
function info(text) { return `<button type="button" class="info" data-tip="${esc(text)}" aria-label="${esc(text)}">i</button>`; }

/* ---------------- looks ---------------- */
const Look = {
  state: Store.get("look", "auto"),
  name() {
    if (this.state !== "auto") return this.state;
    const th = document.documentElement.getAttribute("data-theme");
    if (th) return th === "light" ? "dawn" : "night";
    return matchMedia("(prefers-color-scheme: light)").matches ? "dawn" : "night";
  },
  set(v) {
    this.state = v; Store.set("look", v);
    if (v === "auto") document.documentElement.removeAttribute("data-look"); else document.documentElement.setAttribute("data-look", v);
    Look.changed();
  },
  changed() { World.restyle(); Charts.redraw(); Menubar.refresh(); if (!$("#wall").hidden) Wall.render(); },
};
const Charts = { live: new Set(), add(c) { this.live.add(c); return c; }, redraw() { this.live.forEach((c) => c.draw()); } };

/* ---------------- the landscape ---------------- */
const Wall = {
  riderX: 0.64, riding: null,
  render(riderAt) {
    const cv = $("#wall"), dpr = Math.min(2, devicePixelRatio || 1), w = innerWidth, h = innerHeight;
    const H = 200, W = Math.max(240, Math.min(620, Math.round(H * w / h)));
    const s = Art.scene(W, H, Look.name(), Herd.now());
    const [off, og] = Art.mk(W, H);
    og.putImageData(s.img, 0, 0);
    const rx = Math.round(W * (riderAt == null ? this.riderX : riderAt)), ry = s.ridge[Math.min(W - 1, rx + 10)] - Art.RIDER_H + 1;
    Art.drawRider(og, rx, ry, this.frame || 0, s.riderColor, "#e0404e");
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    const g = cv.getContext("2d"); g.imageSmoothingEnabled = false;
    const k = Math.max(w * dpr / W, h * dpr / H), dw = W * k, dh = H * k;
    const ox = (w * dpr - dw) / 2, oy = h * dpr - dh;
    g.drawImage(off, ox, oy, dw, dh);
    this.off = off; this.dx = ox; this.dy = oy; this.dw = dw; this.dh = dh;
    const hit = $("#rider-hit"), sc = k / dpr;
    hit.style.left = (ox / dpr + (rx - 2) * sc) + "px"; hit.style.top = (oy / dpr + (ry - 2) * sc) + "px";
    hit.style.width = (24 * sc) + "px"; hit.style.height = (Art.RIDER_H + 4) * sc + "px";
    this.k = sc; this.ox = ox / dpr; this.oy = oy / dpr; this.rx = rx; this.ry = ry;
  },
  dissolve(done) {
    const cv = $("#wall");
    if (reduced() || !this.off) { cv.hidden = true; done(); return; }
    const W = this.off.width, H = this.off.height, src = this.off.getContext("2d").getImageData(0, 0, W, H);
    const b8 = (() => { let m = [[0]]; for (let n = 1; n < 8; n *= 2) m = [...m.map((r) => [...r.map((v) => 4 * v), ...r.map((v) => 4 * v + 2)]), ...m.map((r) => [...r.map((v) => 4 * v + 3), ...r.map((v) => 4 * v + 1)])]; return m.map((r) => r.map((v) => (v + 0.5) / 64)); })();
    const [tmp, tg] = Art.mk(W, H), g = cv.getContext("2d"), t0 = performance.now(), dur = 900;
    const step = (now) => {
      const p = Math.min(1, (now - t0) / dur), img = new ImageData(new Uint8ClampedArray(src.data), W, H);
      for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (b8[y & 7][x & 7] < p) img.data[(y * W + x) * 4 + 3] = 0;
      tg.putImageData(img, 0, 0); g.imageSmoothingEnabled = false;
      g.clearRect(0, 0, cv.width, cv.height); g.drawImage(tmp, this.dx, this.dy, this.dw, this.dh);
      if (p < 1) requestAnimationFrame(step); else { cv.hidden = true; done(); }
    };
    requestAnimationFrame(step);
  },
  riderRect() { const h = $("#rider-hit").getBoundingClientRect(); return h; },
  rideIn(done) {
    if (reduced()) { this.render(); done(); return; }
    const t0 = performance.now(), dur = 1500, from = 1.04, to = this.riderX;
    const step = (now) => {
      const u = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - u, 2.2);
      this.frame = Math.floor(now / 130) % 2;
      this.render(from + (to - from) * e);
      if (u < 1) requestAnimationFrame(step); else { this.frame = 0; this.render(); done(); }
    };
    requestAnimationFrame(step);
  },
};

/* ---------------- menus ---------------- */
const Menu = {
  open: null,
  show(btn, items) {
    this.close();
    const m = document.createElement("div");
    m.className = "menu"; m.setAttribute("role", "menu");
    m.innerHTML = items.map((it) => {
      if (it === "-") return "<hr>";
      if (it.head) return `<div class="head">${esc(it.head)}</div>`;
      const role = it.radio ? "menuitemradio" : "menuitem";
      return `<button type="button" role="${role}" ${it.radio ? `aria-checked="${!!it.on}"` : ""} data-i="${items.indexOf(it)}"><span class="tick">${it.on ? "✓" : ""}</span>${esc(it.label)}${it.hint ? `<span class="hint">${esc(it.hint)}</span>` : ""}</button>`;
    }).join("");
    document.body.appendChild(m);
    const r = btn.getBoundingClientRect();
    m.style.left = Math.max(6, Math.min(r.left, innerWidth - m.offsetWidth - 6)) + "px";
    m.style.top = r.bottom + 4 + "px";
    btn.setAttribute("aria-expanded", "true");
    m.addEventListener("click", (e) => {
      const b = e.target.closest("[data-i]"); if (!b) return;
      const it = items[+b.dataset.i]; this.close(); it.run && it.run();
    });
    m.addEventListener("keydown", (e) => {
      const all = $$("[role^=menuitem]", m), i = all.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); all[(i + 1) % all.length].focus(); }
      if (e.key === "ArrowUp") { e.preventDefault(); all[(i - 1 + all.length) % all.length].focus(); }
      if (e.key === "Escape") { this.close(); btn.focus(); }
    });
    this.open = { m, btn };
    $("[role^=menuitem]", m)?.focus({ preventScroll: true });
  },
  close() { if (!this.open) return; this.open.m.remove(); this.open.btn.setAttribute("aria-expanded", "false"); this.open = null; },
};
document.addEventListener("pointerdown", (e) => { if (Menu.open && !e.target.closest(".menu") && e.target !== Menu.open.btn && !Menu.open.btn.contains(e.target)) Menu.close(); });

const Menubar = {
  build() {
    const mb = $("#menubar");
    mb.innerHTML = `
      <button type="button" class="mb-btn mb-logo" id="mb-logo" aria-haspopup="menu" aria-expanded="false" aria-label="Outpost menu"><img src="${Art.face()}" alt=""></button>
      <button type="button" class="mb-btn mb-app" id="mb-app">Outpost</button>
      <button type="button" class="mb-btn mb-hide-s" id="mb-win" aria-haspopup="menu" aria-expanded="false">Window</button>
      <span class="mb-spacer"></span>
      <button type="button" class="mb-btn mb-stat mb-hide-s" id="mb-herd"></button>
      <span class="mb-stat mb-hide-s" id="mb-btc"></span>
      <span class="mb-stat mb-scout mb-hide-s" id="mb-scout"></span>
      <span class="mb-proto mb-hide-s" tabindex="0" data-tip="Prototype. Prices and funding are real Hyperliquid data from 13 October 2026. Positions, traders, groups, open interest history and the record are sample data.">Sample positions</span>
      <button type="button" class="mb-btn mb-wallet" id="mb-wallet"></button>
      <span class="mb-stat mono" id="mb-clock"></span>`;
    $("#mb-logo").onclick = (e) => Menu.show(e.currentTarget, [
      { label: "Ride along with Outrider", run: () => Tour.start() },
      { label: "About Outpost", run: () => WM.open("about") }, "-",
      { head: "Look" },
      ...[["auto", "Match my system"], ["night", "Night ride"], ["dawn", "Dawn"], ["vga", "Mode 13h"]].map(([v, l]) => ({ label: l, radio: true, on: Look.state === v, run: () => Look.set(v) })), "-",
      { head: "Outrider's voice" },
      { label: "In character", radio: true, on: App.voice === "char", run: () => App.setVoice("char") },
      { label: "Plain", radio: true, on: App.voice === "plain", run: () => App.setVoice("plain") }, "-",
      { head: "Preview as" },
      { label: "Token holder", radio: true, on: App.view === "holder", run: () => App.setView("holder") },
      { label: "Visitor", radio: true, on: App.view === "visitor", run: () => App.setView("visitor") }, "-",
      { label: "Tidy the desktop", run: () => Desk.tidy() },
      { label: "Reset the desktop", run: () => Desk.reset() },
    ]);
    $("#mb-win").onclick = (e) => Menu.show(e.currentTarget, [
      ...[...WM.wins.values()].map((w) => ({ label: w.title, on: w === WM.top(), run: () => WM.focus(w.key, true) })),
      ...(WM.wins.size ? ["-", { label: "Close all", run: () => [...WM.wins.keys()].forEach((k) => WM.close(k)) }] : [{ label: "No windows open" }]),
    ]);
    $("#mb-app").onclick = () => { const w = WM.top(); if (w) WM.focus(w.key, true); else WM.open("dispatch"); };
    $("#mb-herd").onclick = () => WM.open("herd");
    $("#mb-wallet").onclick = () => App.view === "visitor" ? WM.open("unlock") : toast("Signed in with 0x28fb…4341 · 12,400 RIDE");
    setInterval(() => this.clock(), 15000); this.clock();
    this.refresh();
  },
  clock() { const d = new Date(); $("#mb-clock").textContent = String(d.getUTCHours()).padStart(2, "0") + ":" + String(d.getUTCMinutes()).padStart(2, "0") + " UTC"; },
  refresh() {
    const h = Herd.now(), d = h - Herd.at(D.asof - 86400);
    $("#mb-herd").innerHTML = App.view === "visitor" ? `<small>Herd</small><b>locked</b>` : `<small>Herd</small><b>${h.toFixed(0)}% long</b><small>${d >= 0 ? "▲" : "▼"}${Math.abs(d).toFixed(0)}</small>`;
    const b = D.prices.BTC, n = b.length - 1, c = (b[n] / b[n - 24] - 1) * 100;
    $("#mb-btc").innerHTML = `<small>BTC</small><b>${fmtPx(b[n])}</b><small>${pct(c)}</small>`;
    $("#mb-scout").innerHTML = `<img src="${Art.face()}" alt=""><small>Outrider checked ${hhmm(D.asof)} UTC</small>`;
    $("#mb-wallet").textContent = App.view === "visitor" ? "Connect wallet" : "0x28fb…4341";
    const top = WM.top(); $("#mb-app").textContent = top ? APPS[top.app].name : "Outpost";
  },
};

/* ---------------- dock ---------------- */
const DOCK = ["dispatch", "herd", "leverage", "map", "traders", "groups", "record", "lab"];
const Dock = {
  build() {
    const dk = $("#dock");
    dk.innerHTML = DOCK.map((a) => `<button type="button" class="dk" data-app="${a}" aria-label="${APPS[a].name}"><img src="${Art.icon(APPS[a].icon)}" alt=""><span class="lbl">${APPS[a].name}</span><span class="run"></span></button>`).join("")
      + `<span class="dk-sep"></span><button type="button" class="dk trash" data-app="trash" aria-label="Trash: drag a desktop item here to remove it"><img src="${Art.icon("trash")}" alt=""><span class="lbl">Trash</span></button>`;
    dk.addEventListener("click", (e) => {
      const b = e.target.closest(".dk"); if (!b) return;
      if (b.dataset.app === "trash") { toast("Drag a desktop item onto the barrel to remove it"); return; }
      WM.open(b.dataset.app, null, b);
    });
    // magnification: each icon grows with the pointer's distance to its centre (macOS style)
    const icons = () => $$(".dk", dk).filter((b) => getComputedStyle(b).display !== "none");
    let px = null, raf = 0, sizes = new Map();
    const BASE = 52, MAX = 92, RANGE = 160;
    const tick = () => {
      raf = 0; let moving = false;
      for (const b of icons()) {
        const r = b.getBoundingClientRect(), cx = r.left + r.width / 2;
        const tgt = px == null || phone() ? BASE : BASE + (MAX - BASE) * Math.max(0, Math.cos(Math.min(1, Math.abs(px - cx) / RANGE) * Math.PI / 2));
        const cur = sizes.get(b) ?? BASE, nxt = reduced() ? tgt : cur + (tgt - cur) * 0.35;
        sizes.set(b, nxt); b.style.width = b.style.height = nxt.toFixed(2) + "px";
        if (Math.abs(nxt - tgt) > 0.3) moving = true;
      }
      if (moving) raf = requestAnimationFrame(tick);
    };
    dk.addEventListener("pointermove", (e) => { if (e.pointerType !== "mouse") return; px = e.clientX; if (!raf) raf = requestAnimationFrame(tick); });
    dk.addEventListener("pointerleave", () => { px = null; if (!raf) raf = requestAnimationFrame(tick); });
    this.refresh();
  },
  refresh() {
    $$("#dock .dk[data-app]").forEach((b) => {
      const a = b.dataset.app; if (a === "trash") return;
      b.classList.toggle("open", [...WM.wins.values()].some((w) => w.app === a || (a === "map" && w.app === "coin") || (a === "traders" && w.app === "trader")));
      const locked = App.view === "visitor" && !APPS[a].public;
      b.classList.toggle("locked", locked);
      let lk = $(".lock", b);
      if (locked && !lk) { lk = document.createElement("img"); lk.className = "lock"; lk.src = Art.lock(); lk.alt = ""; b.appendChild(lk); }
      if (!locked && lk) lk.remove();
      b.setAttribute("aria-label", APPS[a].name + (locked ? " (holders only)" : ""));
    });
  },
  bounce(app) { if (reduced()) return; const b = $(`#dock .dk[data-app="${app}"]`); if (!b) return; b.classList.remove("bounce"); void b.offsetWidth; b.classList.add("bounce"); setTimeout(() => b.classList.remove("bounce"), 750); },
  rect(app) { const b = $(`#dock .dk[data-app="${app}"]`); return b && getComputedStyle(b).display !== "none" ? b.getBoundingClientRect() : null; },
  trashRect() { return $("#dock .trash").getBoundingClientRect(); },
};

/* ---------------- windows ---------------- */
const WM = {
  wins: new Map(), z: 20, n: 0,
  area() { return $("#wins").getBoundingClientRect(); },
  top() { let t = null; for (const w of this.wins.values()) if (!w.min && (!t || w.z > t.z)) t = w; return t; },
  open(app, arg, from) {
    const spec = APPS[app]; if (!spec) return;
    const key = app + (arg ? ":" + arg : "");
    const ex = this.wins.get(key);
    if (ex) { if (ex.min) this.restore(key); this.focus(key, true); return ex; }
    const A = this.area(), saved = Store.get("win:" + key);
    const w0 = Math.min(spec.w || 640, A.width - 24), h0 = Math.min(spec.h || 560, A.height - 96);
    const cascade = (this.n++ % 6) * 28;
    const geo = saved && !phone() ? { ...saved, w: Math.min(saved.w, A.width - 12), h: Math.min(saved.h, A.height - 12) }
      : { x: Math.max(12, Math.round((A.width - w0) / 2 - 140 + cascade + (spec.dx || 0))), y: Math.max(10, 24 + cascade + (spec.dy || 0)), w: w0, h: h0 };
    if (spec.left && !saved) geo.x = Math.max(geo.x, spec.left);
    geo.x = Math.max(-geo.w + 120, Math.min(A.width - 120, geo.x)); geo.y = Math.max(0, Math.min(A.height - 60, geo.y));
    if (!phone()) geo.h = Math.max(220, Math.min(geo.h, A.height - 92 - geo.y));
    const el = document.createElement("section");
    el.className = "win anim-in"; el.setAttribute("role", "dialog"); el.setAttribute("aria-label", spec.name);
    Object.assign(el.style, { left: geo.x + "px", top: geo.y + "px", width: geo.w + "px", height: geo.h + "px" });
    const xs = '<svg viewBox="0 0 9 9"><path d="M1 1l7 7M8 1L1 8" stroke="#000" stroke-width="1.6"/></svg>';
    const ms = '<svg viewBox="0 0 9 9"><path d="M1 4.5h7" stroke="#000" stroke-width="1.6"/></svg>';
    const zs = '<svg viewBox="0 0 9 9"><path d="M1.5 1.5h6v6h-6z" fill="none" stroke="#000" stroke-width="1.4"/></svg>';
    el.innerHTML = `<div class="tb"><div class="ctl"><button type="button" class="x" aria-label="Close">${xs}</button><button type="button" class="m" aria-label="Minimise">${ms}</button><button type="button" class="z" aria-label="Zoom">${zs}</button></div><div class="t"></div><span class="tb-right"></span></div><div class="wb"></div><div class="grip" aria-hidden="true"></div>`;
    $("#wins").appendChild(el);
    const win = { key, app, arg, el, title: "", z: 0, min: false, geo, charts: [], from };
    this.wins.set(key, win);
    this.setTitle(win, spec.title ? spec.title(arg) : spec.name);
    this.bind(win);
    this.render(win);
    this.focus(key);
    this.animIn(win, from || Dock.rect(app) || Dock.rect(spec.dockAs || app));
    Dock.bounce(spec.dockAs || app); Dock.refresh();
    return win;
  },
  setTitle(win, t) { win.title = t; $(".t", win.el).textContent = t; },
  render(win) {
    win.charts.forEach((c) => { c.destroy(); Charts.live.delete(c); }); win.charts = [];
    const spec = APPS[win.app], body = $(".wb", win.el);
    const locked = App.view === "visitor" && !spec.public;
    body.innerHTML = spec.html(win.arg, win);
    $(".tb-right", win.el).innerHTML = spec.pin && !locked ? `<button type="button" class="pin" data-pin aria-label="Pin to desktop"><svg viewBox="0 0 9 9"><path d="M3 1h3l-.5 3 1.5 1.5H2L3.5 4z M4.5 5.5V8.5" fill="none" stroke="currentColor" stroke-width="1.1"/></svg>Pin</button>` : "";
    if (spec.mount) spec.mount(body, win);
    if (locked) {
      body.style.overflow = "hidden";
      const u = document.createElement("div"); u.className = "unlock";
      u.innerHTML = `<div class="box"><img src="${Art.icon(spec.icon)}" width="64" height="64" style="image-rendering:pixelated" alt=""><b>${spec.name} is for holders</b><p>${esc(spec.locked || "Live positioning, updated every few minutes.")}</p><button type="button" class="btn primary" data-unlock>See how to unlock</button></div>`;
      body.appendChild(u);
      $("[data-unlock]", u).onclick = () => WM.open("unlock");
    } else body.style.overflow = "";
    const pin = $("[data-pin]", win.el);
    if (pin) pin.onclick = () => { Desk.add(spec.pin(win.arg)); toast(`Pinned ${win.title} to your desktop`); };
  },
  rerenderAll() { this.wins.forEach((w) => this.render(w)); },
  animIn(win, r) {
    const el = win.el;
    if (r && !phone()) {
      const g = el.getBoundingClientRect(), s = Math.max(0.08, Math.min(0.4, r.width / g.width));
      el.style.transformOrigin = "0 0";
      el.style.setProperty("--from", `translate(${r.left - g.left}px, ${r.top - g.top}px) scale(${s})`);
    }
    if (reduced()) { el.classList.remove("anim-in"); return; }
    requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove("anim-in")));
  },
  animOut(win, r, done) {
    const el = win.el;
    if (r && !phone()) {
      const g = el.getBoundingClientRect(), s = Math.max(0.06, r.width / g.width);
      el.style.transformOrigin = "0 0";
      el.style.setProperty("--from", `translate(${r.left - g.left}px, ${r.top - g.top}px) scale(${s})`);
    }
    else el.style.setProperty("--from", "scale(.96)");
    if (reduced()) { done(); return; }
    el.classList.add("anim-out"); setTimeout(done, 240);
  },
  focus(key, scroll) {
    const w = this.wins.get(key); if (!w) return;
    w.z = ++this.z; w.el.style.zIndex = w.z;
    this.wins.forEach((x) => x.el.classList.toggle("focus", x === w));
    Menubar.refresh();
    if (scroll && phone()) w.el.scrollIntoView?.();
  },
  close(key) {
    const w = this.wins.get(key); if (!w) return;
    this.wins.delete(key);
    w.charts.forEach((c) => { c.destroy(); Charts.live.delete(c); });
    this.animOut(w, null, () => w.el.remove());
    Tip.hide(); Dock.refresh(); Menubar.refresh();
    const t = this.top(); if (t) this.focus(t.key);
  },
  minimize(key) {
    const w = this.wins.get(key); if (!w) return;
    this.animOut(w, Dock.rect(APPS[w.app].dockAs || w.app), () => { w.el.hidden = true; w.el.classList.remove("anim-out"); });
    w.min = true; Menubar.refresh();
    const t = this.top(); if (t) this.focus(t.key);
  },
  restore(key) { const w = this.wins.get(key); w.min = false; w.el.hidden = false; w.el.classList.add("anim-in"); this.animIn(w, Dock.rect(APPS[w.app].dockAs || w.app)); },
  zoom(key) {
    const w = this.wins.get(key), A = this.area(), el = w.el;
    if (w.zoomed) { Object.assign(el.style, { left: w.zoomed.x + "px", top: w.zoomed.y + "px", width: w.zoomed.w + "px", height: w.zoomed.h + "px" }); w.zoomed = null; el.classList.remove("zoomed"); }
    else {
      w.zoomed = { x: el.offsetLeft, y: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight };
      Object.assign(el.style, { left: "8px", top: "8px", width: A.width - 16 + "px", height: A.height - 96 + "px" });
    }
  },
  save(w) { if (!phone()) Store.set("win:" + w.key, { x: w.el.offsetLeft, y: w.el.offsetTop, w: w.el.offsetWidth, h: w.el.offsetHeight }); },
  bind(w) {
    const el = w.el, tb = $(".tb", el);
    el.addEventListener("pointerdown", () => { if (this.top() !== w) this.focus(w.key); }, true);
    $(".x", el).onclick = () => this.close(w.key);
    $(".m", el).onclick = () => this.minimize(w.key);
    $(".z", el).onclick = () => this.zoom(w.key);
    tb.addEventListener("dblclick", (e) => { if (!e.target.closest("button")) this.zoom(w.key); });
    tb.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button") || phone()) return;
      const sx = e.clientX, sy = e.clientY, ox = el.offsetLeft, oy = el.offsetTop, A = this.area();
      el.classList.add("dragging"); tb.setPointerCapture(e.pointerId);
      const mv = (ev) => {
        el.style.left = Math.max(-el.offsetWidth + 120, Math.min(A.width - 120, ox + ev.clientX - sx)) + "px";
        el.style.top = Math.max(0, Math.min(A.height - 40, oy + ev.clientY - sy)) + "px";
      };
      const upf = () => { el.classList.remove("dragging"); tb.removeEventListener("pointermove", mv); tb.removeEventListener("pointerup", upf); w.zoomed = null; el.classList.remove("zoomed"); this.save(w); };
      tb.addEventListener("pointermove", mv); tb.addEventListener("pointerup", upf);
    });
    const grip = $(".grip", el);
    grip.addEventListener("pointerdown", (e) => {
      e.preventDefault(); const sx = e.clientX, sy = e.clientY, ow = el.offsetWidth, oh = el.offsetHeight;
      el.classList.add("dragging"); grip.setPointerCapture(e.pointerId);
      const mv = (ev) => { el.style.width = Math.max(320, ow + ev.clientX - sx) + "px"; el.style.height = Math.max(220, oh + ev.clientY - sy) + "px"; };
      const upf = () => { el.classList.remove("dragging"); grip.removeEventListener("pointermove", mv); grip.removeEventListener("pointerup", upf); this.save(w); };
      grip.addEventListener("pointermove", mv); grip.addEventListener("pointerup", upf);
    });
  },
};

/* ---------------- dragging things onto the desktop ---------------- */
// Mouse and pen drag a card out of a window onto the desktop; touch uses the Pin buttons (so lists still scroll).
function draggable(el, payload, onClick) {
  el.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const sx = e.clientX, sy = e.clientY;
    let ghost = null;
    const touch = e.pointerType === "touch";
    const mv = (ev) => {
      if (touch) return;
      if (!ghost && Math.hypot(ev.clientX - sx, ev.clientY - sy) < 7) return;
      if (!ghost) {
        ghost = document.createElement("div"); ghost.className = "ghost";
        ghost.innerHTML = Desk.itemHTML(payload()); document.body.appendChild(ghost);
        $("#dock").classList.add("drop-ready"); Tip.hide();
      }
      ghost.style.left = ev.clientX + "px"; ghost.style.top = ev.clientY + "px";
    };
    const upf = (ev) => {
      document.removeEventListener("pointermove", mv); document.removeEventListener("pointerup", upf);
      $("#dock").classList.remove("drop-ready");
      if (!ghost) { if (Math.hypot(ev.clientX - sx, ev.clientY - sy) < 7) onClick && onClick(ev); return; }
      ghost.remove();
      const over = document.elementFromPoint(ev.clientX, ev.clientY);
      if (over && (over.closest(".win") || over.closest("#dock") || over.closest("#menubar"))) { toast("Drop it on an empty part of the desktop to pin it"); return; }
      const D0 = $("#desk").getBoundingClientRect();
      Desk.add({ ...payload(), x: ev.clientX - D0.left - 100, y: ev.clientY - D0.top - 40 });
      toast("Pinned to your desktop");
    };
    document.addEventListener("pointermove", mv); document.addEventListener("pointerup", upf);
  });
}

/* ---------------- the desktop ---------------- */
const Desk = {
  items: [],
  load() { this.items = Store.get("desk:" + App.view, null) || this.defaults(); },
  save() { Store.set("desk:" + App.view, this.items); World.syncFollow(); },
  defaults() {
    const A = $("#desk").getBoundingClientRect(), x = Math.max(16, A.width - 252);
    if (App.view === "visitor") return [
      { type: "widget", id: "w-record", what: "record", x, y: 20 },
      { type: "note", id: "n-v", x: x + 10, y: 190, text: "Holders see where Hyperliquid's best traders are camped, live, and which three coins I'd look at first." },
    ];
    return [
      ...Picks.list().map((p, i) => ({ type: "coin", id: "c-" + p.coin, coin: p.coin, x, y: 20 + i * 142 })),
      { type: "widget", id: "w-herd", what: "herd", x, y: 20 + 3 * 142 + 6 },
    ];
  },
  tidy() {
    const A = $("#desk").getBoundingClientRect(); let col = 0, row = 0;
    this.items.forEach((it) => { it.x = Math.max(16, A.width - 252 - col * 236); it.y = 20 + row * 150; row++; if (20 + (row + 1) * 150 > A.height - 90) { row = 0; col++; } });
    this.save(); this.render();
  },
  reset() { Store.del("desk:" + App.view); this.items = this.defaults(); this.save(); this.render(); toast("Desktop reset"); },
  add(it) {
    const id = it.id || it.type + "-" + (it.coin || it.address || it.what || Math.random().toString(36).slice(2));
    const ex = this.items.find((x) => x.id === id);
    const A = $("#desk").getBoundingClientRect();
    const pos = { x: it.x ?? A.width / 2 - 110, y: it.y ?? 60 };
    if (ex) Object.assign(ex, pos); else this.items.push({ ...it, ...pos, id });
    this.save(); this.render();
  },
  itemHTML(it) {
    if (it.type === "coin") return Apps.dossier(it.coin);
    if (it.type === "trader") return Apps.traderCard(it.address);
    if (it.type === "widget") return Apps.widget(it.what);
    if (it.type === "note") return `<div class="note">${esc(it.text)}<div class="sig"><img src="${Art.face()}" alt="">Outrider</div></div>`;
    return "";
  },
  render() {
    const host = $("#desk"); host.innerHTML = "";
    const wrap = phone() ? Object.assign(document.createElement("div"), { className: "stack" }) : host;
    if (phone()) host.appendChild(wrap);
    const A = host.getBoundingClientRect();
    const order = phone() ? [...this.items].sort((a, b) => (a.type === "note") - (b.type === "note") || a.y - b.y) : this.items;
    for (const it of order) {
      const el = document.createElement("div");
      el.className = "item" + (it.type === "note" || it.type === "widget" ? " wide" : "");
      el.innerHTML = this.itemHTML(it);
      if (!phone()) { it.x = Math.max(0, Math.min(A.width - 140, it.x)); it.y = Math.max(0, Math.min(A.height - 180, it.y)); el.style.left = it.x + "px"; el.style.top = it.y + "px"; }
      el.tabIndex = 0; el.setAttribute("role", "button");
      el.setAttribute("aria-label", it.type === "coin" ? `${it.coin} dossier` : it.type === "trader" ? "Trader card" : it.type === "widget" ? APPS[it.what].name : "Note from Outrider");
      this.bindItem(el, it);
      wrap.appendChild(el);
    }
  },
  open(it, el) {
    if (it.type === "coin") WM.open("coin", it.coin, el.getBoundingClientRect());
    else if (it.type === "trader") WM.open("trader", it.address, el.getBoundingClientRect());
    else if (it.type === "widget") WM.open(it.what, null, el.getBoundingClientRect());
    else WM.open("dispatch", null, el.getBoundingClientRect());
  },
  bindItem(el, it) {
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this.open(it, el); } if (e.key === "Delete" || e.key === "Backspace") this.remove(it); });
    el.addEventListener("click", () => { if (phone()) this.open(it, el); });
    el.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      if (phone()) return;
      const sx = e.clientX, sy = e.clientY, ox = it.x, oy = it.y, A = $("#desk").getBoundingClientRect();
      let moved = false; el.setPointerCapture(e.pointerId);
      $$(".item.sel").forEach((x) => x.classList.remove("sel")); el.classList.add("sel");
      const trash = $("#dock .trash");
      const mv = (ev) => {
        if (!moved && Math.hypot(ev.clientX - sx, ev.clientY - sy) < 5) return;
        if (!moved) { moved = true; el.classList.add("drag"); $("#dock").classList.add("drop-ready"); }
        it.x = Math.max(-60, Math.min(A.width - 80, ox + ev.clientX - sx)); it.y = Math.max(0, Math.min(A.height - 40, oy + ev.clientY - sy));
        el.style.left = it.x + "px"; el.style.top = it.y + "px";
        const t = Dock.trashRect(); trash.classList.toggle("hot", ev.clientX > t.left - 20 && ev.clientX < t.right + 20 && ev.clientY > t.top - 30);
      };
      const upf = (ev) => {
        el.removeEventListener("pointermove", mv); el.removeEventListener("pointerup", upf);
        el.classList.remove("drag"); $("#dock").classList.remove("drop-ready");
        if (trash.classList.contains("hot")) { trash.classList.remove("hot"); this.remove(it); return; }
        if (moved) this.save(); else this.open(it, el);
        void ev;
      };
      el.addEventListener("pointermove", mv); el.addEventListener("pointerup", upf);
    });
  },
  remove(it) { this.items = this.items.filter((x) => x !== it); this.save(); this.render(); toast("Removed from the desktop"); },
};

document.addEventListener("pointerover", (e) => {
  const t = e.target.closest("[data-tip]"); if (!t || e.pointerType === "touch") return;
  const r = t.getBoundingClientRect(); Tip.show(esc(t.dataset.tip), r.left, r.bottom);
  t.addEventListener("pointerleave", () => Tip.hide(), { once: true });
});
document.addEventListener("focusin", (e) => { const t = e.target.closest?.("[data-tip]"); if (t) { const r = t.getBoundingClientRect(); Tip.show(esc(t.dataset.tip), r.left, r.bottom); } });
document.addEventListener("focusout", (e) => { if (e.target.closest?.("[data-tip]")) Tip.hide(); });
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-tip]");
  if (t && matchMedia("(hover: none)").matches) { const r = t.getBoundingClientRect(); Tip.show(esc(t.dataset.tip), r.left, r.bottom); setTimeout(() => Tip.hide(), 4000); }
});
