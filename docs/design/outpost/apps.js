/* Outpost apps: what each window shows, and the dispatch Outrider writes from the data. */
const D = window.OUTPOST_DATA;
const T = D.times, NOW = D.asof, N = T.length, H1 = 3600, DAY = 86400;
const ix = (t) => Math.max(0, Math.min(N - 1, TimeChart.idx(T, t)));
const P = (coin, t) => D.prices[coin][t == null ? N - 1 : ix(t)];

function fmtPx(p) {
  if (p == null) return "—";
  if (p >= 1000) return "$" + Math.round(p).toLocaleString("en-US");
  if (p >= 10) return "$" + p.toFixed(2);
  if (p >= 1) return "$" + p.toFixed(3);
  if (p >= 0.01) return "$" + p.toFixed(4);
  return "$" + p.toPrecision(3);
}
function usd(x) {
  const a = Math.abs(x), s = x < 0 ? "−" : "";
  if (a >= 1e9) return s + "$" + (a / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return s + "$" + (a / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (a >= 1e3) return s + "$" + Math.round(a / 1e3) + "K";
  return s + "$" + Math.round(a);
}
const pct = (x, d = 1) => (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(d) + "%";
const hhmm = (t) => { const d = new Date(t * 1000); return String(d.getUTCHours()).padStart(2, "0") + ":" + String(d.getUTCMinutes()).padStart(2, "0"); };
const day = (t, wd = true) => new Date(t * 1000).toLocaleDateString("en-GB", { ...(wd ? { weekday: "short" } : {}), day: "numeric", month: "short", timeZone: "UTC" });
const wday = (t) => new Date(t * 1000).toLocaleDateString("en-GB", { weekday: "long", timeZone: "UTC" });
const sideWord = (s) => (s === "long" ? "Long" : "Short");
const arrow = (s) => (s === "long" ? "▲" : "▼");
const ICON = window.OUTPOST_ICONS || {};
function coinIcon(coin, size = 30) {
  const ic = ICON[coin];
  return ic ? `<span class="cchip" style="width:${size}px;height:${size}px;background:${ic.chip}"><img src="${ic.src}" alt=""></span>` : "";
}
function coinTile(coin, size = 30) {
  if (ICON[coin]) return coinIcon(coin, size);
  let h = 0; for (const c of coin) h = (h * 31 + c.charCodeAt(0)) % 360;
  const fs = coin.length > 4 ? size * 0.26 : coin.length > 3 ? size * 0.3 : size * 0.36;
  return `<span class="tile ctile" style="--h:${h};width:${size}px;height:${size}px;font-size:${fs}px">${esc(coin.length > 5 ? coin.slice(0, 4) : coin)}</span>`;
}

/* ---------------- readings ---------------- */
const Herd = { now: () => D.crowd[N - 1], at: (t) => D.crowd[ix(t)] };
const [F0, F1] = D.flush;
const Flush = (() => {
  const i0 = ix(F0), i1 = ix(F1 + 12 * H1);
  let low = Infinity; for (let i = i0; i <= i1; i++) low = Math.min(low, D.oi[i]);
  const before = D.oi[i0], now = D.oi[N - 1];
  let peak = 0; for (let i = ix(F0); i <= ix(F1 + 30 * H1); i++) peak = Math.max(peak, D.crowd[i]);
  const sorted = [...D.oi].sort((a, b) => a - b), rank = sorted.findIndex((v) => v >= now) / sorted.length;
  return { before, low, now, cut: before - low, cutPct: (low / before - 1) * 100, back: (now - low) / (before - low),
    btc: (P("BTC", F1) / P("BTC", F0) - 1) * 100, crowdPeak: peak, rank, oi24: D.oi[ix(NOW - DAY)] };
})();
const fundNow = D.funding.BTC[D.funding.BTC.length - 1][1];
const fundMed = (() => { const v = D.funding.BTC.map((r) => r[1]).sort((a, b) => a - b); return v[Math.floor(v.length / 2)]; })();
const fundRel = () => Math.abs(fundNow - fundMed) < 2 ? "about its two-week norm" : `${fundNow > fundMed ? "above" : "below"} its two-week norm of ${fundMed.toFixed(0)}%`;

const Book = (() => {
  const all = [];
  for (const t of D.traders) for (const p of t.positions) {
    const now = P(p.coin), mv = p.side === "long" ? now / p.entry - 1 : p.entry / now - 1;
    all.push({ ...p, trader: t, move: mv * 100, pnl: p.usd * mv });
  }
  return all;
})();
const onCoin = (coin) => Book.filter((p) => p.coin === coin);
const recordLine = (t) => t.tier === "riding" ? `Riding a winner: ${usd(t.open_profit)} open profit` : `Won ${t.won} of ${t.closed} closed trades · ${usd(t.realized)} · $${t.per_loss} won per $1 lost`;

const Picks = {
  rows: null,
  list() {
    if (this.rows) return this.rows;
    const out = [];
    for (const [coin, L] of Object.entries(D.lenses)) {
      const pr = L.proven, side = pr.long_n >= pr.short_n ? "long" : "short";
      const heads = side === "long" ? pr.long_n : pr.short_n, against = side === "long" ? pr.short_n : pr.long_n;
      if (heads < 3) continue;
      const crowd = 100 * L.crowd.long_n / (L.crowd.long_n + L.crowd.short_n);
      const added = L.added_24h && L.added_24h.side === side ? L.added_24h : null;
      const contrary = side === "short" && crowd >= 65;
      const score = heads - against + (added ? 2 : 0) + (contrary ? 2 : 0);
      const pos = onCoin(coin).filter((p) => p.side === side);
      const w = pos.reduce((s, p) => s + p.usd, 0), avg = pos.reduce((s, p) => s + p.entry * p.usd, 0) / w;
      const move = side === "long" ? (P(coin) / avg - 1) * 100 : (avg / P(coin) - 1) * 100;
      const best = pos.map((p) => p.trader).filter((t) => t.tier === "proven").sort((a, b) => b.won / b.closed - a.won / a.closed)[0];
      const since = Math.min(...pos.map((p) => p.entry_ts));
      out.push({ coin, side, heads, against, crowd, added, contrary, score, avg, move, best, since, usd: w });
    }
    return (this.rows = out.sort((a, b) => b.score - a.score).slice(0, 3));
  },
};
const basket = (() => {
  const coins = [...new Set([...Picks.list().map((p) => p.coin), ...D.groups.map((g) => g.coin)])];
  return { coins, v: T.map((_, i) => 100 * Math.exp(coins.reduce((s, c) => s + Math.log(D.prices[c][i] / D.prices[c][0]), 0) / coins.length)) };
})();

/* ---------------- Outrider's voice ---------------- */
const Voice = {
  get c() { return App.voice === "char"; },
  herd(at = NOW) {
    const h = Herd.at(at), y = Herd.at(at - DAY), d = h - y;
    const chip = `<button type="button" class="ev" data-go="herd">${h.toFixed(0)}%</button>`;
    const move = Math.abs(d) < 0.5 ? `level with ${y.toFixed(0)}%` : `${d > 0 ? "up" : "down"} from ${y.toFixed(0)}%`;
    return this.c
      ? `The herd's leaning long: ${chip} of positions point up, ${move} this time yesterday. They're buying the slide, same as they did on ${wday(F0)}.`
      : `${chip} of positions held by profitable traders are long, ${move} 24 hours earlier, while prices fell.`;
  },
  leverage() {
    const chip = `<button type="button" class="ev" data-go="leverage">${usd(Flush.now)}</button>`;
    const half = Flush.back > 0.4 && Flush.back < 0.6 ? "about half" : Math.round(Flush.back * 100) + "%";
    return this.c
      ? `Leverage is creeping back. Open interest sits at ${chip}, ${half} of what ${wday(F0)}'s flush took off is back on. Funding's ${fundNow.toFixed(0)}% a year, ${fundRel()}, so longs ${fundNow - fundMed >= 2 ? "are paying up for it" : "aren't paying up for it yet"}.`
      : `Open interest across Hyperliquid's main markets is ${chip}: ${half} of the ${wday(F0)} drop has come back. Funding: longs pay ${fundNow.toFixed(0)}% a year, ${fundRel()}.`;
  },
  flush() {
    const chip = `<button type="button" class="ev" data-go="flush">${usd(Flush.cut)}</button>`;
    return this.c
      ? `${wday(F0)}'s flush: between ${hhmm(F0)} and ${hhmm(F1)} UTC, ${chip} of open interest got cut while BTC fell ${Math.abs(Flush.btc).toFixed(1)}%. The herd bought it, ${Flush.crowdPeak.toFixed(0)}% long by nightfall.`
      : `${day(F0)}, ${hhmm(F0)}–${hhmm(F1)} UTC: open interest fell ${chip} (${pct(Flush.cutPct, 0)}) as BTC fell ${Math.abs(Flush.btc).toFixed(1)}%. The share long rose to ${Flush.crowdPeak.toFixed(0)}% that evening.`;
  },
  short(p) {
    const lead = `<b>${p.heads} proven ${p.side}</b>, ${p.against ? p.against + " " + (p.side === "long" ? "short" : "long") : "none " + (p.side === "long" ? "short" : "long")}.`;
    if (p.contrary) return `${lead} Crowd <b>${p.crowd.toFixed(0)}% long</b>.`;
    if (p.added) return `${lead} <b>${usd(p.added.usd)}</b> added today.`;
    return lead;
  },
  three() { return this.c ? "Before you look at anything else, look at these three." : "Where proven traders are most committed now"; },
  pick(p) {
    const lead = `<b>${p.heads} proven traders ${p.side}</b>, ${p.against ? p.against + " the other way" : "none the other way"}.`;
    if (p.contrary) return `${lead} The crowd is <b>${p.crowd.toFixed(0)}% long</b>. ${pct(p.move)} their way since ${day(p.since)}.`;
    if (p.added) return `${lead} ${p.added.traders > 1 ? p.added.traders + " of them" : "One"} added <b>${usd(p.added.usd)}</b> in the last 24 hours.`;
    return `${lead} Best record among them: won ${p.best.won} of ${p.best.closed}.`;
  },
  s5() {
    return this.c
      ? "A herd this long hasn't meant tomorrow turns. I checked 374 days of it: the next day went up about half the time, same as any day."
      : "Days with 80% or more of positions long were followed by a down day no more often than other days (study S5, 374 days).";
  },
  note() {
    return this.c
      ? `${wday(F0)}'s flush cut ${usd(Flush.cut)} of open interest in five hours. About half of it is back on.`
      : `Flush, ${day(F0)}: open interest ${pct(Flush.cutPct, 0)} (${usd(Flush.cut)}) in five hours. About half has come back.`;
  },
  herdSay() {
    const h = Herd.now();
    return this.c
      ? `The herd buys the dips and trims into rallies. Watch the two lines move against each other: when price slid on ${wday(F0)}, the share long jumped to ${Flush.crowdPeak.toFixed(0)}%.`
      : `The share long tends to rise when price falls and ease when it rallies. After the ${day(F0)} drop it reached ${Flush.crowdPeak.toFixed(0)}%; it is ${h.toFixed(0)}% now.`;
  },
};

/* ---------------- desktop cards ---------------- */
const Apps = {
  dossier(coin) {
    const p = Picks.list().find((x) => x.coin === coin), L = D.lenses[coin];
    const side = p ? p.side : L.proven.long_n >= L.proven.short_n ? "long" : "short";
    const v = D.prices[coin].slice(-72), ch = (P(coin) / P(coin, NOW - DAY) - 1) * 100;
    const line = p ? Voice.short(p) : `<b>${L.proven.long_n} proven long</b>, ${L.proven.short_n} short.`;
    return `<div class="dossier"><header>${coinTile(coin)}<span class="who">${esc(coin)}</span><span class="side ${side}">${arrow(side)} ${sideWord(side)}</span></header>
      <p>${line}</p><footer><span class="num">${fmtPx(P(coin))}</span><span class="num">${pct(ch)}</span>${spark(v, 120, 26, `var(--${side === "long" ? "long" : "short"})`, { ref: p ? p.avg : null })}</footer></div>`;
  },
  traderCard(addr) {
    const t = D.traders.find((x) => x.address === addr);
    return `<div class="dossier"><header><img class="av" src="${Art.avatar(addr)}" alt="" style="width:30px;height:30px;image-rendering:pixelated;border-radius:6px"><span class="who" style="font-size:14px">${esc(t.name)}</span></header><p>${esc(recordLine(t))}</p>
      <footer>${t.positions.slice(0, 3).map((p) => `<span class="${p.side}">${arrow(p.side)} ${esc(p.coin)}</span>`).join(" ")}</footer></div>`;
  },
  widget(what) {
    if (what === "herd") {
      const h = Herd.now(), d = h - Herd.at(NOW - DAY);
      return `<div class="widget"><div class="w-top"><span>The herd</span><span>now</span></div><div class="w-big">${h.toFixed(0)}<span style="font-size:.5em">% long</span></div>
        <div class="w-sub"><span class="${d >= 0 ? "long" : "short"}">${d >= 0 ? "▲" : "▼"} ${Math.abs(d).toFixed(1)} pts</span> in 24h</div>${spark(D.crowd.slice(-72), 190, 34, "var(--long)", { ref: 80 })}</div>`;
    }
    if (what === "record") {
      const s = Record.stats();
      return `<div class="widget"><div class="w-top"><span>The record</span><span>public</span></div><div class="w-big">${s.hit}%</div><div class="w-sub">of ${s.n} calls went their way within 7 days. Misses included.</div></div>`;
    }
    if (what === "leverage") {
      return `<div class="widget"><div class="w-top"><span>Leverage</span><span>open interest</span></div><div class="w-big">${usd(Flush.now)}</div><div class="w-sub">${Math.round(Flush.back * 100)}% back since the flush</div>${spark(D.oi.slice(-120), 190, 34, "var(--accent)")}</div>`;
    }
    return "";
  },
};

/* ---------------- the record ---------------- */
const Record = {
  stats() {
    const r7 = D.record.filter((r) => r["7d"] != null), up = r7.filter((r) => r["7d"] > 0).length;
    const med = (a) => { const s = [...a].sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : 0; };
    return { n: r7.length, hit: Math.round(100 * up / Math.max(1, r7.length)), med: med(r7.map((r) => r["7d"])), all: D.record.length,
      med1: med(D.record.filter((r) => r["1d"] != null).map((r) => r["1d"])) };
  },
};

/* ---------------- windows ---------------- */
function rangeTabs(opts, cur) { return `<div class="tabs" role="group">${opts.map(([v, l]) => `<button type="button" data-v="${v}" aria-pressed="${v === cur}">${l}</button>`).join("")}</div>`; }
function wireTabs(host, onPick) {
  host.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-v]"); if (!b) return;
    $$("button", host).forEach((x) => x.setAttribute("aria-pressed", String(x === b))); onPick(b.dataset.v);
  });
}
const SPANS = { "24h": DAY, "3d": 3 * DAY, "7d": 7 * DAY, "14d": 14.9 * DAY };

