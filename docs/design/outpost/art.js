/* Pixel art for Outpost: icons, Outrider's portrait, the rider sprite and the 320x200 landscape.
   Everything is drawn at low resolution and scaled up with nearest-neighbour, so it stays sharp at any size. */
const Art = (() => {
  const mk = (w, h) => { const c = document.createElement("canvas"); c.width = w; c.height = h; return [c, c.getContext("2d")]; };
  const hex = (h) => { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
  const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));

  function grid(g, rows, pal, ox = 0, oy = 0) {
    rows.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) {
        const k = row[x];
        if (k === "." || !pal[k]) continue;
        g.fillStyle = pal[k];
        g.fillRect(ox + x, oy + y, 1, 1);
      }
    });
  }

  // a 16x16 rounded tile with a bevel, then a 12x12 glyph in the middle
  function tile(g, fill, light, shade, line) {
    const cut = new Set(["0,0", "1,0", "0,1", "14,0", "15,0", "15,1", "0,14", "0,15", "1,15", "15,14", "15,15", "14,15"]);
    const edge = new Set(["1,1", "14,1", "1,14", "14,14"]);
    for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
      const k = x + "," + y;
      if (cut.has(k)) continue;
      g.fillStyle = edge.has(k) || x === 0 || y === 0 || x === 15 || y === 15 ? line : y === 1 ? light : y === 14 ? shade : fill;
      g.fillRect(x, y, 1, 1);
    }
  }

  const GLYPHS = {
    dispatch: {
      tile: ["#2a2d6e", "#4a4ea8", "#1a1c48", "#07081c"],
      pal: { h: "#6a4428", a: "#ffb02e", s: "#f2c9a0", k: "#07081c", r: "#e0404e", w: "#ffe1b0" },
      rows: ["............", "....hhhh....", "...hhhhhh...", "...haaaah...", "hhhhhhhhhhhh", "...ssssss...",
        "...skssks...", "...rrrrrr...", "..rrrrrrrr..", "..rrwrrwrr..", "...rrrrrr...", "....rrrr...."],
    },
    herd: {
      tile: ["#2f73c9", "#5a9be6", "#1f4f94", "#0c1f40"],
      pal: { w: "#f6eedc", b: "#6b4226", k: "#140a04", m: "#d29a72" },
      rows: ["w..........w", "ww........ww", ".ww......ww.", "..wbbbbbbw..", "..bbbbbbbb..", ".bbkbbbbkbb.",
        "..bbbbbbbb..", "...bbbbbb...", "...mmmmmm...", "...mkmmkm...", "....mmmm....", "............"],
    },
    leverage: {
      tile: ["#3a2e4c", "#5a4a70", "#241c30", "#0c0812"],
      pal: { y: "#ffe14a", o: "#ff8a2a", f: "#e8dcc0", r: "#e8424e", R: "#a52834", t: "#c9b89a" },
      rows: [".......y....", "......yoy...", ".......y....", "......f.....", ".....f......", "..rr.rr.rr..",
        "..rr.rr.rr..", "..tttttttt..", "..rr.rr.rr..", "..rr.rr.rr..", "..rr.rr.rr..", "..RR.RR.RR.."],
    },
    map: {
      tile: ["#e3c48f", "#f6e2b8", "#b8945c", "#3a2a14"],
      pal: { k: "#3a2a14", r: "#d8374f", w: "#fffaf0" },
      rows: [".....kk.....", "....k..k....", "...k.rr.k...", "..k..rr..k..", ".k...rr...k.", "k...rrrr...k",
        "k...wwww...k", ".k...ww...k.", "..k..ww..k..", "...k.ww.k...", "....k..k....", ".....kk....."],
    },
    traders: {
      tile: ["#26306e", "#46529e", "#161d48", "#060a20"],
      pal: { g: "#ffcf4a", G: "#c9862a" },
      rows: [".....gg.....", ".....gg.....", "g...gggg...g", ".gggggggggg.", "..gggGGggg..", "...ggGGgg...",
        "...ggGGgg...", "..gggGGggg..", ".gggggggggg.", "g...gggg...g", ".....gg.....", ".....gg....."],
    },
    groups: {
      tile: ["#b8532e", "#de7a4e", "#83371c", "#2a0f04"],
      pal: { a: "#fff0d8" },
      rows: ["............", ".......a....", "aaaaaaaaa...", ".......a....", ".........a..", "..aaaaaaaaa.",
        ".........a..", "............", "......a.....", "aaaaaaaa....", "......a.....", "............"],
    },
    record: {
      tile: ["#356a58", "#4f8f78", "#21483b", "#08180f"],
      pal: { w: "#fbf6e8", l: "#9aa6b8", c: "#1f3a8a" },
      rows: ["..wwwwwwww..", "..wllllllw..", "..wwwwwwww..", "..wllllwww..", "..wwwwwwww..", "..wlllllww.c",
        "..wwwwwwwwcc", "..wllwwwwcc.", "..wwwcwwcc..", "..wwwccccw..", "..wwwwccww..", "..wwwwwwww.."],
    },
    lab: {
      tile: ["#6f5cc9", "#9483e6", "#4a3c94", "#170f38"],
      pal: { k: "#1a1430", w: "#d4ecff", b: "#ffffff", h: "#9a6630" },
      rows: ["..kkkk......", ".kwwwwk.....", "kwwbbwwk....", "kwbwwwwk....", "kwwwwwwk....", "kwwwwwwk....",
        ".kwwwwk.....", "..kkkkhh....", "......hhh...", ".......hhh..", "........hhh.", ".........hh."],
    },
    trash: {
      tile: null,
      pal: { k: "#1a120a", b: "#9a6436", B: "#6e4422", m: "#9aa2b4" },
      rows: ["..kkkkkkkk..", ".kbbbbbbbbk.", ".kmmmmmmmmk.", "kbbbbbbbbbbk", "kbBbbbbbBbbk", "kbBbbbbbBbbk",
        "kmmmmmmmmmmk", "kbBbbbbbBbbk", "kbBbbbbbBbbk", ".kmmmmmmmmk.", ".kbbbbbbbbk.", "..kkkkkkkk.."],
    },
  };

  const cache = {};
  function icon(name) {
    if (cache[name]) return cache[name];
    const gl = GLYPHS[name];
    const [c, g] = mk(16, 16);
    if (gl.tile) tile(g, ...gl.tile);
    grid(g, gl.rows, gl.pal, 2, 2);
    return (cache[name] = c.toDataURL());
  }

  function lock() {
    if (cache.lock) return cache.lock;
    const [c, g] = mk(8, 8);
    grid(g, ["..kkkk..", ".k....k.", ".k....k.", "kkkkkkkk", "kyyyyyyk", "kyykkyyk", "kyyyyyyk", "kkkkkkkk"],
      { k: "#1a1206", y: "#ffb02e" });
    return (cache.lock = c.toDataURL());
  }

  // Outrider: 24x24 portrait on a dithered night sky
  const FACE = [
    "........................", ".........KKKKKK.........", "........KhHHHHhK........", ".......KhhHHHHhhK.......",
    ".......KhhhhhhhhK.......", ".......KaaaaaaaaK.......", "..KKKKKhhhhhhhhhhKKKKK..", ".KhhhhhhhhhhhhhhhhhhhhK.",
    "..KKKKKKKKKKKKKKKKKKKK..", ".......KSSSSSSSSK.......", ".......KsKKssKKsK.......", ".......KssssssssK.......",
    ".......KrrrrrrrrK.......", "......KrrwrrrrwrrK......", "......KrrrrrrrrrrK......", ".......KrrwrrwrrK.......",
    "........KRrrrrRK........", "......ccCKRRRRKCcc......", "....cccccCKKKKCccccc....", "...ccccccccCCcccccccc...",
    "..cccccccccCCccccccccc..", "..cccccccccCCccccccccc..", ".ccccccccccCCcccccccccc.", ".ccccccccccCCcccccccccc.",
  ];
  const BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]].map((r) => r.map((v) => (v + 0.5) / 16));
  function face(blink = false) {
    const key = "face" + blink;
    if (cache[key]) return cache[key];
    const [c, g] = mk(24, 24);
    const sky = ["#0b0d2e", "#1a1f5a", "#3a2a6e", "#6a2f6a"].map(hex);
    for (let y = 0; y < 24; y++) for (let x = 0; x < 24; x++) {
      const u = (y / 23) * (sky.length - 1), i = Math.floor(u), f = u - i;
      const col = sky[Math.min(sky.length - 1, f > BAYER[y & 3][x & 3] ? i + 1 : i)];
      g.fillStyle = `rgb(${col})`; g.fillRect(x, y, 1, 1);
    }
    const rows = blink ? FACE.map((r, y) => (y === 10 ? ".......KssssssssK......." : r)) : FACE;
    grid(g, rows, { K: "#07081c", h: "#6a4428", H: "#8a5c36", a: "#ffb02e", s: "#f2c9a0", S: "#c9946c",
      r: "#e0404e", R: "#a52834", w: "#ffe1b0", c: "#3b4290", C: "#262b66" });
    return (cache[key] = c.toDataURL());
  }

  // identicon avatar for a trader (5x5 mirrored, from the address)
  function avatar(addr) {
    const key = "av" + addr;
    if (cache[key]) return cache[key];
    let h = 2166136261;
    for (const ch of addr) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0; }
    const hue = h % 360, hue2 = (hue + 150) % 360;
    const [c, g] = mk(7, 7);
    g.fillStyle = `hsl(${hue2} 30% 22%)`; g.fillRect(0, 0, 7, 7);
    g.fillStyle = `hsl(${hue} 70% 64%)`;
    let bits = h ^ 0x9e3779b9;
    for (let y = 0; y < 5; y++) for (let x = 0; x < 3; x++) {
      if (bits & 1) { g.fillRect(1 + x, 1 + y, 1, 1); g.fillRect(5 - x, 1 + y, 1, 1); }
      bits = (bits >>> 1) | ((bits & 1) << 31);
    }
    return (cache[key] = c.toDataURL());
  }

  // the rider (20x14, facing right), two walking frames
  const RIDER = [
    "........kkk.........", ".......kkkkk........", "......kkkkkkk.......", "........kk..........", "........kr..........",
    ".......kkkk......kk.", "......kkkkk.....kkkk", "......kkkkk....kkk..", "...kkkkkkkkkkkkkk...", "..kkkkkkkkkkkkkkk...",
    ".k.kkkkkkkkkkkkkk...",
  ];
  const LEGS = [["....kk.kk....kk.kk..", "....k...k....k...k..", "...k....k...k....k.."],
    ["....kk.kk....kk.kk..", ".....k.k......k.k...", ".....k..k.....k..k.."]];

  /* ---- the landscape ---- */
  const LOOKS = {
    night: { sky: ["#05061a", "#0d1240", "#1d1a58", "#3a2266", "#64306c"], cool: ["#3a4a8a", "#5a6aa8"], hot: ["#c8503e", "#f08a46"],
      far: "#3a2858", mid: "#221840", near: "#120c24", ground: "#0b0818", stars: ["#8a8ac8", "#c9c8f0", "#ffffff"],
      moon: ["#f3ead8", "#d6c8a8"], cactus: "#0a0716", rider: "#06040e" },
    dawn: { sky: ["#6f9fd6", "#9dbbe6", "#c9c6e6", "#efcfd4"], cool: ["#e8d6e0", "#f4e2dc"], hot: ["#ffc59a", "#ffb07a"],
      far: "#c79a8e", mid: "#9a6a5e", near: "#5e3a3a", ground: "#4a2c30", stars: [],
      moon: ["#fff4d0", "#ffd890"], cactus: "#3a2224", rider: "#2a1618" },
    vga: { sky: ["#000000", "#000055", "#0000aa", "#5555ff"], cool: ["#0000aa", "#5555ff"], hot: ["#aa00aa", "#ff55ff"],
      far: "#aa5500", mid: "#550055", near: "#000000", ground: "#000000", stars: ["#aaaaaa", "#ffffff", "#ffff55"],
      moon: ["#ffff55", "#aaaa00"], cactus: "#000000", rider: "#000000" },
  };

  function rng(seed) { let s = seed >>> 0; return () => { s = (s + 0x6d2b79f5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

  function scene(W, H, lookName, herd) {
    const L = LOOKS[lookName];
    const img = new ImageData(W, H), d = img.data;
    const put = (x, y, rgb) => { if (x < 0 || y < 0 || x >= W || y >= H) return; const i = (y * W + x) * 4; d[i] = rgb[0]; d[i + 1] = rgb[1]; d[i + 2] = rgb[2]; d[i + 3] = 255; };
    const k = Math.max(0, Math.min(1, (herd - 55) / 35));           // the sky warms as the herd leans long
    const glow = [mix(hex(L.cool[0]), hex(L.hot[0]), k), mix(hex(L.cool[1]), hex(L.hot[1]), k)];
    const ramp = [...L.sky.map(hex), ...glow];
    const hz = Math.round(H * 0.66);
    for (let y = 0; y < hz; y++) {
      const u = Math.pow(y / hz, 1.35) * (ramp.length - 1), i = Math.floor(u), f = u - i;
      for (let x = 0; x < W; x++) put(x, y, ramp[Math.min(ramp.length - 1, f > BAYER[y & 3][x & 3] ? i + 1 : i)]);
    }
    const R = rng(7);
    if (L.stars.length) {
      const n = Math.round(W * hz * 0.0035);
      for (let s = 0; s < n; s++) {
        const x = Math.floor(R() * W), y = Math.floor(Math.pow(R(), 1.6) * hz * 0.82);
        const c = hex(L.stars[Math.floor(R() * R() * L.stars.length * 1.4) % L.stars.length]);
        put(x, y, c);
        if (R() < 0.05) { put(x - 1, y, c); put(x + 1, y, c); put(x, y - 1, c); put(x, y + 1, c); }
      }
    }
    // moon (night) or sun (dawn), dithered shading
    const mx = Math.round(W * (lookName === "dawn" ? 0.7 : 0.71)), my = Math.round(lookName === "dawn" ? hz - 6 : H * 0.16), mr = lookName === "dawn" ? 15 : 10;
    const m0 = hex(L.moon[0]), m1 = hex(L.moon[1]);
    for (let y = -mr; y <= mr; y++) for (let x = -mr; x <= mr; x++) {
      const r2 = (x * x + y * y) / (mr * mr);
      if (r2 > 1) continue;
      const sh = (x + y) / (2 * mr) + 0.35 + r2 * 0.25;
      put(mx + x, my + y, sh > BAYER[(my + y) & 3][(mx + x) & 3] + 0.25 ? m1 : m0);
    }
    if (lookName !== "dawn") [[3, -2], [-4, 3], [1, 4], [-2, -5]].forEach(([a, b]) => put(mx + a, my + b, m1));

    // mesas: flat-topped buttes with talus slopes, three layers
    function layer(base, n, hMin, hMax, wMin, wMax, color, seed, rough) {
      const r = rng(seed), hgt = new Float32Array(W);
      for (let x = 0; x < W; x++) hgt[x] = rough * (Math.sin(x * 0.09 + seed) + Math.sin(x * 0.031 + seed * 2)) + rough;
      for (let i = 0; i < n; i++) {
        const cx = r() * W, w = wMin + r() * (wMax - wMin), h = hMin + r() * (hMax - hMin), slope = 2 + r() * 5;
        for (let x = Math.floor(cx - w / 2 - slope * 2); x < cx + w / 2 + slope * 2; x++) {
          if (x < 0 || x >= W) continue;
          const dx = Math.abs(x - cx) - w / 2;
          const v = dx <= 0 ? h : Math.max(0, h - Math.pow(dx / slope, 1.4) * 4);
          if (v > hgt[x]) hgt[x] = v;
        }
      }
      const c = hex(color);
      for (let x = 0; x < W; x++) for (let y = Math.round(base - hgt[x]); y < H; y++) put(x, y, c);
      return hgt;
    }
    layer(hz + 2, Math.round(W / 70), 8, 30, 14, 46, L.far, 11, 1.2);
    layer(hz + 12, Math.round(W / 55), 4, 13, 10, 40, L.mid, 23, 2.2);
    const nearBase = Math.round(H * 0.86);
    const ridge = new Int16Array(W);
    const nc = hex(L.near), gc = hex(L.ground);
    for (let x = 0; x < W; x++) {
      const t = x / W;
      ridge[x] = Math.round(nearBase - 6 * Math.exp(-Math.pow((t - 0.64) / 0.16, 2)) - 2 * Math.sin(x * 0.05) - 1.5 * Math.sin(x * 0.13 + 1));
      for (let y = ridge[x]; y < H; y++) put(x, y, (y - ridge[x]) / (H - ridge[x]) > BAYER[y & 3][x & 3] * 0.9 + 0.1 ? gc : nc);
    }
    // saguaro silhouettes
    const cc = hex(L.cactus);
    [[0.12, 16], [0.31, 10], [0.88, 13]].forEach(([fx, h]) => {
      const x = Math.round(W * fx), b = ridge[x];
      for (let y = 0; y < h; y++) { put(x, b - y, cc); put(x + 1, b - y, cc); }
      for (let y = 0; y < 5; y++) { put(x - 3, b - 5 - y, cc); put(x + 4, b - 7 - y, cc); }
      for (let i = 0; i < 3; i++) { put(x - 2 + 0 * i, b - 5, cc); put(x - 1, b - 5, cc); put(x + 2, b - 7, cc); put(x + 3, b - 7, cc); }
    });
    // pebbles on the plain
    const pr = rng(5);
    for (let i = 0; i < W * 0.6; i++) { const x = Math.floor(pr() * W), y = ridge[x] + 3 + Math.floor(pr() * (H - ridge[x] - 3)); put(x, y, nc); }
    return { img, ridge, riderColor: L.rider };
  }

  function drawRider(g, x, y, frame, color, scarf) {
    grid(g, RIDER, { k: color, r: scarf }, x, y);
    grid(g, LEGS[frame], { k: color }, x, y + RIDER.length);
  }

  return { icon, lock, face, avatar, scene, drawRider, GLYPHS, mk, RIDER_H: RIDER.length + 3, RIDER_W: 20 };
})();
