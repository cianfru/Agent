# Outpost: Outrider's desktop (prototype)

A clickable prototype of the desk as a desktop: Outrider's dispatch first (what matters, in plain words, with the
evidence one click behind every number), a magnifying dock, dossiers you drag onto the desktop and drop in the
barrel to remove, windows with zoomable charts, three looks (Night ride, Dawn, Mode 13h) and a visitor view that
shows what holding the token opens. No build tools: plain HTML, CSS and JavaScript, like `synth/static`.

- `fetch.py` pulls real Hyperliquid prices, BTC/ETH funding and open interest into `hl.json` (not committed).
- `gen.py` builds `sample.json` around those prices: every position, trader, group, open interest history and
  record entry in it is made up, shaped like the agent's own data (`synth/desk.py`). Study verdicts are real
  (`synth/data/research.json`).
- `build.py` inlines `style.css`, `art.js`, `chart.js`, `shell.js`, `apps.js` and `sample.json` into
  `page.html` and writes `outpost.html` (an Artifact page body; not committed).

Wiring it to the desk means replacing `window.OUTPOST_DATA` with `/desk/home` (and the trader and record
endpoints) and moving the files into `synth/static/assets`. Open interest history needs the agent to record
`metaAndAssetCtxs` each poll (one free call).