const APPS = {
  dispatch: {
    name: "Dispatch", icon: "dispatch", w: 700, h: 780, public: true, dx: -40, left: 252,
    html() {
      const visitor = App.view === "visitor";
      const picks = Picks.list();
      const lines = [["herd", visitor ? Voice.herd(NOW - DAY) : Voice.herd()], ["leverage", Voice.leverage()], ["map", Voice.flush()]];
      const pickCards = picks.map((p, i) => visitor
        ? `<div class="pick" aria-label="Locked"><div class="top"><span class="tile ctile" style="--h:${i * 90};width:26px;height:26px">?</span><span class="c">Hidden</span></div><p>Proven traders' most committed coin, number ${i + 1}. Holders see it live.</p></div>`
        : `<button type="button" class="pick" data-coin="${p.coin}"><div class="top">${coinTile(p.coin, 26)}<span class="c">${p.coin}</span><span class="s ${p.side}">${arrow(p.side)} ${sideWord(p.side)}</span></div><p>${Voice.pick(p)}</p>${spark(D.prices[p.coin].slice(-96), 160, 30, `var(--${p.side})`, { ref: p.avg })}<span class="drag-hint">Avg entry ${fmtPx(p.avg)} · now ${fmtPx(P(p.coin))}</span></button>`).join("");
      const last = NOW - 2 * DAY, newG = D.groups.filter((g) => g.ts > last).length;
      const scored = D.record.filter((r) => [1, 3, 7].some((d) => r.ts + d * DAY > last && r.ts + d * DAY <= NOW)).length;
      return `<div class="dispatch">
        <div class="dsp-head"><img class="face" src="${Art.face()}" alt=""><div><div class="who">Outrider</div><div class="when">${visitor ? "Yesterday's dispatch" : "Dispatch"} · ${day(visitor ? NOW - DAY : NOW)}, ${hhmm(NOW)} UTC</div></div>
          <div class="voice">${rangeTabs([["char", "In character"], ["plain", "Plain"]], App.voice)}</div></div>
        <div class="row" style="margin-top:14px"><button type="button" class="btn primary" data-ride>Ride along on the map</button><span class="sub">Outrider takes you through it, about a minute.</span></div>
        <ul class="lines">${lines.map(([ic, txt]) => `<li><img class="mk" src="${Art.icon(ic)}" alt=""><p>${txt}</p></li>`).join("")}</ul>
        <div class="sect"><h3><img src="${Art.icon("traders")}" width="22" height="22" style="image-rendering:pixelated" alt="">${Voice.three()} ${info("Ranked by how many proven traders hold the same side (minus those holding the other side), plus fresh adding in the last 24 hours, plus proven traders standing against a crowd that leans the other way. A record of where they are, not a call.")}</h3>
          <div class="three">${pickCards}</div>
          ${visitor ? "" : `<div class="sub" style="margin-top:8px">Open one for the chart and who's in it. Drag one onto your desktop to keep it there.</div>`}</div>
        ${visitor ? `<div class="locked-band"><p><b>The live dispatch is for holders.</b> Visitors read yesterday's, a day late, and the public record.</p><button type="button" class="btn primary" data-unlock>Unlock live</button></div>` : ""}
        <div class="foot"><img class="mk" src="${Art.icon("lab")}" width="22" height="22" style="image-rendering:pixelated" alt=""><span>${Voice.s5()} <button type="button" class="ev" data-go="lab" style="font-family:var(--body)">The study</button></span></div>
        ${visitor ? "" : `<div class="since"><span class="sub" style="background:none;border:0;padding:5px 0">Since your last visit (${day(last)}):</span><span>Herd ${pct(Herd.now() - Herd.at(last), 0).replace("%", " pts")}</span><span>${newG} new group${newG === 1 ? "" : "s"}</span><span>${scored} calls scored</span><span>BTC ${pct((P("BTC") / P("BTC", last) - 1) * 100)}</span></div>`}
      </div>`;
    },
    mount(body) {
      wireTabs($(".voice", body), (v) => App.setVoice(v));
      $$(".pick[data-coin]", body).forEach((el) => draggable(el, () => ({ type: "coin", coin: el.dataset.coin, id: "c-" + el.dataset.coin }), () => WM.open("coin", el.dataset.coin, el.getBoundingClientRect())));
      body.addEventListener("click", (e) => {
        const go = e.target.closest("[data-go]");
        if (go) {
          const r = go.getBoundingClientRect(), g = go.dataset.go;
          if (g === "flush") { const w = WM.open("leverage", null, r); w?.api?.flush(); } else WM.open(g, null, r);
        }
        if (e.target.closest("[data-unlock]")) WM.open("unlock");
        if (e.target.closest("[data-ride]")) Tour.start();
      });
    },
  },

  herd: {
    name: "The Herd", icon: "herd", w: 760, h: 720, dx: 40, dy: 20, locked: "How the profitable traders' positions lean, long against short, beside price, every few minutes.",
    pin: () => ({ type: "widget", what: "herd", id: "w-herd" }),
    html() {
      const h = Herd.now(), d = h - Herd.at(NOW - DAY);
      return `<div class="lede"><div class="hero">${h.toFixed(0)}<small>% long</small></div><div class="delta ${d >= 0 ? "long" : "short"}">${d >= 0 ? "▲" : "▼"} ${Math.abs(d).toFixed(1)} pts in 24h</div>
          ${info("The share of all positions held by Reflex's profitable Hyperliquid traders that are long, by headcount: one trader, one vote, whatever their size.")}</div>
        <p class="say">${Voice.herdSay()}</p>
        <div class="row" style="justify-content:space-between">${rangeTabs([["24h", "24h"], ["3d", "3 days"], ["7d", "7 days"], ["14d", "14 days"]], "7d")}
          <div class="row" data-price>${rangeTabs([["btc", "BTC"], ["basket", "Alerted coins"]], "btc")}${info("Alerted coins: an equal-weight index of " + basket.coins.join(", ") + ", the coins Outrider flagged or saw groups form on, starting at 100.")}</div></div>
        <div class="chart" style="height:400px"></div>
        <div class="legend"><span><i style="border-color:var(--long)"></i>Share long</span><span><i class="band" style="background:color-mix(in srgb,var(--accent) 22%,transparent)"></i>Flush</span><span><i class="band" style="background:color-mix(in srgb,var(--short) 22%,transparent)"></i>Crowded: 80%+</span></div>
        <div class="chart-help"><span>Scroll or pinch to zoom</span><span>Drag to move through time</span><span>Double-click to reset</span></div>`;
    },
    mount(body, win) {
      const cfg = { label: "Share of positions long and price over time", initial: SPANS["7d"], events: [{ t0: F0, t1: F1, label: "Flush" }],
        panels: [
          { title: "Share of positions long", weight: 1.2, yfmt: (v) => v + "%", bands: [{ y0: 80, y1: 100, label: "Crowded", color: "--short" }],
            series: [{ label: "long", color: "--long", t: T, v: D.crowd, kind: "area", fmt: (v) => v.toFixed(1) + "%" }] },
          { title: "BTC price", yfmt: (v) => v >= 1000 ? (v / 1000).toFixed(1) + "K" : v.toFixed(0),
            series: [{ label: "BTC", color: "--price", t: T, v: D.prices.BTC, fmt: fmtPx },
              { label: "alerted coins (index)", color: "--price", t: T, v: basket.v, fmt: (v) => v.toFixed(1), hidden: true }] },
        ] };
      const ch = Charts.add(new TimeChart($(".chart", body), cfg)); win.charts.push(ch);
      wireTabs($(".tabs", body), (v) => ch.setRange(SPANS[v]));
      wireTabs($("[data-price] .tabs", body), (v) => {
        const p = cfg.panels[1]; p.series[0].hidden = v !== "btc"; p.series[1].hidden = v === "btc";
        p.title = v === "btc" ? "BTC price" : "Alerted coins, equal-weight index"; p.yfmt = v === "btc" ? cfg.panels[1].yfmt : (x) => x.toFixed(0); ch.draw();
      });
    },
  },

  leverage: {
    name: "Leverage", icon: "leverage", w: 760, h: 700, dx: 80, dy: 40, locked: "Open interest and funding, with every flush marked.",
    pin: () => ({ type: "widget", what: "leverage", id: "w-leverage" }),
    html() {
      const d = (Flush.now / Flush.oi24 - 1) * 100;
      return `<div class="lede"><div class="hero">${usd(Flush.now)}</div><div class="delta">open interest · ${pct(d)} in 24h</div>
          ${info("Open interest: the value of all open positions on Hyperliquid's main markets. Funding: what longs pay shorts to hold, shown per year (BTC).")}</div>
        <p class="say">${App.voice === "char"
          ? `${wday(F0)}'s flush cut ${usd(Flush.cut)} in five hours. ${Math.round(Flush.back * 100)}% of it has come back. Open interest now sits higher than on ${Math.round(Flush.rank * 100)}% of the hours in these two weeks.`
          : `Open interest fell ${usd(Flush.cut)} (${pct(Flush.cutPct, 0)}) on ${day(F0)} and has recovered ${Math.round(Flush.back * 100)}% of that. It is above ${Math.round(Flush.rank * 100)}% of hourly readings in the last 15 days.`}</p>
        <div class="row" style="justify-content:space-between">${rangeTabs([["3d", "3 days"], ["7d", "7 days"], ["14d", "14 days"]], "14d")}<button type="button" class="btn" data-flush style="height:32px">Zoom to the flush</button></div>
        <div class="chart" style="height:400px"></div>
        <div class="legend"><span><i style="border-color:var(--accent)"></i>Open interest</span><span><i style="border-color:var(--price)"></i>BTC funding, % a year</span><span><i class="band" style="background:color-mix(in srgb,var(--accent) 22%,transparent)"></i>Flush</span></div>
        <div class="sub" style="margin-top:10px">Funding is Hyperliquid's real data. Open interest history here is sample: the agent would record it every poll, one free call.</div>`;
    },
    mount(body, win) {
      const ft = D.funding.BTC.map((r) => r[0]), fv = D.funding.BTC.map((r) => r[1]);
      const cfg = { label: "Open interest and funding over time", initial: SPANS["14d"], events: [{ t0: F0, t1: F1, label: "Flush" }],
        panels: [
          { title: "Open interest", weight: 1.3, yfmt: (v) => "$" + (v / 1e9).toFixed(1) + "B", series: [{ label: "open interest", color: "--accent", t: T, v: D.oi, kind: "area", fmt: usd }] },
          { title: "BTC funding, % a year", zero: true, refs: [{ y: 0, color: "--ink-3" }], yfmt: (v) => v.toFixed(0) + "%", series: [{ label: "funding", color: "--price", t: ft.filter((t) => t >= T[0]), v: fv.slice(ft.findIndex((t) => t >= T[0])), fmt: (v) => v.toFixed(1) + "%" }] },
        ] };
      const ch = Charts.add(new TimeChart($(".chart", body), cfg)); win.charts.push(ch);
      wireTabs($(".tabs", body), (v) => ch.setRange(SPANS[v]));
      const flush = () => { ch.clampView(F0 - 30 * H1, F1 + 30 * H1); ch.draw(); $$(".tabs button", body).forEach((b) => b.setAttribute("aria-pressed", "false")); };
      $("[data-flush]", body).onclick = flush;
      win.api = { flush: () => setTimeout(flush, 60) };
    },
  },

  map: {
    name: "The Map", icon: "map", w: 820, h: 720, dx: 20, dy: 10, locked: "Every coin placed by how the crowd and the proven traders lean.",
    html() {
      return `<p class="say" style="margin-top:0">${App.voice === "char" ? "Every coin, placed by two crowds: across, how long the herd is; up, how long the proven traders are. The odd corners are where it gets interesting." : "Each coin: across, the crowd's share long; up, proven traders' share long. Bubble size: what proven traders hold in it."}
        ${info("Proven traders: wallets with 5+ positions held 3+ days that made money on closed trades. Crowd: all of Reflex's profitable traders. Coins with fewer than 2 proven positions sit out.")}</p>
        <div class="map"></div>
        <div class="legend"><span><i class="band" style="background:var(--long)"></i>Proven traders mostly long</span><span><i class="band" style="background:var(--short)"></i>Mostly short</span><span><i class="band" style="background:var(--ink-3)"></i>Split</span><span class="sub">Click a coin to open it. Drag it out to pin it.</span></div>`;
    },
    mount(body, win) {
      const host = $(".map", body);
      const draw = () => {
        const W = host.clientWidth, Hh = host.clientHeight; if (!W) return;
        const m = { l: 48, r: 18, t: 40, b: 40 }, X = (v) => m.l + (v - 55) / (92 - 55) * (W - m.l - m.r), Y = (v) => Hh - m.b - v / 100 * (Hh - m.t - m.b);
        const herd = Herd.now();
        const rows = Object.entries(D.lenses).map(([coin, L]) => ({ coin, L, cx: 100 * L.crowd.long_n / (L.crowd.long_n + L.crowd.short_n),
          n: L.proven.long_n + L.proven.short_n, py: 100 * L.proven.long_n / Math.max(1, L.proven.long_n + L.proven.short_n), usd: L.proven.long_usd + L.proven.short_usd }))
          .filter((r) => r.n >= 2).sort((a, b) => b.usd - a.usd);
        const maxU = Math.max(...rows.map((r) => r.usd));
        let svg = `<svg viewBox="0 0 ${W} ${Hh}" role="img" aria-label="Map of coins by crowd and proven traders' lean">`;
        svg += `<rect x="${X(herd)}" y="${m.t}" width="${W - m.r - X(herd)}" height="${Y(50) - m.t}" fill="var(--long)" opacity=".05"/>`;
        svg += `<rect x="${X(herd)}" y="${Y(50)}" width="${W - m.r - X(herd)}" height="${Hh - m.b - Y(50)}" fill="var(--short)" opacity=".07"/>`;
        for (const v of [60, 70, 80, 90]) svg += `<line x1="${X(v)}" x2="${X(v)}" y1="${m.t}" y2="${Hh - m.b}" stroke="var(--grid)"/><text class="ax" x="${X(v)}" y="${Hh - m.b + 18}" text-anchor="middle">${v}%</text>`;
        for (const v of [0, 25, 50, 75, 100]) svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--grid)"/><text class="ax" x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${v}%</text>`;
        svg += `<line x1="${X(herd)}" x2="${X(herd)}" y1="${m.t - 6}" y2="${Hh - m.b}" stroke="var(--ink-3)" stroke-dasharray="4 4"/><text class="ax" x="${X(herd) + 6}" y="${m.t - 10}">Whole market ${herd.toFixed(0)}%</text>`;
        svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(50)}" y2="${Y(50)}" stroke="var(--ink-3)" stroke-dasharray="4 4"/>`;
        const quads = [[W - m.r - 6, m.t + 16, "end", "Everyone long"], [m.l + 8, m.t + 16, "start", "Pros long, crowd lighter"],
          [W - m.r - 6, Hh - m.b - 10, "end", "Pros short, crowd long"], [m.l + 8, Hh - m.b - 10, "start", "Pros short, crowd lighter"]];
        svg += `<text class="ax" x="${(W + m.l) / 2}" y="${Hh - 6}" text-anchor="middle">The crowd's share long →</text>`;
        svg += `<text class="ax" transform="translate(13 ${(Hh - m.b + m.t) / 2}) rotate(-90)" text-anchor="middle">Proven traders' share long →</text>`;
        // place, then nudge overlapping bubbles apart (vertically first, so the crowd reading stays put)
        const B = rows.map((r) => {
          const rad = 7 + 24 * Math.sqrt(r.usd / maxU);
          return { r, rad, x: X(Math.max(55.5, Math.min(91.5, r.cx))), y: Math.max(m.t + rad, Math.min(Hh - m.b - rad, Y(r.py))) };
        });
        for (let it = 0; it < 120; it++) {
          for (let i = 0; i < B.length; i++) for (let j = i + 1; j < B.length; j++) {
            const a = B[i], b = B[j], dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 0.01, need = a.rad + b.rad + 5;
            if (d >= need) continue;
            const push = (need - d) / 2, ux = dx / d, uy = dy / d;
            a.x -= ux * push * 0.35; b.x += ux * push * 0.35; a.y -= uy * push + (dy === 0 ? push : 0); b.y += uy * push;
          }
          for (const q of B) { q.y = Math.max(m.t + q.rad, Math.min(Hh - m.b - q.rad, q.y)); q.x = Math.max(m.l + q.rad, Math.min(W - m.r - q.rad - 40, q.x)); }
        }
        // labels: try right, left, above, below; skip spots that hit a bubble or another label
        const boxes = [], hits = (bx) => boxes.some((o) => bx.x < o.x + o.w && bx.x + bx.w > o.x && bx.y < o.y + o.h && bx.y + bx.h > o.y)
          || B.some((o) => { const cx = Math.max(bx.x, Math.min(o.x, bx.x + bx.w)), cy = Math.max(bx.y, Math.min(o.y, bx.y + bx.h)); return Math.hypot(o.x - cx, o.y - cy) < o.rad + 1; });
        for (const q of B) {
          if (q.rad >= 20 && !ICON[q.r.coin]) continue;
          const w = q.r.coin.length * 8.6 + 4, h = 15;
          const cands = [[q.x + q.rad + 5, q.y - 7.5], [q.x - q.rad - 5 - w, q.y - 7.5], [q.x - w / 2, q.y - q.rad - 4 - h], [q.x - w / 2, q.y + q.rad + 4]];
          const pick = cands.map(([x0, y0]) => ({ x: x0, y: y0, w, h })).find((bx) => !hits(bx) && bx.x > m.l && bx.x + bx.w < W - 2 && bx.y > m.t - 14 && bx.y + bx.h < Hh - m.b) || { x: cands[0][0], y: cands[0][1], w, h };
          boxes.push(pick); q.lab = pick;
        }
        for (const [qx, qy, anchor, txt] of quads) {
          const w = txt.length * 7.4, bx = { x: anchor === "end" ? qx - w : qx, y: qy - 11, w, h: 14 };
          if (!hits(bx)) svg += `<text class="quad" x="${qx}" y="${qy}" text-anchor="${anchor}">${txt}</text>`;
        }
        for (const q of B) {
          const { r, rad, x, y } = q, col = r.py >= 60 ? "var(--long)" : r.py <= 40 ? "var(--short)" : "var(--ink-3)";
          const cv = getComputedStyle(document.documentElement).getPropertyValue(col.slice(4, -1)).trim(), inside = rad >= 20 && !ICON[r.coin], label = inside ? `<text x="${x}" y="${y + 4.5}" text-anchor="middle" style="fill:${TimeChart.onColor(cv)}">${r.coin}</text>` : `<text x="${q.lab.x + 2}" y="${q.lab.y + 11.5}">${r.coin}</text>`;
          svg += `<g class="bub" data-coin="${r.coin}" tabindex="0" role="button" aria-label="${r.coin}: crowd ${r.cx.toFixed(0)}% long, proven traders ${r.L.proven.long_n} long ${r.L.proven.short_n} short"><circle cx="${x}" cy="${y}" r="${rad + 10}" fill="transparent"/><circle cx="${x}" cy="${y}" r="${rad + 2}" fill="var(--win)"/><circle class="fill" cx="${x}" cy="${y}" r="${rad}" fill="${col}" fill-opacity=".9"/>${ICON[r.coin] ? (() => { const ri = Math.max(5, rad - Math.max(3, rad * 0.22)), id = "cl" + r.coin; return `<clipPath id="${id}"><circle cx="${x}" cy="${y}" r="${ri}"/></clipPath><circle cx="${x}" cy="${y}" r="${ri}" fill="${ICON[r.coin].chip === "transparent" ? "var(--win)" : ICON[r.coin].chip}"/><image href="${ICON[r.coin].src}" x="${x - ri}" y="${y - ri}" width="${ri * 2}" height="${ri * 2}" clip-path="url(#${id})" preserveAspectRatio="xMidYMid meet"/>`; })() : ""}${label}</g>`;
        }
        host.innerHTML = svg + "</svg>";
        $$(".bub", host).forEach((g) => {
          const coin = g.dataset.coin, L = D.lenses[coin];
          const tip = () => `<div class="lab">${coin}</div><div><span class="v">${(100 * L.crowd.long_n / (L.crowd.long_n + L.crowd.short_n)).toFixed(0)}%</span> <span class="lab">of the crowd long</span></div><div><span class="v">${L.proven.long_n} long · ${L.proven.short_n} short</span> <span class="lab">proven traders</span></div><div><span class="v">${usd(L.proven.long_usd + L.proven.short_usd)}</span> <span class="lab">they hold</span></div>`;
          g.addEventListener("pointermove", (e) => Tip.show(tip(), e.clientX, e.clientY));
          g.addEventListener("pointerleave", () => Tip.hide());
          g.addEventListener("keydown", (e) => { if (e.key === "Enter") WM.open("coin", coin, g.getBoundingClientRect()); });
          draggable(g, () => ({ type: "coin", coin, id: "c-" + coin }), () => { Tip.hide(); WM.open("coin", coin, g.getBoundingClientRect()); });
        });
      };
      const ro = new ResizeObserver(draw); ro.observe(host);
      win.charts.push({ draw, destroy: () => ro.disconnect() }); Charts.add(win.charts[win.charts.length - 1]);
    },
  },

  coin: {
    name: "Coin", icon: "map", dockAs: "map", w: 780, h: 780, dx: 60, dy: 30, locked: "Price with every proven trader's entry, the three lenses and who's in.",
    title: (c) => c, titleIcon: (c) => ICON[c], pin: (c) => ({ type: "coin", coin: c, id: "c-" + c }),
    html(coin) {
      const L = D.lenses[coin], pos = onCoin(coin), ch = (P(coin) / P(coin, NOW - DAY) - 1) * 100;
      const crowd = 100 * L.crowd.long_n / (L.crowd.long_n + L.crowd.short_n);
      const p = Picks.list().find((x) => x.coin === coin);
      const lens = (nm, l, s, tip, extra) => { const sh = 100 * l / Math.max(1, l + s); return `<div class="lens"><span class="nm">${nm} ${info(tip)}</span><div class="bar" role="img" aria-label="${l} long, ${s} short"><i style="width:${sh}%"></i><i style="width:${100 - sh}%"></i></div><span class="val"><span class="long">${l}</span> · <span class="short">${s}</span>${extra || ""}</span></div>`; };
      const say = pos.length
        ? `${L.proven.long_n} proven trader${L.proven.long_n === 1 ? " is" : "s are"} long ${coin} and ${L.proven.short_n} short, holding ${usd(L.proven.long_usd + L.proven.short_usd)} between them. The crowd is ${crowd.toFixed(0)}% long.${p ? " " + (p.added ? `${p.added.traders > 1 ? p.added.traders + " of them" : "One"} added ${usd(p.added.usd)} in the last 24 hours.` : "") : ""}`
        : `No proven trader holds ${coin} now. The crowd is ${crowd.toFixed(0)}% long.`;
      return `<div class="row">${coinTile(coin, 48)}<div><div class="hero coinname">${esc(coin)}</div><div class="sub"><span class="num" style="color:var(--ink)">${fmtPx(P(coin))}</span> · <span class="num">${pct(ch)}</span> in 24h · funding ${L.funding_apr.toFixed(0)}% a year</div></div></div>
        <p class="say">${say}</p>
        <div class="row" style="justify-content:space-between">${rangeTabs([["3d", "3 days"], ["7d", "7 days"], ["14d", "14 days"]], "7d")}</div>
        <div class="chart" style="height:340px"></div>
        <div class="legend"><span><i style="border-color:var(--price)"></i>Price</span><span><i class="band" style="background:var(--long);border-radius:50%;width:10px"></i>A proven trader went long</span><span><i class="band" style="background:var(--short);border-radius:50%;width:10px"></i>Went short</span><span><i class="dash" style="border-color:var(--ink-3)"></i>Their average entry</span></div>
        <div class="sect"><h3>Three lenses</h3>
          ${lens("Crowd", L.crowd.long_n, L.crowd.short_n, "All of Reflex's profitable Hyperliquid traders holding this coin: how many are long and short.")}
          ${lens("Proven", L.proven.long_n, L.proven.short_n, "Traders with 5+ positions held 3+ days that made money on closed trades.", "")}
          ${lens("Big money", L.big.long_n, L.big.short_n, "Accounts with $1M+ in them, counted once per owner.")}</div>
        <div class="sect"><h3>Who's in</h3><div class="list"><div class="r h"><span></span><span>Trader</span><span>Side</span><span class="num">Since entry</span><span class="num">Size</span></div>
          ${pos.sort((a, b) => b.usd - a.usd).map((q) => `<div class="r" data-addr="${q.trader.address}"><img class="av" src="${Art.avatar(q.trader.address)}" alt=""><span><div class="n">${esc(q.trader.name)}</div><div class="d">${esc(recordLine(q.trader))}</div></span><span class="${q.side}">${arrow(q.side)} ${sideWord(q.side)} · ${day(q.entry_ts, false)}</span><span class="num ${q.move >= 0 ? "long" : "short"}">${pct(q.move)}</span><span class="num">${usd(q.usd)}</span></div>`).join("") || `<div class="sub" style="padding:10px 0">No proven trader holds it now.</div>`}</div></div>
        <div class="row" style="margin-top:20px"><a class="btn primary" href="https://app.hyperliquid.xyz/trade/${encodeURIComponent(coin)}" target="_blank" rel="noopener">Open ${esc(coin)} on Hyperliquid ↗</a><button type="button" class="btn" data-pinme>Pin to desktop</button></div>`;
    },
    mount(body, win) {
      const coin = win.arg, pos = onCoin(coin);
      const maxU = Math.max(1, ...pos.map((p) => p.usd));
      const dom = D.lenses[coin].proven.long_n >= D.lenses[coin].proven.short_n ? "long" : "short";
      const ds = pos.filter((p) => p.side === dom), avg = ds.reduce((s, p) => s + p.entry * p.usd, 0) / Math.max(1, ds.reduce((s, p) => s + p.usd, 0));
      const cfg = { label: coin + " price with proven traders' entries", initial: SPANS["7d"], events: [{ t0: F0, t1: F1, label: "Flush" }],
        markers: pos.map((p) => ({ t: p.entry_ts, v: p.entry, panel: 0, r: 4 + 7 * Math.sqrt(p.usd / maxU), color: p.side === "long" ? "--long" : "--short",
          html: `<div class="lab">${esc(p.trader.name)}</div><div><span class="v">${sideWord(p.side)} ${usd(p.usd)}</span> <span class="lab">at ${fmtPx(p.entry)}</span></div><div class="lab">${day(p.entry_ts)}, ${hhmm(p.entry_ts)} UTC · ${pct(p.move)} since</div>`,
          onClick: () => WM.open("trader", p.trader.address) })),
        panels: [{ title: coin + " price", yfmt: (v) => fmtPx(v).replace("$", ""), refs: ds.length ? [{ y: avg, label: "Average entry " + fmtPx(avg) }] : [],
          series: [{ label: coin, color: "--price", t: T, v: D.prices[coin], kind: "area", fmt: fmtPx }] }] };
      const ch = Charts.add(new TimeChart($(".chart", body), cfg)); win.charts.push(ch);
      wireTabs($(".tabs", body), (v) => ch.setRange(SPANS[v]));
      $$(".r[data-addr]", body).forEach((r) => draggable(r, () => ({ type: "trader", address: r.dataset.addr, id: "t-" + r.dataset.addr }), () => WM.open("trader", r.dataset.addr, r.getBoundingClientRect())));
      $("[data-pinme]", body).onclick = () => { Desk.add({ type: "coin", coin, id: "c-" + coin }); toast(`Pinned ${coin} to your desktop`); };
    },
  },

  traders: {
    name: "Traders", icon: "traders", w: 760, h: 700, dx: 30, dy: 30, locked: "Every proven trader, their record in plain words and what they hold.",
    html(_, win) {
      const tab = win.tab || "proven", list = D.traders.filter((t) => t.tier === tab);
      return `<p class="say" style="margin-top:0">${App.voice === "char" ? "The riders worth watching. Proven means they've made money on trades held three days or more, misses counted." : "Proven traders: 5+ positions held 3+ days, profitable on closed trades. Riding: holding big open winners, not yet proven on closed trades."}</p>
        ${rangeTabs([["proven", `Proven (${D.traders.filter((t) => t.tier === "proven").length})`], ["riding", `Riding a winner (${D.traders.filter((t) => t.tier === "riding").length})`]], tab)}
        <div class="list" style="margin-top:10px"><div class="r h"><span></span><span>Trader</span><span>Holds now</span><span class="num">Open P/L</span><span class="num">Account</span></div>
        ${list.sort((a, b) => (b.realized || b.open_profit) - (a.realized || a.open_profit)).map((t) => {
          const open = Book.filter((p) => p.trader === t).reduce((s, p) => s + p.pnl, 0);
          return `<div class="r" data-addr="${t.address}"><img class="av" src="${Art.avatar(t.address)}" alt=""><span><div class="n">${esc(t.name)}</div><div class="d">${esc(recordLine(t))}</div></span><span class="d" style="font-size:13px">${t.positions.slice(0, 3).map((p) => `<span class="${p.side}">${arrow(p.side)}${esc(p.coin)}</span>`).join(" ")}${t.positions.length > 3 ? " +" + (t.positions.length - 3) : ""}</span><span class="num ${open >= 0 ? "long" : "short"}">${usd(open)}</span><span class="num">${usd(t.equity)}</span></div>`;
        }).join("")}</div><div class="sub" style="margin-top:8px">Open a trader for what they hold. Drag one onto the desktop to keep an eye on them.</div>`;
    },
    mount(body, win) {
      wireTabs($(".tabs", body), (v) => { win.tab = v; WM.render(win); });
      $$(".r[data-addr]", body).forEach((r) => draggable(r, () => ({ type: "trader", address: r.dataset.addr, id: "t-" + r.dataset.addr }), () => WM.open("trader", r.dataset.addr, r.getBoundingClientRect())));
    },
  },

  trader: {
    name: "Trader", icon: "traders", dockAs: "traders", w: 640, h: 620, dx: 120, dy: 60,
    title: (a) => (D.traders.find((t) => t.address === a) || {}).name || "Trader", pin: (a) => ({ type: "trader", address: a, id: "t-" + a }),
    html(addr) {
      const t = D.traders.find((x) => x.address === addr), pos = Book.filter((p) => p.trader === t);
      return `<div class="row"><img src="${Art.avatar(addr)}" width="56" height="56" style="image-rendering:pixelated;border-radius:10px" alt=""><div><div class="hero" style="font-size:30px">${esc(t.name)}</div><div class="sub mono">${addr.slice(0, 6)}…${addr.slice(-4)} · ${t.tier === "proven" ? "Proven" : "Riding a winner"}</div></div></div>
        <p class="say">${esc(recordLine(t))}. Usually holds about ${t.hold_days} days.</p>
        <div class="sect"><h3>Holds now</h3><div class="list"><div class="r h"><span></span><span>Coin</span><span>Side</span><span class="num">Since entry</span><span class="num">Size</span></div>
        ${pos.map((p) => `<div class="r" data-coin="${p.coin}">${coinTile(p.coin, 28)}<span><div class="n">${p.coin}</div><div class="d">from ${fmtPx(p.entry)} · ${day(p.entry_ts)}</div></span><span class="${p.side}">${arrow(p.side)} ${sideWord(p.side)}</span><span class="num ${p.move >= 0 ? "long" : "short"}">${pct(p.move)}</span><span class="num">${usd(p.usd)}</span></div>`).join("")}</div></div>`;
    },
    mount(body) { $$(".r[data-coin]", body).forEach((r) => draggable(r, () => ({ type: "coin", coin: r.dataset.coin, id: "c-" + r.dataset.coin }), () => WM.open("coin", r.dataset.coin, r.getBoundingClientRect()))); },
  },

  groups: {
    name: "Groups", icon: "groups", w: 760, h: 680, dx: 0, dy: 50, locked: "When proven traders open the same side within hours, followed trader by trader until they're out.",
    html() {
      return `<p class="say" style="margin-top:0">${App.voice === "char" ? "When two or more proven traders open the same side within six hours, I follow every one of them until they're out." : "A group forms when 2+ proven traders open the same side on one coin within 6 hours. Each is followed until they close."}
        ${info("Study S1 found groups did no better than a single trader opening alone. They are shown to follow who did what, not as a signal.")}</p>
        <div class="groups">${D.groups.map((g) => {
          const t1 = NOW, span = t1 - g.ts + 6 * H1, x = (t) => Math.max(0, Math.min(100, (t - g.ts + 3 * H1) / span * 100));
          const res = g.members.map((m) => { const e = m.exit ?? P(g.coin); return (g.side === "long" ? e / m.entry - 1 : m.entry / e - 1) * 100; });
          const avg = res.reduce((s, v) => s + v, 0) / res.length, open = g.members.filter((m) => !m.exit_ts).length;
          return `<div class="grp" data-coin="${g.coin}"><header>${coinTile(g.coin, 24)}<b>#${g.id} ${g.coin} ${g.side}</b><span class="sub">${g.members.length} traders · formed ${day(g.ts)}, ${hhmm(g.ts)} UTC · ${open === g.members.length ? "all still in" : open ? open + " still in" : "all out"}</span><span class="num ${avg >= 0 ? "long" : "short"}" style="margin-left:auto">${pct(avg)} avg</span></header>
            <div style="margin-top:8px">${g.members.map((m, i) => `<div class="lane"><span class="nm">${esc(m.name)}</span><span class="track"><i class="${m.exit_ts ? "" : "open"}" style="left:${x(m.entry_ts)}%;width:${Math.max(1, x(m.exit_ts || t1) - x(m.entry_ts))}%;background:var(--${g.side})"></i></span><span class="num ${res[i] >= 0 ? "long" : "short"}" style="text-align:right">${m.exit_ts ? "" : "open "}${pct(res[i])}</span></div>`).join("")}</div></div>`;
        }).join("")}</div>`;
    },
    mount(body) { $$(".grp", body).forEach((g) => { g.style.cursor = "pointer"; g.addEventListener("click", () => WM.open("coin", g.dataset.coin, g.getBoundingClientRect())); }); },
  },

  record: {
    name: "The Record", icon: "record", w: 760, h: 700, public: true, dx: 50, dy: 0, pin: () => ({ type: "widget", what: "record", id: "w-record" }),
    html() {
      const s = Record.stats();
      return `<p class="say" style="margin-top:0">${App.voice === "char" ? "Every call I log is written down before the outcome, scored at 1, 3 and 7 days, and chained so nobody can edit it afterwards. Misses stay in." : "Every logged call, scored at 1, 3 and 7 days. Each entry is hash-chained to the one before it, so none can be changed or removed later."}</p>
        <div class="scoreboard"><div><b>${s.all}</b><span>calls logged since ${day(D.record[D.record.length - 1].ts, false)}</span></div><div><b>${s.hit}%</b><span>went their way within 7 days (${s.n} scored)</span></div><div><b class="${s.med >= 0 ? "long" : "short"}">${pct(s.med)}</b><span>median move their way at 7 days</span></div></div>
        <div class="row" style="justify-content:space-between;margin:14px 0 4px"><span class="sub">Open to everyone, a day late for non-holders.</span><button type="button" class="btn" data-verify style="height:32px">Check the chain</button></div>
        <div class="list"><div class="r h"><span></span><span>Call</span><span class="num">1 day</span><span class="num">3 days</span><span class="num">7 days</span></div>
        ${D.record.slice(0, 18).map((r) => `<div class="r" data-coin="${r.coin}">${coinTile(r.coin, 28)}<span><div class="n">#${r.id} ${r.coin} <span class="${r.side}">${sideWord(r.side).toLowerCase()}</span></div><div class="d">${r.kind} · ${day(r.ts)} · <span class="chain">${r.hash.slice(0, 10)}</span></div></span>${["1d", "3d", "7d"].map((h) => r[h] == null ? `<span class="num sub">due</span>` : `<span class="num ${r[h] >= 0 ? "long" : "short"}">${pct(r[h])}</span>`).join("")}</div>`).join("")}</div>`;
    },
    mount(body) {
      $("[data-verify]", body).onclick = (e) => {
        const b = e.currentTarget; b.disabled = true; b.textContent = "Checking 1 of " + D.record.length;
        let i = 0; const t = setInterval(() => { i += 3; b.textContent = `Checking ${Math.min(i, D.record.length)} of ${D.record.length}`; if (i >= D.record.length) { clearInterval(t); b.textContent = "Chain intact: every entry matches"; } }, 60);
      };
      $$(".r[data-coin]", body).forEach((r) => r.addEventListener("click", () => App.view === "visitor" ? WM.open("unlock") : WM.open("coin", r.dataset.coin, r.getBoundingClientRect())));
    },
  },

  lab: {
    name: "The Lab", icon: "lab", w: 720, h: 700, public: true, dx: 90, dy: 20,
    html() {
      const ANS = { context: "Context", promising: "Promising" };
      return `<p class="say" style="margin-top:0">${App.voice === "char" ? "Every hunch gets tested before it's trusted. The rules are written down first; most of the answers are no." : "Each study's rules are declared before any result is computed. Until a result meets its bar, the reading is context, not a signal."}</p>
        <div class="studies">${D.studies.map((s) => {
          const bars = s.bars || [], mx = Math.max(1, ...bars.map((b) => Math.abs(b.value)));
          const parts = s.finding.split(/(?<=\.)\s+/), short = parts[0].split(" ").length <= 3;
          const ans = short ? parts[0] : (ANS[s.verdict] || s.verdict) + ".", rest = short ? parts.slice(1).join(" ") : s.finding;
          return `<div class="study"><div class="q">${esc(s.title)}</div><div class="a"><span class="ans">${esc(ans)}</span><span class="sub">${short ? (ANS[s.verdict] || s.verdict) + " · " : ""}${s.id}</span></div>
            <p style="margin-top:6px">${esc(rest)}</p>
            ${bars.length ? `<div class="sbars">${bars.map((b) => `<div class="sb"><span>${esc(b.label)}${b.n ? ` <span class="sub">(${b.n})</span>` : ""}</span><span class="trk"><i style="${b.value >= 0 ? "left:50%" : `right:50%`};width:${Math.abs(b.value) / mx * 50}%;background:var(--ink-2)"></i></span><span class="num" style="text-align:right">${pct(b.value, 2)}</span></div>`).join("")}</div>${s.unit ? `<div class="sub" style="margin-top:6px">${esc(s.unit)}</div>` : ""}` : ""}</div>`;
        }).join("")}</div>`;
    },
  },

  about: {
    name: "About Outpost", icon: "dispatch", w: 520, h: 520, public: true, dx: 120, dy: 60,
    html() {
      return `<div class="row"><img src="${Art.face()}" width="72" height="72" style="image-rendering:pixelated;border-radius:12px" alt=""><div><div class="hero" style="font-size:34px">Outpost</div><div class="sub">Outrider's desktop · prototype</div></div></div>
        <p class="say">Outrider rides ahead of the herd and reports what he sees. The desktop is the territory he scouts: every camp a coin, every rider a proven trader's position, the cattle the crowd, the dust the leverage. Ride along with him, or drag the timeline back and ride the last two weeks yourself.</p>
        <p class="say">The light follows the herd. It runs warmer as more positions lean long, cooler as they lean short.</p>
        <p class="sub">In this prototype, prices and funding are real Hyperliquid data from 13 October 2026. Positions, traders, groups, open interest history and the record are sample data shaped like the agent's. The token name (RIDE) and threshold are placeholders.</p>`;
    },
  },

  unlock: {
    name: "Unlock Outpost", icon: "dispatch", w: 500, h: 470, public: true, dx: 100, dy: 40,
    html() {
      return `<div style="text-align:center;display:grid;gap:14px;justify-items:center;padding-top:8px"><img src="${Art.face()}" width="80" height="80" style="image-rendering:pixelated;border-radius:14px" alt="">
        <div class="hero" style="font-size:30px">Ride with Outrider</div>
        <p class="say" style="margin:0">Hold 10,000 RIDE in your wallet and the whole desktop opens: the live dispatch, the three coins to look at first, the herd, the map, every proven trader and every group.</p>
        <div class="row" style="justify-content:center"><button type="button" class="btn primary" data-connect>Connect wallet</button><button type="button" class="btn" data-buy>Get RIDE</button></div>
        <p class="sub" style="margin:0">Visitors keep the record, the lab and yesterday's dispatch. Nothing to sign up for: you sign one message to show the wallet is yours.</p></div>`;
    },
    mount(body) {
      $("[data-connect]", body).onclick = () => { App.setView("holder"); WM.close("unlock"); toast("Signed in: 12,400 RIDE. The desktop is open."); };
      $("[data-buy]", body).onclick = () => toast("The token isn't live yet");
    },
  },
};

