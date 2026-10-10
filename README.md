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

## Groups: smart traders tracked to their close

Every convergence becomes a numbered group (`#7 STRK short`), and every trader in it is tracked until they close.

- **Hyperliquid groups** come from Reflex's convergences. Reflex gives each trader's wallet, entry, size and time (`/api/hyperlens/opens/{coin}`). Each wallet is then read straight from Hyperliquid's free public API (`synth/hyperliquid.py`). A close is priced from the trader's own closing fills, which also give realised PnL. If no fills are found, the price is the mid when the position was seen gone, marked ≈.
- **Derive groups are off by default** (`SYNTH_DERIVE_GROUPS=1` turns them on). Cowboy builds each trader's book from its daily trader history, so new positions show up once a day in a burst, a day or two late, which can't be timed as convergences. Turn them on once Cowboy publishes positions as they trade. When on: Derive groups form when 2+ smart option wallets open legs in the same direction on one coin within `SYNTH_FAST_CONVERGENCE_HOURS`. Each leg is followed in the trader's published book (`traders/{address}.json`). A leg that disappears before expiry is closed (at its last mark, marked ≈); after expiry it is expired.
- **New fast groups** that formed in the last 2 hours arrive on Telegram as a chart and a caption, with **Follow / Ignore** buttons.
  - The chart shows 48 hours of price, each entry, and the line beyond which every trader in the group is losing.
  - The caption lists each wallet with size and entry, the entry range, the price now, and that line.
- **Followed groups** message you when a trader cuts size by half or more or closes (exit, result, time held), and with a summary when all are out. Unfollowed groups are recorded only.
- **On the dashboard:** the Groups tab shows every group trader by trader, with a Follow toggle and the scoreboard (closed in profit, average result).
- **In Telegram:** `/following` lists followed groups; `/groups` shows the latest five. The bot only obeys `TELEGRAM_CHAT_ID`.

Alerts are built in code. Set `SYNTH_LLM_ALERTS=1` only if you want the model to word them.

**Convergences and the engine (study S1).** Every convergence seen within 2 hours of forming is logged with the engine's 1D and 4H reading for that coin at that moment: with them, against them, or no signal (`synth/study.py`). The Groups tab and `GET /study` show how each kind went: the move their way at 1, 3 and 7 days, and the traders' own result once all are out. Rules and the bar for a verdict: `docs/studies/S1-engine-vs-traders.md` (amendment 3). Until 30 of each, from 10 coins each, it is context, not a signal.

## Daily brief (Telegram, after 08:00 UTC)

The brief has three short sections and is under 15 lines:
- **Derive smart wallets:** BTC, ETH, HYPE. One line each: the lean of top option wallets (7d, else 30d), their net delta, and whether calls or puts were bought today. Built in code.
- **Hyperliquid today:** where profitable traders are opening. Convergences from the last 24 hours come first, fastest at the top, then the busiest coins by new opens. Built in code.
- **Open groups:** smart traders who converged and are still in, with how many are still in, their entry range, the price now, and the losing line. Built in code; there is no model in the brief.

Only smart-money events ping you: convergences, big single-wallet moves, smart-cohort flips, the same move on both venues, and the crowded-market warning. There are at most 2 alerts per poll and `SYNTH_MAX_ALERTS_PER_DAY` (default 6) per 24 hours. Engine signals, skew shifts and Reflex's anomalies and setups only show on the dashboard and in the pulse.

Reflex flags a convergence when 2+ profitable traders open the same side within 24 hours. The agent alerts only on fast ones, where all the opens came within `SYNTH_FAST_CONVERGENCE_HOURS` (default 6). Slower ones stay on the dashboard and in the pulse but don't ping you.

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

The **market read** goes to Telegram at `SYNTH_PULSE_HOURS_UTC` (default 06, 10, 14 and 18 UTC; none at night). It is built in code (`synth/stories.py`), with no model cost:
- **The overall smart-money lean on Hyperliquid:** shorts $ vs longs $, positions opened each side in 24 hours, and the change since the last read.
- **One Derive line** for BTC, ETH and HYPE.
- **"Where it's crowding":** coins where smart money is moving and market data or a second smart signal agrees, for example `STRK short · shorts piling in · 4 smart shorts in 3h (#7) · open interest +34% · funding negative: shorts crowded`.

**Toxic flow (VPIN)** gets priority. VPIN is Reflex's measure of how one-sided taker flow is: 55% and up is toxic, 70% and up is critical. Its side comes from the coin's CVD reading.
- A coin with toxic flow goes to the top of "Where it's crowding".
- Toxic coins with no smart-money story get their own line.
- Critical flow on the same side as smart money is an immediate Telegram alert. It counts toward the daily cap and isn't repeated within 12 hours.

Any other raw anomaly on its own (open-interest surge, volume spike, buy/sell imbalance, funding extreme) is never sent. It only appears as context for smart money. The same read is shown at the top of the dashboard's Now tab.

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
| `GET /study` | Convergences by the engine's reading when they formed, and how they went |
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
