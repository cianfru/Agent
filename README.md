# Agent

A private market synthesizer that sits above **Reflex** (`cianfru/RCCE_Scanner`) and **Cowboy** (`cianfru/derive_scan`). It reads what both products already publish, detects events in plain code, and only calls a cheap LLM to explain an event or write the daily brief. Results come back through an HTTP API and, optionally, Telegram.

It follows the plan recorded in `derive_scan/docs/next-stage-agent-and-social.md`:
- Watching is code, not a model.
- The model is called only on events and once a day.
- Every pick is logged before the outcome and scored at 1, 3 and 7 days.

## How it works

Each poll (every 5 minutes by default) runs four steps:

1. **Collect** (`synth/sources/`)
   - **Cowboy** is read from the public `site-data` branch of `cianfru/cowboy-feed` (no key):
     - `markets.json`: consensus, sentiment, and per-coin RCCE engine and options state
     - `traders.json`: smart cohorts' delta per coin
     - `flow.json`: 24h taker flow
     - `wallets/{COIN}.json`: top holders' net delta
   - **Reflex** is read from its Railway API (needs an access code):
     - `/api/hyperlens/positioning`: profitable Hyperliquid traders' long/short per coin, opens in the last 24h, and the engine's 4h/1d reading
     - `/api/notifications/convergence`: the "2+ profitable traders opened the same side" event
     - `/api/notifications/anomalies`, `/market-setups`, `/exhaustion-opportunities`
     - `/api/market-pulse`
2. **Detect** (`synth/detectors.py`). This compares the snapshot with the previous one and scores each event from 0 to 100:

   | Event | What it means | Typical score |
   |---|---|---|
   | `smart_convergence` | 2+ smart wallets moved the same way on one coin (Reflex convergence, or Cowboy holders) | 70-95 |
   | `cross_confirm` | The same coin and direction confirmed by the other product's smart money | 75-85 |
   | `cohort_flip` | A smart cohort's or profitable traders' lean flipped long/short | 65-68 |
   | `wallet_move` | One smart Derive wallet opened, added, cut or closed at least $100K of delta | 35-75 |
   | `signal_change`, `regime_change` | RCCE engine transitions (4h/1d) | 30-75 |
   | `skew_shift` | 30d 25-delta risk reversal moved by at least 2 vol points | 40-70 |
   | `anomaly`, `setup` | Reflex's funding/OI/volume outliers and OI-vs-price setups | 42-60 |
   | `crowded` | RISK-ON breadth of 80% or more, Fear & Greed of 70 or more, and hot coins | 60-90 |

   The same event is not repeated within 12 hours. The first poll of a source only sets the baseline and sends no alerts.
3. **Alert** (`synth/llm.py`, `synth/notify.py`). Events scoring at least `SYNTH_ALERT_MIN_SCORE` (default 60), at most 5 per poll, go to the model together with everything both products say about that coin. The model returns structured JSON: headline, direction, conviction 1-5, why it matters, and what confirms or contradicts it. That JSON is pushed to Telegram.
4. **Journal.** Alerts with conviction 3 or higher, and every brief setup, are logged with their entry price. Their 1d, 3d and 7d returns are filled in automatically (`GET /picks`).

**Cost.** With Claude Haiku 5.5 ($0.10/$0.50 per million tokens), an alert is about 3K tokens and a brief about 10-15K tokens, roughly $0.001 each. To have the daily brief written by a stronger model, set `SYNTH_BRIEF_MODEL=claude-opus-5-5`; alerts stay on Haiku.

## Public desk posts (X via Orbio)

A separate "desk" account posts once a day. The post is written for a phone and runs at most 280 characters:
- the market backdrop
- where profitable Hyperliquid traders and Derive's top option wallets line up
- at most one setup with what would undo it (or "No setup today")
- an optional Robinhood Chain fact from Block0's public weekly numbers (`agent-data/stats.json`)

