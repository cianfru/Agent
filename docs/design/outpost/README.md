# Outpost: Outrider's desktop (prototype)

A clickable prototype of the desk as Outrider's desktop. The desktop is the Territory (`world.js`): every camp is
a coin, placed east by the crowd's share long and north of the river when most proven traders holding it are long;
each rider is one proven trader's position (blue bandana long, red short), cattle are the crowd, dust is open
interest. A timeline replays the last two weeks (riders ride out from the Outpost when a position opens and home when
it closes). Outrider greets you with the dispatch and offers a Ride along (`tour.js`): he rides camp to camp while
the camera follows, replaying Thursday's flush on the way. On top: a magnifying dock, dossiers you drag onto the
desktop (followed camps carry your flag) and drop in the barrel to remove, windows with zoomable charts, three looks
(Night ride, Dawn, Mode 13h) and a visitor view, a day late and without riders. No build tools: plain HTML, CSS and
JavaScript, like `synth/static`.

- `fetch.py` pulls real Hyperliquid prices, BTC/ETH funding and open interest into `hl.json` (not committed).
- `gen.py` builds `sample.json` around those prices: every position, trader, group, open interest history and
  record entry in it is made up, shaped like the agent's own data (`synth/desk.py`). Study verdicts are real
  (`synth/data/research.json`).
- `build.py` inlines `style.css`, `art.js`, `chart.js`, `shell.js`, `apps.js`, `world.js`, `tour.js` and `sample.json` into
  `page.html` and writes `outpost.html` (an Artifact page body; not committed).

Wiring it to the desk means replacing `window.OUTPOST_DATA` with `/desk/home` (and the trader and record
endpoints) and moving the files into `synth/static/assets`. Open interest history needs the agent to record
`metaAndAssetCtxs` each poll (one free call).
