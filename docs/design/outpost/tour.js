/* Outrider's greeting and the Ride along: the dispatch told as a ride through the Territory. */
const Tour = {
  i: -1, steps: [], on: false,
  card() { return $("#tour"); },
  greet() {
    const vis = App.view === "visitor";
    const c = this.card(); c.hidden = false; c.classList.remove("riding");
    c.innerHTML = `<img class="tf" src="${Art.face()}" alt=""><div class="tt"><div class="tw">Outrider · ${vis ? "yesterday's dispatch" : "dispatch, " + hhmm(NOW) + " UTC"}</div>
      <p>${App.voice === "char"
        ? `The herd's leaning long at ${Herd.at(vis ? NOW - DAY : NOW).toFixed(0)}%, leverage is creeping back after ${wday(F0)}'s flush, and ${vis ? "three camps want a look. Holders see which." : "three camps want a look."}`
        : `${Herd.at(vis ? NOW - DAY : NOW).toFixed(0)}% of positions are long. Open interest has recovered about half of ${wday(F0)}'s drop. Three coins stand out.`}</p></div>
      <div class="tbtn"><button type="button" class="btn primary" data-ride>Ride along</button><button type="button" class="btn" data-read>Read it</button></div>
      <button type="button" class="tx" aria-label="Close">×</button>`;
    $("[data-ride]", c).onclick = () => this.start();
    $("[data-read]", c).onclick = () => { this.hide(); WM.open("dispatch", null, c.getBoundingClientRect()); };
    $(".tx", c).onclick = () => this.hide();
  },
  hide() { this.card().hidden = true; },
  build() {
    const c = App.voice === "char", vis = App.view === "visitor";
    const f = Flush, picks = Picks.list();
    const S = [
      { say: c ? "This is the territory. Every camp is a coin: the further east, the more of the herd is long. North of the dry riverbed the proven traders lean long, south of it they lean short."
               : "Each camp is a coin. Further east: more of the crowd is long. North of the riverbed: most proven traders holding it are long; south: short.",
        go: () => { World.setTime(vis ? NOW - DAY : NOW); World.ring(null); World.flyHome(); World.rideTo("_fort"); } },
      { say: c ? "Each rider is a proven trader's position: blue bandana long, red short. New ones ride out from the Outpost, closed ones ride home. The cattle are the herd. The dust is leverage."
               : "Each rider is one position held by a proven trader (blue: long, red: short). Cattle are the wider crowd. Dust thickens with open interest.",
        go: () => { World.ring(null); World.fly({ x: 300, y: 150, z: 1.5 }); World.rideTo("_herd"); } },
      { say: c ? `That dotted trail is the herd's line: ${Herd.at(vis ? NOW - DAY : NOW).toFixed(0)}% of positions long, against ${Herd.at((vis ? NOW - DAY : NOW) - DAY).toFixed(0)}% a day before. The light runs warmer as they lean long.`
               : `The dotted line marks the whole crowd: ${Herd.at(vis ? NOW - DAY : NOW).toFixed(0)}% long, ${Herd.at((vis ? NOW - DAY : NOW) - DAY).toFixed(0)}% 24 hours earlier.`,
        go: () => { World.ring("_herd"); World.flyHome(); World.rideTo("_herd"); } },
      { say: c ? `Now watch ${wday(F0)}. From ${hhmm(F0)} UTC, ${usd(f.cut)} of open interest gets cut in five hours. Dust kicks up, the cattle scatter, and the herd piles back in long: ${f.crowdPeak.toFixed(0)}% by nightfall.`
               : `${day(F0)}, from ${hhmm(F0)} UTC: open interest fell ${usd(f.cut)} (${pct(f.cutPct, 0)}) in five hours while BTC fell ${Math.abs(f.btc).toFixed(1)}%. The share long then rose to ${f.crowdPeak.toFixed(0)}%.`,
        go: () => { World.ring(null); World.flyHome(); World.rideTo("_herd"); World.play(F0 - 5 * H1, F1 + 12 * H1, 7500); } },
      { say: c ? `Since then about half of that leverage is back on, and the herd's drifting long again as prices slide.`
               : `Since then open interest has recovered ${Math.round(f.back * 100)}% of the drop, and the share long has climbed while prices fell.`,
        go: () => { World.ring(null); World.play(F1 + 12 * H1, vis ? NOW - DAY : NOW, 4200); } },
    ];
    if (vis) S.push({ say: c ? "Three camps stand out today. Which ones, and who's camped there, is for holders." : "Three coins stand out today. Holders see which, and who holds them.",
      go: () => { World.ring(null); World.flyHome(); World.rideTo("_fort"); }, unlock: true });
    else picks.forEach((p, k) => S.push({
      say: (c ? ["First stop, ", "Next, ", "Last, "][k] + p.coin + ". " : p.coin + ": ") + Voice.pick(p),
      go: () => { World.setTime(NOW); World.flyTo(p.coin, phone() ? 1.5 : 2.1); World.ring(p.coin); World.rideTo(p.coin); }, coin: p.coin,
    }));
    S.push({ say: c ? "That's the ride. Click any camp to look inside, pin the ones you want me to watch, or drag the timeline back and ride it yourself."
                    : "Click a camp to open it, pin coins to follow them, or use the timeline to replay the last two weeks.",
      go: () => { World.ring(null); World.setTime(vis ? NOW - DAY : NOW); World.flyHome(); World.rideTo("_fort"); }, last: true });
    return S;
  },
  start() {
    this.steps = this.build(); this.on = true; this.i = -1;
    document.body.classList.add("riding");
    [...WM.wins.values()].forEach((w) => { if (!w.min) WM.minimize(w.key); });
    this.card().classList.add("riding");
    this.next();
  },
  render() {
    const s = this.steps[this.i], c = this.card();
    c.innerHTML = `<img class="tf" src="${Art.face()}" alt=""><div class="tt"><div class="tw">Ride along · ${this.i + 1} of ${this.steps.length}</div><p>${s.say}</p></div>
      <div class="tbtn">${this.i ? `<button type="button" class="btn" data-back>Back</button>` : ""}
        ${s.coin ? `<button type="button" class="btn" data-open>Open ${s.coin}</button>` : ""}${s.unlock ? `<button type="button" class="btn" data-unlock>Unlock</button>` : ""}
        <button type="button" class="btn primary" data-next>${s.last ? "Done" : "Next"}</button></div>
      <button type="button" class="tx" aria-label="End the ride">×</button>`;
    $("[data-next]", c).onclick = () => (s.last ? this.end() : this.next());
    $("[data-back]", c) && ($("[data-back]", c).onclick = () => this.back());
    $("[data-open]", c) && ($("[data-open]", c).onclick = () => { this.end(); WM.open("coin", s.coin); });
    $("[data-unlock]", c) && ($("[data-unlock]", c).onclick = () => { this.end(); WM.open("unlock"); });
    $(".tx", c).onclick = () => this.end();
    $("[data-next]", c).focus({ preventScroll: true });
  },
  next() { this.i++; this.render(); this.steps[this.i].go(); },
  back() { this.i = Math.max(0, this.i - 1); this.render(); this.steps[this.i].go(); },
  end() {
    this.on = false; document.body.classList.remove("riding");
    World.ring(null); World.setTime(App.view === "visitor" ? NOW - DAY : NOW); World.flyHome(); World.rideTo("_fort");
    this.greet(); this.hide();
  },
};
document.addEventListener("keydown", (e) => {
  if (!Tour.on) return;
  if (e.key === "ArrowRight") Tour.steps[Tour.i].last ? Tour.end() : Tour.next();
  else if (e.key === "ArrowLeft") Tour.back();
  else if (e.key === "Escape") Tour.end();
});