Rules, enforced in code (`synth/post.py`):
- No links: Orbio refuses linked posts.
- No forecast or hype words: Block0's list plus the products' banned words.
- Every setup is logged as a numbered pick before the outcome.
- A failing draft is retried once with the problems fed back, then rejected.

On Mondays a scorecard built in code (no model) posts the 7-day results: hits, average move, best and worst, misses included.

- `SYNTH_POST_MODE=draft` (default): posts are stored and sent to your Telegram. `POST /posts/{id}/publish` approves and publishes one.
- `SYNTH_POST_MODE=live`: posts publish through Orbio's gateway (`social.post`, about $0.019 per post).
  - Needs `ORBIO_API_KEY`, and the desk X account connected in Orbio's dashboard (only the owner can do that).
  - A post is never sent twice.

The agent has no token. Launching one on Orbio waits until the posts show they are useful.

## Dashboard

Open the service's root URL (`/`) for a private dashboard. If `SYNTH_API_KEY` is set, the page asks for the key once and keeps it in the browser.

- **Now:** events over the last 1h, 6h, 24h or 3d, plus a "smart money now" board. The board shows one row per coin with four readings: engine, Hyperliquid profitable traders, Derive smart wallets and options tone. The row says how many agree (for example `3/4 long`, `2L 1S`, `split`). Coins where 3 or more agree are marked.
- **Brief:** the latest daily brief, or write one now.
- **Posts:** desk drafts and published posts, with a Publish button.
- **Picks:** every logged pick with 1d, 3d and 7d results and a running hit rate.
- **Ask:** a free-form question answered from current data.

The **hourly pulse** is a plain-text summary of the last hour's events by coin, plus coins where the layers agree. It is built in code (no model cost) and sent to Telegram only when something happened. Set the cadence with `SYNTH_PULSE_MINUTES` and the bar with `SYNTH_PULSE_MIN_SCORE`.

## API

| Route | |
|---|---|
| `GET /health` | Liveness and last poll stats (no auth) |
| `GET /` | Dashboard |
| `GET /pulse?hours=1` | Last hours' events by coin, with the pulse text |
| `GET /board` | Smart money board: where the layers agree per coin |
| `GET /events?hours=24&coin=&min_score=` | Detected events |
| `GET /coin/{coin}` | Both products' current view of a coin, plus 7 days of its events |
| `GET /brief` / `POST /brief?push=true` | Latest daily brief / write one now |
| `POST /ask` `{"question": "..."}` | Question answered from current data and the last 7 days of events |
| `GET /picks` | Pick journal with 1d/3d/7d returns |
| `GET /posts` | Desk posts (draft, published, failed, rejected) |
| `POST /posts/daily?publish=false` | Write today's post now |
| `POST /posts/scorecard?publish=false` | Build the weekly scorecard now |
| `POST /posts/{id}/publish` | Approve a draft: publish it via Orbio |
| `POST /cycle` | Run one poll now |

If `SYNTH_API_KEY` is set, every route except `/health` needs the header `X-API-Key`.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env                 # fill in keys; export them into the environment
python -m synth cycle                # one poll; prints new events
python -m synth digest               # the compact data the model sees (no model call)
python -m synth serve                # API + background poller on $PORT (default 8000)
```

Deploy: the `Dockerfile` runs `python -m synth serve`. On Railway, mount a volume at `/data` so the SQLite state (`SYNTH_DB=/data/synth.db`) survives restarts.

## Secrets you need to add (never commit them)

- `ANTHROPIC_API_KEY`: without it the agent still detects and pushes plain-text alerts.
- `REFLEX_ACCESS_CODE`: a dedicated, revocable invite code (Reflex owner: `POST /api/owner/invites`). Without it only Cowboy is read.
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`: optional, for push (and for receiving draft posts).
- `ORBIO_API_KEY`: only when publishing desk posts.

Reflex counts every request as activity, which raises its Railway cost, so keep `SYNTH_POLL_SECONDS` at a few minutes. ETag/304 is used to keep egress low.

## Tests

```bash
pip install -r requirements-dev.txt && pytest -q
```

Decision support only: the agent never holds trading keys or places trades.
