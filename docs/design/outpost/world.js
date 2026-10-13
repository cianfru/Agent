/* The Territory: the market as a place you can ride through.
   Every camp is a coin. East = more of the crowd is long; north of the riverbed = most proven traders holding it
   are long, south = short. Riders are proven traders, camped at their biggest position; cattle are the crowd.
   Dust rises with open interest. A timeline replays the last two weeks; Outrider rides wherever he is talking about. */
const World = (() => {
  const LW = 520, LH = 340, MX0 = 62, MX1 = 382, MY0 = 76, MY1 = 256;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const X = (c) => MX0 + (clamp(c, 58, 90) - 58) / 32 * (MX1 - MX0);
  const Y = (p) => MY1 - p / 100 * (MY1 - MY0);
  const RIVER = Y(50), FORT = { x: 46, y: RIVER - 24 };
  const hexrgb = (h) => { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
  const mixc = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
  const BAY = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]].map((r) => r.map((v) => (v + 0.5) / 16));

  const PAL = {
    night: { north: ["#17223a", "#1b2742", "#202d4a", "#253352"], south: ["#2a1830", "#311c37", "#38203e", "#402546"], grass: "#355a5e", crack: "#1c0f22", rock: "#5a3f62",
      water: ["#101a40", "#16245a", "#2a3f86"], glint: "#a9bfff", bank: "#0f0c22", glow: [255, 150, 60], vignette: 0.55,
      mesa: ["#4a3456", "#3a2846", "#0d0a1c"], mesaRim: "#6a5084", scrub: "#2c4436", cactus: "#3f6b4b", trail: "#9a8fd0", tent: ["#e4dcf2", "#a89bcc", "#241c40"],
      cow: [["#8a5a3c", "#e8d6b8"], ["#d8c3a0", "#8a5a3c"], ["#4a3426", "#e8d6b8"]], horse: "#6a4428", blaze: "#efe2c8", hat: "#21160f", coat: "#3b4290", skin: "#f2c9a0",
      fort: ["#6a4a34", "#8f6849", "#3a2618"], dust: "#c8baf0", bg: "#1a152f", light: [255, 150, 70], cool: [70, 110, 255] },
    dawn: { north: ["#d3cf9e", "#cbc693", "#c2bd88", "#b8b37d"], south: ["#e8b88f", "#e0ad84", "#d6a079", "#cb946e"], grass: "#8a9a52", crack: "#a8694a", rock: "#f1d2ae",
      water: ["#5f93c4", "#77a7d6", "#a6c8ea"], glint: "#ffffff", bank: "#9a7650", glow: [255, 170, 90], vignette: 0.12,
      mesa: ["#d08a5c", "#b8704a", "#8a4c30"], mesaRim: "#e8a476", scrub: "#7d8848", cactus: "#4d7a44", trail: "#fff4dc", tent: ["#fffaf0", "#dccab0", "#5a3a26"],
      cow: [["#7a4a2c", "#f3e4c6"], ["#f3e4c6", "#7a4a2c"], ["#3e2a1e", "#f3e4c6"]], horse: "#6a4428", blaze: "#fff6e4", hat: "#21160f", coat: "#2f4a8a", skin: "#f2c9a0",
      fort: ["#8a5a3a", "#a8744c", "#4a2c1a"], dust: "#fff2d6", bg: "#d6b385", light: [255, 140, 60], cool: [90, 120, 255] },
    vga: { north: ["#005500", "#00aa00", "#005500", "#000000"], south: ["#aa5500", "#aa0000", "#aa5500", "#000000"], grass: "#55ff55", crack: "#000000", rock: "#ffff55",
      water: ["#0000aa", "#0000aa", "#5555ff"], glint: "#55ffff", bank: "#000000", glow: [255, 255, 85], vignette: 0,
      mesa: ["#ff5555", "#aa0000", "#000000"], mesaRim: "#ffff55", scrub: "#00aa00", cactus: "#55ff55", trail: "#ffff55", tent: ["#ffffff", "#aaaaaa", "#000000"],
      cow: [["#000000", "#ffffff"], ["#ffffff", "#000000"], ["#555555", "#ffffff"]], horse: "#000000", blaze: "#ffffff", hat: "#000000", coat: "#0000aa", skin: "#ffff55",
      fort: ["#aa5500", "#ffff55", "#000000"], dust: "#ffff55", bg: "#000055", light: [255, 85, 255], cool: [85, 85, 255] },
  };

  /* ---------------- sprites ---------------- */
  const RIDER = [
    ["...kkk...", ".kkkkkkk.", "...sss...", "...rrr...", "..ccccc..", ".cchhhcc.", "..hhwhh..", ".hhhwhhh.", ".hh.h.hh.", ".h.....h."],
    ["...kkk...", ".kkkkkkk.", "...sss...", "...rrr...", "..ccccc..", ".cchhhcc.", "..hhwhh..", ".hhhwhhh.", ".hh.h.hh.", "..h...h.."],
  ];
  const COW = [".bbb.", "wbbbb", ".b..b"];
  const TENT = ["...a...", "..aab..", ".aaabb.", ".aadbb.", "aaaddbb", "aaaddbb"];
  const FIRE = [[".y.", "yoy", "kok"], ["..y", "oyo", "kok"]];
  const FLAG = ["faa..", "faaa.", "faa..", "f....", "f....", "f....", "ff..."];
  const FORTG = ["..........a.........", "..........aa........", "..........f.........", "p.p.p.p.p.f.p.p.p.p.",
    "pppppppppppppppppppp", "pPpPpPpPpPpPpPpPpPpP", "pppppppppppppppppppp", "pPpPpPpPddpPpPpPpPpP", "ppppppppddpppppppppp", "pPpPpPpPddpPpPpPpPpP"];
  function sprite(rows, pal) {
    const c = document.createElement("canvas"); c.width = rows[0].length; c.height = rows.length;
    const g = c.getContext("2d");
    rows.forEach((r, y) => { for (let x = 0; x < r.length; x++) { const k = r[x]; if (k === "." || !pal[k]) continue; g.fillStyle = pal[k]; g.fillRect(x, y, 1, 1); } });
    return c;
  }

  /* ---------------- state ---------------- */
  const POS = [
    ...D.traders.flatMap((t) => t.positions.map((p) => ({ ...p, address: t.address, exit_ts: null }))),
    ...D.closed,
  ];
  const TR = Object.fromEntries(D.traders.map((t) => [t.address, t]));
  const COINS = Object.keys(D.lenses);
  const heads = Object.fromEntries(COINS.map((c) => [c, D.lenses[c].crowd.long_n + D.lenses[c].crowd.short_n]));
  const maxHeads = Math.max(...Object.values(heads));
  const S = {
    t: NOW, look: null, terrain: null, spr: null, cam: { x: (MX0 + MX1) / 2, y: (MY0 + MY1) / 2 + 4, z: 1 }, fit: 3, frame: 0,
    camps: {}, riders: {}, cows: [], dust: [], ring: null, outrider: { x: FORT.x + 2, y: FORT.y + 2, tx: FORT.x + 2, ty: FORT.y + 2, f: 0 },
    playing: null, flight: null, drag: null, dirty: true, hidden: false,
  };
  const maxT = () => (App.view === "visitor" ? NOW - DAY : NOW);

  /* value noise for the ground */
  function hash(x, y) { let h = Math.imul(x, 374761393) + Math.imul(y, 668265263); h = Math.imul(h ^ (h >>> 13), 1274126177); return ((h ^ (h >>> 16)) >>> 0) / 4294967296; }
  function noise(x, y) {
    const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi, s = (t) => t * t * (3 - 2 * t);
    const a = hash(xi, yi), b = hash(xi + 1, yi), c = hash(xi, yi + 1), d = hash(xi + 1, yi + 1);
    return a + (b - a) * s(xf) + (c - a) * s(yf) + (a - b - c + d) * s(xf) * s(yf);
  }
  const riverY = (x) => RIVER + 3 * Math.sin(x / 23) + 2 * Math.sin(x / 9 + 1);

  function buildTerrain() {
    const P = PAL[S.look], img = new ImageData(LW, LH), d = img.data;
    const put = (x, y, c) => { x = Math.round(x); y = Math.round(y); if (x < 0 || y < 0 || x >= LW || y >= LH) return; const i = (y * LW + x) * 4; d[i] = c[0]; d[i + 1] = c[1]; d[i + 2] = c[2]; d[i + 3] = 255; };
    const NG = P.north.map(hexrgb), SG = P.south.map(hexrgb);
    for (let y = 0; y < LH; y++) for (let x = 0; x < LW; x++) {
      const n = noise(x / 20, y / 20) * 0.6 + noise(x / 5, y / 5) * 0.4;
      const k = clamp(Math.floor(n * 4 + BAY[y & 3][x & 3] - 0.5), 0, 3);
      // north of the river is long country, south is short country; the two blend across the banks
      const dy = y - riverY(x), blend = clamp((dy + 14) / 28, 0, 1);
      put(x, y, BAY[y & 3][x & 3] < blend ? SG[k] : NG[k]);
    }
    // texture: grass tufts up north, cracked earth and stones down south (readable without colour)
    const GR = hexrgb(P.grass), CR = hexrgb(P.crack), RK = hexrgb(P.rock);
    for (let i = 0; i < 900; i++) {
      const x = Math.floor(hash(i, 21) * LW), y = Math.floor(hash(i, 22) * LH), dy = y - riverY(x);
      if (Math.abs(dy) < 7) continue;
      if (dy < 0) { put(x, y, GR); put(x, y - 1, GR); if (hash(i, 23) > 0.5) { put(x + 2, y, GR); put(x + 2, y - 1, GR); put(x + 1, y - 2, GR); } }
      else if (hash(i, 24) > 0.45) { let cx = x, cy = y; for (let k = 0; k < 4 + (i % 4); k++) { put(cx, cy, CR); cx += hash(i, k) > 0.5 ? 1 : 0; cy += hash(i, k + 9) > 0.5 ? 1 : -1; cx += 1; } }
      else { put(x, y, RK); put(x + 1, y, RK); }
    }
    // mesas around the edges, lit from the north-west
    const MZ = P.mesa.map(hexrgb), RIM = hexrgb(P.mesaRim);
    const mesas = [[16, 22, 14], [118, 12, 9], [300, 10, 11], [430, 26, 15], [496, 90, 18], [470, 190, 13], [500, 300, 20], [420, 326, 14], [22, 300, 18], [130, 330, 12], [262, 334, 11]];
    const inside = (mx, my, r, x, y) => Math.hypot((x - mx) / 1.3, y - my) < r * (0.8 + 0.32 * noise(x / 7, y / 7));
    for (const [mx, my, r] of mesas) {
      for (let y = my - r - 6; y < my + r + 9; y++) for (let x = mx - r * 1.5 - 6; x < mx + r * 1.5 + 9; x++)
        if (inside(mx, my, r, x - 3, y - 5) && !inside(mx, my, r, x, y)) put(x, y, MZ[2]);
      for (let y = my - r - 4; y < my + r + 4; y++) for (let x = mx - r * 1.5 - 4; x < mx + r * 1.5 + 4; x++) {
        if (!inside(mx, my, r, x, y)) continue;
        const edge = !inside(mx, my, r, x - 1, y - 1), t = noise(x / 5, y / 5);
        put(x, y, edge ? RIM : t > 0.5 + (BAY[y & 3][x & 3] - 0.5) * 0.4 ? MZ[0] : MZ[1]);
      }
    }
    // the river: the 50% line. Water, banks, glints of light.
    const WA = P.water.map(hexrgb), GL = hexrgb(P.glint), BK = hexrgb(P.bank);
    for (let x = 0; x < LW; x++) {
      const yc = riverY(x), w = 3 + Math.round(1.2 * Math.sin(x / 31));
      for (let dy = -w - 1; dy <= w + 1; dy++) {
        const y = Math.round(yc + dy), a = Math.abs(dy);
        put(x, y, a > w ? BK : a > w - 1 ? WA[1] : (noise(x / 4, y / 2) + BAY[y & 3][x & 3] * 0.3 > 0.95 ? WA[2] : WA[0]));
      }
      if (hash(x, 31) > 0.86) put(x, Math.round(yc + (hash(x, 32) - 0.5) * 3), GL);
    }
    // cacti on both sides
    const CA = hexrgb(P.cactus);
    for (let i = 0; i < 70; i++) {
      const x = Math.floor(hash(i, 41) * LW), y = Math.floor(hash(i, 42) * LH);
      if (Math.abs(y - riverY(x)) < 9) continue;
      put(x, y, CA); put(x, y - 1, CA); put(x, y - 2, CA); put(x - 1, y - 1, CA); put(x + 1, y - 2, CA);
    }
    const c = document.createElement("canvas"); c.width = LW; c.height = LH; c.getContext("2d").putImageData(img, 0, 0);
    return c;
  }
  function buildSprites() {
    const P = PAL[S.look];
    const rider = (scarf, horse) => RIDER.map((r) => sprite(r, { k: P.hat, s: P.skin, r: scarf, c: P.coat, h: horse || P.horse, w: P.blaze }));
    const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
    return {
      long: rider(css("--long")), short: rider(css("--short")),
      outrider: RIDER.map((r) => sprite(r, { k: P.hat, s: P.skin, r: "#e0404e", c: "#3b4290", h: "#e8e2f4", w: "#ffffff" })),
      cows: P.cow.map(([b, w]) => sprite(COW, { b, w })),
      tent: sprite(TENT, { a: P.tent[0], b: P.tent[1], d: P.tent[2] }),
      fire: FIRE.map((r) => sprite(r, { y: "#ffe14a", o: "#ff8a2a", k: "#4a2a16" })),
      flag: sprite(FLAG, { f: P.fort[2], a: css("--accent") }),
      fort: sprite(FORTG, { p: P.fort[0], P: P.fort[1], d: P.fort[2], f: P.fort[2], a: css("--accent") }),
    };
  }

  /* ---------------- readings at time t ---------------- */
  function read(t) {
    const i = ix(t), oiK = D.oi[i] / D.oi[N - 1];
    const open = POS.filter((p) => p.entry_ts <= t && (p.exit_ts == null || p.exit_ts > t));
    const camps = {};
    for (const c of COINS) {
      const mine = open.filter((p) => p.coin === c);
      const pl = mine.filter((p) => p.side === "long").length, ps = mine.length - pl;
      const crowd = D.coin_crowd[c][i], pros = mine.length ? 100 * pl / mine.length : null;
      const oi = D.lenses[c].oi * oiK;
      camps[c] = { coin: c, crowd, pros, pl, ps, usd: mine.reduce((s, p) => s + p.usd, 0), oi,
        tier: oi > 1e9 ? 3 : oi > 3e8 ? 2 : 1, x: X(crowd), y: pros == null ? riverY(X(crowd)) : Y(pros) };
    }
    // nudge camps apart, mostly up and down, so the east-west reading stays put
    const L = Object.values(camps);
    for (let k = 0; k < 140; k++) for (let a = 0; a < L.length; a++) for (let b = a + 1; b < L.length; b++) {
      const A = L[a], B = L[b], dx = B.x - A.x, dy = B.y - A.y, d = Math.hypot(dx, dy) || 0.01, need = 50;
      if (d >= need) continue;
      const push = (need - d) / 2;
      A.x -= dx / d * push * 0.45; B.x += dx / d * push * 0.45;
      A.y -= (dy / d || -1) * push; B.y += (dy / d || 1) * push;
      for (const q of [A, B]) { q.x = clamp(q.x, MX0 - 10, MX1 + 10); q.y = clamp(q.y, MY0, MY1); }
    }
    // riders: one per open position held by a proven trader, ringed around that camp (two rings when crowded)
    const riders = {}, at = {};
    for (const p of open) (at[p.coin] = at[p.coin] || []).push(p);
    for (const [coin, list] of Object.entries(at)) {
      list.sort((a, b) => (a.side === b.side ? b.usd - a.usd : a.side === "long" ? -1 : 1));
      const c = camps[coin], inner = Math.min(list.length, 7);
      list.forEach((p, k) => {
        const ring = k < inner ? 0 : 1, n = ring ? list.length - inner : inner, j = ring ? k - inner : k;
        const ang = -Math.PI / 2 - Math.PI / 5 + j * (2 * Math.PI / Math.max(n, 6)) + ring * 0.4, rad = ring ? 19 : 12;
        riders[p.address + p.coin + p.entry_ts] = { trader: TR[p.address], coin, side: p.side, usd: p.usd, since: p.entry_ts,
          tx: c.x + Math.cos(ang) * rad, ty: c.y + 1 + Math.sin(ang) * rad * 0.72 };
      });
    }
    return { camps, riders, herd: D.crowd[i], oiRank: rank(D.oi[i]), flush: t >= F0 && t <= F1 + 4 * H1 ? Math.sin(Math.PI * (t - F0) / (F1 + 4 * H1 - F0)) : 0 };
  }
  const sortedOI = [...D.oi].sort((a, b) => a - b);
  const rank = (v) => sortedOI.findIndex((x) => x >= v) / sortedOI.length;

  function apply(t, instant) {
    S.t = t; const R = read(t); S.R = R;
    for (const c of Object.values(R.camps)) {
      const cur = S.camps[c.coin];
      S.camps[c.coin] = { ...c, cx: instant || !cur ? c.x : cur.cx, cy: instant || !cur ? c.y : cur.cy };
    }
    for (const [a, r] of Object.entries(R.riders)) {
      const cur = S.riders[a], home = { x: FORT.x + 2, y: FORT.y + 4 };
      S.riders[a] = { ...r, x: instant ? r.tx : cur && !cur.leaving ? cur.x : cur ? cur.x : home.x, y: instant ? r.ty : cur && !cur.leaving ? cur.y : cur ? cur.y : home.y };
    }
    for (const [a, r] of Object.entries(S.riders)) if (!R.riders[a]) {
      if (instant) delete S.riders[a]; else { r.leaving = true; r.tx = FORT.x + 2; r.ty = FORT.y + 4; }
    }
    if (!S.cows.length) {
      for (const c of COINS) {
        const n = 4 + Math.round(11 * Math.sqrt(heads[c] / maxHeads));
        for (let k = 0; k < n; k++) S.cows.push({ coin: c, a: hash(k, c.length * 31 + c.charCodeAt(0)) * Math.PI * 2, r: 19 + hash(k, 77 + c.charCodeAt(0)) * 6, v: Math.floor(hash(k, 5) * 3), w: 0, p: hash(k, 11) * 6.28 });
      }
    }
    UI.time(); UI.place(); S.dirty = true;
  }

  /* ---------------- drawing ---------------- */
  const frame = document.createElement("canvas"); frame.width = LW; frame.height = LH;
  const fg = frame.getContext("2d");
  function compose(now) {
    const P = PAL[S.look], still = reduced();
    fg.imageSmoothingEnabled = false;
    fg.drawImage(S.terrain, 0, 0);
    const R = S.R;
    // the herd's line
    const hx = Math.round(X(R.herd));
    fg.fillStyle = P.trail; fg.globalAlpha = S.ring === "_herd" ? 1 : 0.7;
    for (let y = MY0 - 18; y < MY1 + 22; y += 4) fg.fillRect(hx, y, 1, 2);
    fg.globalAlpha = 1;
    // cattle
    for (const cw of S.cows) {
      const c = S.camps[cw.coin]; if (!c) continue;
      if (!still) { cw.w += 0.012; }
      const scatter = 1 + 1.8 * R.flush, wob = still ? 0 : Math.sin(cw.w + cw.p) * 1.6;
      const x = Math.round(c.cx + Math.cos(cw.a + wob * 0.05) * (cw.r * scatter + wob)), y = Math.round(c.cy + Math.sin(cw.a) * (cw.r * 0.72 * scatter) + 4);
      fg.drawImage(S.spr.cows[cw.v], x - 2, y - 1);
    }
    // firelight: camps glow, the bigger the market the wider
    if (P.glow && S.look !== "dawn") {
      fg.globalCompositeOperation = "lighter";
      for (const c of Object.values(S.camps)) {
        const r = 10 + 7 * c.tier, gr = fg.createRadialGradient(c.cx, c.cy, 1, c.cx, c.cy, r);
        gr.addColorStop(0, `rgba(${P.glow.join(",")},${c.pros == null ? 0.08 : 0.2})`); gr.addColorStop(1, "rgba(0,0,0,0)");
        fg.fillStyle = gr; fg.fillRect(c.cx - r, c.cy - r, r * 2, r * 2);
      }
      fg.globalCompositeOperation = "source-over";
    }
    // camps
    const slots = { 1: [[0, 0]], 2: [[-5, 0], [4, 1]], 3: [[-7, 1], [1, -3], [7, 2]] };
    for (const c of Object.values(S.camps)) {
      fg.globalAlpha = c.pros == null ? 0.5 : 1;
      for (const [dx, dy] of slots[c.tier]) fg.drawImage(S.spr.tent, Math.round(c.cx + dx - 3), Math.round(c.cy + dy - 5));
      if (c.tier === 3) fg.drawImage(S.spr.fire[still ? 0 : Math.floor(now / 260) % 2], Math.round(c.cx - 1), Math.round(c.cy + 4));
      fg.globalAlpha = 1;
      if (S.follow.has(c.coin)) fg.drawImage(S.spr.flag, Math.round(c.cx + 9), Math.round(c.cy - 12));
    }
    // highlight ring
    if (S.ring && S.camps[S.ring]) {
      const c = S.camps[S.ring]; fg.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
      for (let k = 0; k < 64; k++) { if (k % 4 > 1) continue; const a = k / 64 * Math.PI * 2; fg.fillRect(Math.round(c.cx + Math.cos(a) * 21), Math.round(c.cy + Math.sin(a) * 17), 1, 1); }
    }
    // the outpost
    fg.drawImage(S.spr.fort, FORT.x - 10, FORT.y - 6);
    // riders
    if (App.view !== "visitor") {
      const list = Object.values(S.riders).sort((a, b) => a.y - b.y);
      for (const r of list) {
        const moving = Math.hypot(r.tx - r.x, r.ty - r.y) > 0.6;
        const f = moving && !still ? Math.floor(now / 140) % 2 : 0;
        fg.drawImage((r.side === "short" ? S.spr.short : S.spr.long)[f], Math.round(r.x - 4), Math.round(r.y - 9));
      }
    }
    const o = S.outrider, om = Math.hypot(o.tx - o.x, o.ty - o.y) > 0.6;
    fg.drawImage(S.spr.outrider[om && !still ? Math.floor(now / 120) % 2 : 0], Math.round(o.x - 4), Math.round(o.y - 9));
    // dust rises with open interest
    if (!still) {
      const want = Math.round(40 + 160 * R.oiRank + 260 * R.flush);
      while (S.dust.length < want) S.dust.push({ x: Math.random() * LW, y: Math.random() * LH, v: 0.15 + Math.random() * 0.5 });
      if (S.dust.length > want) S.dust.length = want;
      fg.fillStyle = P.dust; fg.globalAlpha = 0.35 + 0.3 * R.flush;
      for (const p of S.dust) { p.x += p.v * (1 + 2.5 * R.flush); p.y += Math.sin((p.x + p.v * 90) / 30) * 0.08; if (p.x > LW) { p.x = 0; p.y = Math.random() * LH; } fg.fillRect(Math.round(p.x), Math.round(p.y), 1, 1); }
      fg.globalAlpha = 1;
    }
    // the light follows the herd: warmer when it leans long
    const warm = clamp((R.herd - 66) / 20, 0, 1);
    fg.fillStyle = `rgb(${mixc(P.cool, P.light, warm)})`; fg.globalAlpha = 0.07; fg.fillRect(0, 0, LW, LH); fg.globalAlpha = 1;
  }

  function view() {
    const W = innerWidth, Hh = innerHeight, tray = !phone() && W > 1100 ? 250 : 0;
    const riding = document.body.classList.contains("riding"), top = 30 + (phone() && !riding ? 150 : 0), bottom = phone() ? (riding ? 230 : 140) : (riding ? 190 : 140);
    return { x0: 0, y0: top, w: W - tray, h: Hh - top - bottom, W, H: Hh };
  }
  function fitScale() {
    const v = view();
    return Math.max(phone() ? 2.2 : 1, Math.min(v.w / (MX1 - MX0 + 60), v.h / (MY1 - MY0 + 56)), innerWidth / LW, (innerHeight - 30) / LH);
  }
  function scale() { return S.fit * S.cam.z; }
  function clampCam() {
    const v = view(), s = scale();
    const hw = v.w / 2 / s, hh = v.h / 2 / s;
    S.cam.x = LW > hw * 2 ? clamp(S.cam.x, hw - 30, LW - hw + 30) : LW / 2;
    S.cam.y = LH > hh * 2 ? clamp(S.cam.y, hh - 30, LH - hh + 30) : LH / 2;
  }
  function toScreen(x, y) { const v = view(), s = scale(); return [v.x0 + v.w / 2 + (x - S.cam.x) * s, v.y0 + v.h / 2 + (y - S.cam.y) * s]; }
  function toWorld(sx, sy) { const v = view(), s = scale(); return [(sx - v.x0 - v.w / 2) / s + S.cam.x, (sy - v.y0 - v.h / 2) / s + S.cam.y]; }

  function blit() {
    const cv = $("#world"), dpr = Math.min(2, devicePixelRatio || 1), W = innerWidth, Hh = innerHeight;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(Hh * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(Hh * dpr); }
    const g = cv.getContext("2d"); g.imageSmoothingEnabled = false;
    g.setTransform(1, 0, 0, 1, 0, 0); g.fillStyle = PAL[S.look].bg; g.fillRect(0, 0, cv.width, cv.height);
    const [x0, y0] = toScreen(0, 0), s = scale();
    g.drawImage(frame, Math.round(x0 * dpr), Math.round(y0 * dpr), Math.round(LW * s * dpr), Math.round(LH * s * dpr));
    const vg = PAL[S.look].vignette;
    if (vg) {
      const gr = g.createRadialGradient(cv.width * 0.45, cv.height * 0.45, Math.min(cv.width, cv.height) * 0.35, cv.width * 0.45, cv.height * 0.45, Math.max(cv.width, cv.height) * 0.75);
      gr.addColorStop(0, "rgba(0,0,0,0)"); gr.addColorStop(1, `rgba(4,3,14,${vg})`); g.fillStyle = gr; g.fillRect(0, 0, cv.width, cv.height);
    }
  }

  function tick(now) {
    requestAnimationFrame(tick);
    if (S.hidden) return;
    let moving = false;
    // camera flight
    if (S.flight) {
      const f = S.flight, u = clamp((now - f.t0) / f.dur, 0, 1), e = u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
      S.cam.x = f.a.x + (f.b.x - f.a.x) * e; S.cam.y = f.a.y + (f.b.y - f.a.y) * e; S.cam.z = f.a.z + (f.b.z - f.a.z) * e;
      clampCam(); moving = true;
      if (u >= 1) { S.flight = null; f.done && f.done(); }
    }
    // time playback
    if (S.playing) {
      const p = S.playing, u = clamp((now - p.t0) / p.dur, 0, 1);
      apply(p.a + (p.b - p.a) * u); moving = true;
      if (u >= 1) { S.playing = null; UI.play(false); p.done && p.done(); }
    }
    // things ease toward where they belong
    const k = reduced() ? 1 : 0.12;
    for (const c of Object.values(S.camps)) { c.cx += (c.x - c.cx) * k; c.cy += (c.y - c.cy) * k; if (Math.abs(c.x - c.cx) > 0.05) moving = true; }
    const rk = reduced() ? 1 : 0.06;
    for (const [a, r] of Object.entries(S.riders)) {
      const dx = r.tx - r.x, dy = r.ty - r.y, d = Math.hypot(dx, dy);
      if (d > 0.4) { const st = Math.min(d, Math.max(0.5, d * rk)); r.x += dx / d * st; r.y += dy / d * st; moving = true; }
      else if (r.leaving) delete S.riders[a];
    }
    const o = S.outrider, od = Math.hypot(o.tx - o.x, o.ty - o.y);
    if (od > 0.4) { const st = reduced() ? od : Math.min(od, Math.max(0.6, od * 0.07)); o.x += (o.tx - o.x) / od * st; o.y += (o.ty - o.y) / od * st; moving = true; }
    if (moving || S.dirty || !reduced()) { compose(now); blit(); UI.place(); S.dirty = false; }
  }

  /* ---------------- labels, plaques and the timeline ---------------- */
  const UI = {
    build() {
      const ui = $("#world-ui");
      ui.innerHTML = COINS.map((c) => `<button type="button" class="camp" data-coin="${c}">${coinIcon(c, 16)}<b>${c}</b><span class="cl"></span></button>`).join("")
        + `<div class="plaque" data-at="ne">Everyone long</div><div class="plaque" data-at="nw">Pros long, crowd lighter</div>`
        + `<div class="plaque" data-at="se">Pros short, crowd long</div><div class="plaque" data-at="sw">Pros short, crowd lighter</div>`
        + `<div class="plaque river" data-at="river">The divide: north, the pros lean long</div><div class="plaque herd" data-at="herd"></div><div class="plaque fort" data-at="fort">The Outpost</div>`;
      $$(".camp", ui).forEach((b) => {
        const coin = b.dataset.coin;
        b.addEventListener("pointerenter", (e) => { const c = S.camps[coin], r = b.getBoundingClientRect(); Tip.show(UI.campTip(c), r.left, r.bottom); void e; });
        b.addEventListener("pointerleave", () => Tip.hide());
        b.addEventListener("click", () => { Tip.hide(); WM.open("coin", coin, b.getBoundingClientRect()); });
      });
      const tr = $("#trail");
      tr.innerHTML = `<button type="button" class="tr-play" aria-label="Play the last two weeks">▶</button>
        <div class="tr-track"><input type="range" id="tr-range" min="${T[0]}" max="${NOW}" step="3600" value="${NOW}" aria-label="Time"><i class="tr-ev" title="Flush"></i></div>
        <span class="tr-when mono"></span><button type="button" class="tr-now">Now</button>${info("How to read the territory. Each camp is a coin: further east, a larger share of the crowd is long; north of the river, most proven traders holding it are long, south, short. Camps are nudged apart so none overlap; the Map window has the exact positions. Each rider is one proven trader's position (blue bandana long, red short). Cattle show how many of the crowd hold the coin. Dust thickens with open interest. Drag the timeline or press play to replay the last two weeks.")}`;
      const rg = $("#tr-range", tr);
      const ev = $(".tr-ev", tr), span = NOW - T[0];
      ev.style.left = (F0 - T[0]) / span * 100 + "%"; ev.style.width = Math.max(0.6, (F1 - F0) / span * 100) + "%";
      rg.addEventListener("input", () => { S.playing = null; UI.play(false); apply(Math.min(+rg.value, maxT())); });
      $(".tr-now", tr).onclick = () => { S.playing = null; UI.play(false); apply(maxT()); };
      $(".tr-play", tr).onclick = () => {
        if (S.playing) { S.playing = null; UI.play(false); return; }
        const from = S.t >= maxT() - 3600 ? T[0] : S.t;
        World.play(from, maxT(), Math.max(1500, (maxT() - from) / (NOW - T[0]) * 16000));
      };
    },
    play(on) { const b = $("#trail .tr-play"); if (b) { b.textContent = on ? "❚❚" : "▶"; b.setAttribute("aria-label", on ? "Pause" : "Play the last two weeks"); } },
    time() {
      const rg = $("#tr-range"); if (!rg) return;
      rg.value = S.t; rg.max = maxT();
      const live = S.t >= maxT() - 1800;
      $("#trail .tr-when").textContent = live ? (App.view === "visitor" ? "A day ago" : "Now") : day(S.t) + ", " + hhmm(S.t) + " UTC";
      $("#trail").classList.toggle("live", live);
      const h = $("#world-ui .herd");
      if (h) h.textContent = `The herd: ${S.R.herd.toFixed(0)}% long`;
      for (const b of $$("#world-ui .camp")) {
        const c = S.camps[b.dataset.coin];
        b.classList.toggle("empty", !(c.pl + c.ps));
      }
    },
    campTip(c) {
      const vis = App.view === "visitor";
      return `<div class="lab">${c.coin} · ${S.t >= maxT() - 1800 ? "now" : day(S.t) + ", " + hhmm(S.t)}</div>
        <div><span class="v">${c.crowd.toFixed(0)}%</span> <span class="lab">of the crowd long</span></div>
        ${vis ? `<div class="lab">Riders show for holders</div>` : `<div><span class="v">${c.pl} long · ${c.ps} short</span> <span class="lab">proven traders</span></div>${c.usd ? `<div><span class="v">${usd(c.usd)}</span> <span class="lab">they hold</span></div>` : ""}`}
        <div><span class="v">${usd(c.oi)}</span> <span class="lab">open interest</span></div>`;
    },
    place() {
      const s = scale();
      for (const b of $$("#world-ui .camp")) {
        const c = S.camps[b.dataset.coin]; if (!c) continue;
        const [x, y] = toScreen(c.cx, c.cy + 9);
        b.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px) translate(-50%, 0)`;
        b.classList.toggle("ring", S.ring === c.coin);
        b.style.fontSize = Math.max(11, Math.min(14, 3.6 * s)) + "px";
      }
      const pos = { ne: [MX1, MY0 - 4, "end"], nw: [MX0, MY0 - 4, "start"], se: [MX1, MY1 + 14, "end"], sw: [MX0, MY1 + 14, "start"],
        river: [MX1 + 4, riverY(MX1) - 13, "end"], herd: [X(S.R.herd), MY0 - 22, "mid"], fort: [FORT.x + 13, FORT.y + 4, "start"] };
      for (const p of $$("#world-ui .plaque")) {
        const [wx, wy, al] = pos[p.dataset.at], [x, y] = toScreen(wx, wy);
        p.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px) translate(${al === "end" ? "-100%" : al === "mid" ? "-50%" : "0"}, -50%)`;
      }
    },
  };

  /* ---------------- input ---------------- */
  function hitRider(wx, wy) {
    const o = S.outrider;
    if (Math.hypot(o.x - wx, o.y - 5 - wy) < 7) return { outrider: true };
    if (App.view === "visitor") return null;
    let best = null, bd = 7;
    for (const r of Object.values(S.riders)) { if (r.leaving) continue; const d = Math.hypot(r.x - wx, r.y - 5 - wy); if (d < bd) { bd = d; best = r; } }
    return best;
  }
  function bind() {
    const cv = $("#world");
    const ptrs = new Map();
    cv.addEventListener("pointerdown", (e) => {
      cv.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
      S.drag = { sx: e.clientX, sy: e.clientY, cx: S.cam.x, cy: S.cam.y, moved: false };
      if (ptrs.size === 2) { const [a, b] = [...ptrs.values()]; S.pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), z: S.cam.z }; }
      S.flight = null;
    });
    cv.addEventListener("pointermove", (e) => {
      if (ptrs.has(e.pointerId)) ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (S.pinch && ptrs.size === 2) { const [a, b] = [...ptrs.values()]; S.cam.z = clamp(S.pinch.z * Math.hypot(a.x - b.x, a.y - b.y) / S.pinch.d, 1, 4); clampCam(); S.dirty = true; return; }
      if (S.drag && ptrs.size) {
        const dx = e.clientX - S.drag.sx, dy = e.clientY - S.drag.sy;
        if (Math.hypot(dx, dy) > 4) S.drag.moved = true;
        if (S.drag.moved) { S.cam.x = S.drag.cx - dx / scale(); S.cam.y = S.drag.cy - dy / scale(); clampCam(); S.dirty = true; cv.style.cursor = "grabbing"; Tip.hide(); return; }
      }
      const [wx, wy] = toWorld(e.clientX, e.clientY), h = hitRider(wx, wy);
      if (h && e.pointerType === "mouse") {
        cv.style.cursor = "pointer";
        if (h.outrider) Tip.show(`<div class="v">Outrider</div><div class="lab">Open the dispatch</div>`, e.clientX, e.clientY);
        else Tip.show(`<div class="v">${esc(h.trader.name)}</div><div><span class="${h.side}">${sideWord(h.side)} ${h.coin}</span> <span class="lab">${usd(h.usd)} since ${day(h.since)}</span></div><div class="lab">${esc(recordLine(h.trader))}</div>`, e.clientX, e.clientY);
      } else { cv.style.cursor = ""; if (e.pointerType === "mouse") Tip.hide(); }
    });
    const up = (e) => {
      ptrs.delete(e.pointerId); if (ptrs.size < 2) S.pinch = null;
      cv.style.cursor = "";
      if (S.drag && !S.drag.moved) {
        const [wx, wy] = toWorld(e.clientX, e.clientY), h = hitRider(wx, wy);
        if (h && h.outrider) WM.open("dispatch");
        else if (h) WM.open("trader", h.trader.address);
        else {
          const c = Object.values(S.camps).find((q) => Math.hypot(q.cx - wx, q.cy - wy) < 11);
          if (c) WM.open("coin", c.coin);
        }
      }
      S.drag = null;
    };
    cv.addEventListener("pointerup", up); cv.addEventListener("pointercancel", up);
    cv.addEventListener("pointerleave", () => Tip.hide());
    cv.addEventListener("wheel", (e) => {
      e.preventDefault();
      const [wx, wy] = toWorld(e.clientX, e.clientY), z0 = S.cam.z;
      S.cam.z = clamp(z0 * Math.exp(-e.deltaY * 0.0016), 1, 4);
      const k = 1 - z0 / S.cam.z; S.cam.x += (wx - S.cam.x) * k; S.cam.y += (wy - S.cam.y) * k;
      clampCam(); S.dirty = true; S.flight = null;
    }, { passive: false });
    cv.addEventListener("dblclick", () => World.flyHome());
    document.addEventListener("visibilitychange", () => { S.hidden = document.hidden; });
  }

  return {
    start() {
      S.follow = new Set();
      this.restyle(true);
      UI.build(); bind();
      S.fit = fitScale(); clampCam();
      apply(maxT(), true);
      this.syncFollow();
      requestAnimationFrame(tick);
      addEventListener("resize", () => { S.fit = fitScale(); clampCam(); S.dirty = true; });
    },
    restyle(first) {
      S.look = Look.name(); S.terrain = buildTerrain(); S.spr = buildSprites(); S.dirty = true;
      if (!first) apply(S.t, true);
    },
    syncFollow() { S.follow = new Set(Desk.items.filter((i) => i.type === "coin").map((i) => i.coin)); S.dirty = true; },
    viewChanged() { S.t = Math.min(S.t, maxT()); apply(maxT(), true); },
    play(a, b, dur, done) { apply(a); if (reduced()) { apply(b); done && done(); return; } S.playing = { a, b, dur, t0: performance.now(), done }; UI.play(true); },
    setTime(t) { S.playing = null; UI.play(false); apply(t); },
    fly(to, dur = 1100, done) {
      const b = { x: to.x ?? S.cam.x, y: to.y ?? S.cam.y, z: to.z ?? S.cam.z };
      if (reduced()) { Object.assign(S.cam, b); clampCam(); S.dirty = true; done && done(); return; }
      S.flight = { a: { ...S.cam }, b, t0: performance.now(), dur, done };
    },
    flyHome(done) { this.fly({ x: phone() ? X(S.R.herd) - 20 : (MX0 + MX1) / 2, y: (MY0 + MY1) / 2 + 4, z: 1 }, 900, done); },
    flyTo(coin, z = 2, done) { const c = S.camps[coin]; this.fly({ x: c.x, y: c.y + 4, z }, 1200, done); },
    ring(k) { S.ring = k; S.dirty = true; UI.place(); },
    rideTo(coin) {
      const c = coin === "_fort" ? { x: FORT.x + 2, y: FORT.y + 2 } : coin === "_herd" ? { x: X(S.R.herd) + 8, y: (MY0 + MY1) / 2 } : S.camps[coin];
      S.outrider.tx = c.x + (coin.startsWith("_") ? 0 : -22); S.outrider.ty = c.y + (coin.startsWith("_") ? 0 : 2);
    },
    camp(coin) { return S.camps[coin]; },
    get t() { return S.t; },
    screenOf(coin) { const c = S.camps[coin]; return toScreen(c.cx, c.cy); },
  };
})();