/* ---------------- the app ---------------- */
const App = {
  voice: Store.get("voice", "char"),
  view: Store.get("view", "holder"),
  setVoice(v) { this.voice = v; Store.set("voice", v); WM.rerenderAll(); if (!Tour.card().hidden && !Tour.on) Tour.greet(); },
  setView(v) {
    this.view = v; Store.set("view", v);
    Desk.load(); Desk.render(); World.syncFollow(); World.viewChanged(); WM.rerenderAll(); Dock.refresh(); Menubar.refresh();
    if (!Tour.on) Tour.greet();
  },
  start() {
    Tip.el = $("#tip");
    if (Look.state !== "auto") document.documentElement.setAttribute("data-look", Look.state);
    matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => Look.changed());
    new MutationObserver(() => Look.changed()).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    Menubar.build(); Dock.build(); Desk.load(); World.start();
    const hit = $("#rider-hit"); hit.hidden = true;
    let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => Desk.render(), 120); });
    document.fonts?.ready.then(() => Charts.redraw());
    let first = false;
    try { first = !sessionStorage.getItem("outpost-booted") && !reduced(); sessionStorage.setItem("outpost-booted", "1"); } catch { /* ignore */ }
    const boot = $("#boot");
    const go = () => { Desk.render(); Tour.greet(); };
    if (!first) { boot.remove(); $("#wall").hidden = true; go(); return; }
    Wall.render(1.2);
    setTimeout(() => { boot.classList.add("off"); setTimeout(() => boot.remove(), 500); Wall.rideIn(() => setTimeout(() => Wall.dissolve(go), 350)); }, 900);
    boot.addEventListener("click", () => { boot.classList.add("off"); });
  },
};
