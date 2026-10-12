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
   - Each horizon is scored at the horizon itself: from the live price in the first half hour after it, and later (the agent was down) from Hyperliquid's 15-minute candle at that moment, never from today's price. A coin with no Hyperliquid history stays unscored rather than scored at the wrong time.
   - A result is written once and never overwritten.
   - The journal is append-only: a pick is never deleted or edited. One that should not count (a draft rejected, a false group purged) is voided with its reason and still listed by `GET /picks`.

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
- **In Telegram:** `/now` sends the live pulse; `/positions` what the tracked traders hold and are adding; `/following` lists followed groups; `/groups` shows the latest five; `/help` explains each alert and the words in them. Alerts are written in plain words (a trader's record reads "6 won, +$85K ($55K of it still open), wins $2.2 per $1 lost"; above $20 per $1 it says "hardly any losses", since the ratio then only says the losses were tiny). The bot only obeys `TELEGRAM_CHAT_ID`.

Alerts are built in code. Set `SYNTH_LLM_ALERTS=1` only if you want the model to word them.

**Convergences and the engine (study S1).** Every convergence seen within 2 hours of forming is logged with the engine's 1D and 4H reading for that coin at that moment: with them, against them, or no signal (`synth/study.py`). The Groups tab and `GET /study` show how each kind went: the move their way at 1, 3 and 7 days, and the traders' own result once all are out. Rules and the bar for a verdict: `docs/studies/S1-engine-vs-traders.md` (amendment 3). Until 30 of each, from 10 coins each, it is context, not a signal.

## The live pulse: who is opening and closing, 1h / 6h / 24h

Reflex refreshes every tracked profitable wallet's positions every few minutes while the agent polls it. Each poll the agent stores one compact row per coin: long and short positions, their dollars and the price (`series` table, 8 days kept). Comparing consecutive rows gives the flow (`synth/flow.py`):
- **L / S:** the change in how many traders hold a long or a short. A close shows as a minus, so `L−2 S+3` means two longs closed and three shorts opened (net).
- **Net size:** dollars added long minus dollars added short, with the price move taken out, so a rally doesn't read as longs added.
- **Left out:** intervals where Reflex's roster changed (its daily refresh adds and drops wallets), the agent was down for more than 30 minutes, or Reflex served stale data. The flow is only what traders did.

Where it shows:
- **Dashboard, Now tab:** the Pulse panel follows the 1h / 6h / 24h / 3d selector. Tiles show long and short positions now with their change; the share of positions that are long, as a line you can hover; and coins moving long (green) and moving short (red). Event cards are green or red by direction too.
- **Telegram:** the market read (06, 10, 14, 18 UTC) now comes as a picture: the 24h line of the share long, and a map of the most active coins with their L / S change and net size in the last 1h, 6h and 24h, coloured by direction. Send `/now` at any time for the same picture with a two-line summary.
- **API:** `GET /flow?hours=6` (JSON) and `GET /flow.png` (the picture).

## Tracked traders: proven and riding winners

Two groups, both read every poll from Hyperliquid's public API, one light request per trader (`synth/position_traders.py`):
- **Proven** (study S3 with the owner's amendment; started from `synth/data/position_traders.json`, 100 on 10 October 2026): 5+
  positions held 3+ days, **open ones counted at what was already taken off them plus their unrealised PnL** (a loser cut
  to a small winning remnant still reads as a loser), those positions at least half the size the
  trader trades, in profit with a profit factor of 1.5+. Closed positions are not the only record: the trader who opened
  NEAR near the bottom and still holds it counts.
  **Kept current** (`synth/proven.py`): the rule is rerun in the background on a pool rebuilt daily from the leaderboard
  (Reflex's profitable-trader rule, accounts of $500K+ in profit, the listed). New names are read first, listed traders
  every 2 days so their records stay current, the rest weekly; a first read covers 365 days, later reads only the fills
  since (merged per order, pages joined on the boundary millisecond). Paced to at most 30% of Hyperliquid's per-IP
  budget, and every Hyperliquid read in the agent (this refresh, the daily scan, tracking, scoring) draws on one budget
  of 900 weight a minute, three quarters of the limit; a 429 waits as long as Hyperliquid asks. Someone who stops qualifying drops after 30 days; bots stay out. The same reads keep the owner map current:
  each big account's sub-accounts weekly, and wallets moving in the same minutes (E1 link 3a) recomputed weekly.
- **Riding winners**, two ways, both in profit all-time and not market makers:
  - found once a day from Hyperliquid's leaderboard among accounts of $500K+: open positions with $250K+ unrealised
    profit, one of them up 20%+ on its value;
  - **any account size** (owner, 12 October 2026: "if a guy is up 200% unrealized, I need to know"; "we should
    track everyone that has an open position"): a position open 14+ days, up 30%+ on price the trader's way or 200%+
    on margin, with $10K+ in profit. Every profitable account of $10K+ on the leaderboard (about 14,400 on 12 October,
    against 4,500 before) is read once a day; a wallet holding a big open winner goes to the front of the refresh,
    which reads its fills for how long it has been open and lists it when it qualifies.
  A rider who stops qualifying for 14 days drops off. A trader whose list place rests on open profit (closed trades not
  in profit) reads "riding a winner", with the trade that carries it.
- **Open winners** (desk, Traders tab): the biggest unrealised profits among everyone the agent reads (every profitable
  $10K+ account daily, the refresh pool at its last check, the tracked traders every poll), up 20%+ on price or 100%+
  on margin, $10K+, open a week or more: held since, entry,
  up on price, return on margin and leverage as Hyperliquid shows them, open profit, share of the account. Only from a
  read in the last 36 hours (an older one may describe a position long closed), held counted to that read; the daily
  scan reads again every wallet it has a winner on file for, so a closed one drops off. Outrider can post the ones held a
  month or more (up 50%+ on price, $50K+), read in the last 6 hours.

Leverage is shown for every trader (low under 3x, moderate 3-5x, high over 5x), never used to leave anyone out.

**Big money**, a third lens: the daily scan reads every account of $500K+ in profit all-time anyway, so the agent keeps
their long and short per coin, **one vote per owner** (owner, 10 October 2026): an owner's wallets are netted per coin
and vote long or short from $25K, with dollars beside, so one giant account cannot decide the reading (the two largest
accounts were short about $1.2B of ETH, BTC, SOL and HYPE while holding dollars on Hyperliquid: hedges held elsewhere,
if they are, cannot be seen from here). Owners come from study E1 (`synth/data/owners.json`): Hyperliquid's sub-account records and wallets that
trade together; money links are left out until checked. Reflex's profitable traders are picked by monthly return (30%+), which a
$200M account cannot make, so the biggest and steadiest money never shows there. On XPL (10 October 2026) the two
disagreed: Reflex's list 19 long ($2.9M) against 5 short ($0.7M); big profitable accounts $7.4M long against $18.4M
short, the largest two shorts held by accounts with $145M and $108M all-time profit at 3-5x.

What the agent does with them:
- **Holdings:** coin, side, value, entry, held since (unknown for riders until the agent sees them open something),
  unrealised PnL, leverage now.
- **Adding:** every add and cut is recorded; the dashboard shows, per coin and side, how many traders added and the net
  dollars over 24 hours and 7 days.
- **Build-up alert** (Telegram, at most 3 a day, one per coin and side per 72 hours): 3+ tracked traders each add $25K+,
  $250K+ in all, within 72 hours, while the price moved under 15%. The shape NEAR had before its move. It also says what
  tracked traders hold on the other side and whether big money agrees. Context, not a signal (study S4: long build-ups
  beat the market by 4 points over 30 days, but big runs were no more common than for any coin). BTC and ETH are left
  out (owner, 10 October 2026): they read as direction and sentiment, shown on the desk; the alert looks for coins that
  can run.
- **Conviction alert** (at most 5 a day): a new position at least 2x the trader's usual size within 4 hours of opening.
  One that grows into a conviction bet after it opened is logged as its own pick at the mid when it did, never at the
  smaller open's price.
- **Forward record:** every new position of $10K+ is a pick at Hyperliquid's mid, scored at 14 and 30 days.
- **A read after a gap** (more than 30 minutes since the trader's last one: the agent was down, or the trader left the
  list and came back) is a fresh starting point: what changed meanwhile makes no pick, alert or flow.

Where it shows: the dashboard's **Traders** tab (by coin; what they are adding; each trader with group, record,
leverage and positions; filters for proven, riding and leverage 5x or less), `GET /position-traders`, and `/positions`
in Telegram (holdings by coin and what they added this week).

## Daily brief (Telegram, after 08:00 UTC)

The brief has three short sections and is under 15 lines:
- **Derive smart wallets:** BTC, ETH, HYPE. One line each: the lean of top option wallets (7d, else 30d), their net delta, and whether calls or puts were bought today. Built in code.
- **Hyperliquid today:** where profitable traders are opening. Convergences from the last 24 hours come first, fastest at the top, then the busiest coins by new opens. Built in code.
- **Open groups:** smart traders who converged and are still in, with how many are still in, their entry range, the price now, and the losing line. Built in code; there is no model in the brief.

Only smart-money events ping you: convergences, big single-wallet moves, smart-cohort flips, the same move on both venues, and the crowded-market warning. There are at most 2 alerts per poll and `SYNTH_MAX_ALERTS_PER_DAY` (default 6) per 24 hours. Engine signals, skew shifts and Reflex's anomalies and setups only show on the dashboard and in the pulse.

Reflex flags a convergence when 2+ profitable traders open the same side within 24 hours. The agent alerts only on fast ones, where all the opens came within `SYNTH_FAST_CONVERGENCE_HOURS` (default 6). Slower ones stay on the dashboard and in the pulse but don't ping you.

## Outrider on X (the persona)

Outrider is the public character: a masked scout who rides ahead of the herd and posts what he sees, built to bring
traders to Cowboy (owner, 11 October 2026). Mostly Derive's options as Cowboy shows them, some Hyperliquid smart money.
It posts and never replies; it never says what happens next (`synth/persona.py`).

- **Stories, found in code** from Cowboy's public files and Outrider's own alerts, every number formatted before the
  model sees it: a settlement that just paid (what an answer cost a day out against what it paid), how Derive's option
  wallets lean per date (headcount against money), a Saloon question (what Higher and Lower cost and how far price must
  move), where the biggest call and put piles sit, a top trader by codename (the name Cowboy shows,
  `synth/wallet_names.py`, never an address), the herd, and Hyperliquid conviction bets and build-ups.
- **Rotation:** Cowboy stories weigh more, fresh ones more again; the same kind within 12 hours or coin within 6 hours
  weighs less; a story is not told twice in 2 days, a trader not twice in 3 weeks.
- **Writing:** the model writes in the voice (`VOICE`); `SYNTH_PERSONA_MODEL` (default `claude-opus-5-5`, with
  Anthropic's server-side refusal fallback on).
- **Checks in code:** at most 270 characters; no forecast or advice words, emojis, hashtags, links, addresses or other
  @mentions; never "Reflex" or "Larsson"; @derivexyz exactly once on Cowboy stories; every number must appear in the
  facts. A failing draft is retried once with the problems, then kept as rejected.
- **Drafts on Telegram** at `SYNTH_PERSONA_HOURS_UTC` (default 9, 13, 17, 21; empty disables), with **Post** (published
  through Orbio) and **Skip**; reply to a draft with new text to replace it (checked the same way, except the numbers);
  `/draft` makes one now. `SYNTH_PERSONA_MODE=live` publishes without asking: only once the drafts have earned it.
- Needs the X account connected in Orbio (`ORBIO_API_KEY`), as for the desk posts.

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

On Mondays a scorecard built in code (no model) posts the 7-day results: hits, average move, best and worst, misses included. Every logged setup counts, posted or not, and the scorecard says how many were logged but not posted. A setup keeps the time and price it was logged at and, once posted, the time and Hyperliquid mid it reached X at.

- `SYNTH_POST_MODE=draft` (default): posts are stored and sent to your Telegram. `POST /posts/{id}/publish` approves and publishes one.
- `SYNTH_POST_MODE=live`: posts publish through Orbio's gateway (`social.post`, about $0.019 per post).
  - Needs `ORBIO_API_KEY`, and the desk X account connected in Orbio's dashboard (only the owner can do that).
  - A post is never sent twice: it is claimed (`publishing`) before the call, so a double tap or Telegram and the
    dashboard at once send it once. `running` posts are checked each poll with Orbio's free `social.post.status`.
    `failed` (Orbio refused it before posting) can be tried again. `unknown` (Orbio's answer was lost, or it failed
    after the request reached it) may already be on X, so it is never sent again without the owner checking X.

The agent has no token. Launching one on Orbio waits until the posts show they are useful.

## Dashboard (the desk)

Open the service's root URL (`/`). Token holders press **Connect wallet** (see "Token holders" below); the owner
chooses **Owner**, enters the key once and the browser keeps it. Holders see everything but the owner's controls (Ask,
Posts, writing a brief, Follow) (`synth/static/dashboard.html`, `synth/desk.py`).

**Design** (shared with the public record: `synth/static/assets/ui.css` and `ui.js`, served at `/static`): Apple's system type and
colours, glass cards over a quiet ambient light, dark and light, phone and desktop. No pills: tabs are underlined text.
Minimal text on screen; every explanation sits behind an (i) that opens on hover, focus or tap.

**Signing in** is one sheet that shows one state at a time, so every outcome has its own screen: the desk's name and
who it is for; a wallet chooser when the browser has several (EIP-6963); on a phone without a wallet, buttons that open
the page in MetaMask or Coinbase Wallet; Connect, Sign (free, no gas) and Check balance as three steps; declined, a
request already open in the wallet, too busy, or the chain unreachable, each with Try again; too few tokens, with the
balance against the threshold and **Get {symbol}** (`GATE_BUY_URL`); a session that ended (12 hours, or the balance fell);
then the desk. Switching account in the wallet signs out. The account menu (top right) shows the wallet, its balance,
the public record and Sign out.

- **Market** (home, `GET /desk`):
  - a one-line read of the market;
  - six tiles (crowd long, big money long, tracked long, uptrend share and run age, BTC, alerts in 24 hours);
  - **direction of BTC and ETH**: each lens's share of heads long as a gauge around 50% (dollars on hover), whether they agree, the
    crowd's share over 7 days against price, and what tracked traders added in 24 hours (the majors as one directional
    reading, like Cowboy's wallets layer);
  - the crowd against price (BTC above, share of profitable traders long below, 24h / 3d / 7d, hover);
  - the latest alerts (build-ups with whether big money agrees, conviction bets, convergences);
  - where the money is moving (tracked traders' net adds per coin, 24h or 7d, or the crowd's opens and closes; the
    other coins);
  - **three lenses on the other coins**: the crowd (Reflex's profitable traders), tracked traders and big money, each as
    heads long against short (one vote per trader, per owner for big money) with dollars, and whether they agree (a
    lean needs 3 heads and 1.5 times the other side);
  - the market read (the text sent to Telegram).
- **Traders:** tracked traders (proven and riding winners), by coin and one by one, what they are adding.
- **Groups:** convergence groups trader by trader, with Follow.
- **Research** (`GET /research`): the market cycle from Reflex's market history (fetched once a day: share of coins in
  an uptrend, the current risk-on or risk-off run against past runs), and every study as a chart with its finding and
  verdict (S1 to S5, where the money came from), plus the forward record.
- **Record:** picks (scored at 1, 3 and 7 days, voided ones listed with their reason), the brief, and desk posts (owner).
- **Ask:** a question answered from current data (uses the model).

## API

| Route | |
|---|---|
| `GET /health` | Liveness, last poll stats and each stage's last error (no auth); 503 when no poll finished for 3 intervals (at least 15 minutes) |
| `GET /` | Dashboard |
| `GET /pulse?hours=1` | Last hours' events by coin, with the pulse text |
| `GET /board` | Smart money board: where the layers agree per coin |
| `GET /events?hours=24&coin=&min_score=` | Detected events |
| `GET /coin/{coin}` | Both products' current view of a coin, plus 7 days of its events |
| `GET /brief` / `POST /brief?push=true` | Latest daily brief / write one now |
| `POST /ask` `{"question": "..."}` | Question answered from current data and the last 7 days of events |
| `GET /picks` | Pick journal with 1d/3d/7d returns, voided picks with their reason |
| `GET /flow?hours=1` | What profitable traders opened and closed in the window, and the share long over time |
| `GET /flow.png` | The pulse picture (as sent to Telegram) |
| `GET /desk` | The desk's home: readings, BTC and ETH direction, crowd against price, money moving, three lenses, alerts |
| `GET /research` | Studies as charts, the forward record, Reflex's market cycle |
| `GET /position-traders` | Tracked traders (proven and riding winners): records, holdings, what they are adding, the forward record |
| `GET /study` | Convergences by the engine's reading when they formed, and how they went |
| `GET /posts` | Desk posts (draft, published, failed, rejected) |
| `POST /posts/daily?publish=false` | Write today's post now |
| `POST /posts/scorecard?publish=false` | Build the weekly scorecard now |
| `POST /posts/{id}/publish` | Approve a draft: publish it via Orbio |
| `POST /cycle` | Run one poll now |
| `GET /backup` | A consistent copy of the whole SQLite database, for keeping off the server |
| `GET /r` | The public record's page (no key) |
| `GET /r/receipts.json` | The public record: every call logged `RECEIPTS_DELAY_H`+ hours ago and how it went (no key) |
| `GET /r/ledger.json` | The hash chain behind it, calls inside the delay withheld (no key) |

**Who can call what.** The owner (header `X-API-Key: SYNTH_API_KEY`) can call everything. A token holder signed in with their wallet (below) can read the desk: `/board`, `/pulse`, `/events`, `/coin`, `GET /brief`, `/picks`, `/groups`, `/flow`, `/desk`, `/research`, `/position-traders`, `/study`. Only the owner can do what posts, runs, writes or costs money: `POST /brief`, `/ask`, `/posts` and publishing, Follow, `/cycle`, `/backup`. `/health`, `/` and `/auth/*` need nothing.

## The public record (receipts)

Every call the agent logs (smart-money alerts, desk setups, convergences, tracked traders' new positions, brief setups) is public `RECEIPTS_DELAY_H` hours after it was logged (24 by default; holders see calls live on the desk): `GET /r/receipts.json`, no key. Each call shows when it was logged, the coin, side and entry, its results at 1/3/7 (and 14/30) days, whether and when it was posted, and its void reason if voided. Misses and voided calls stay in. The reasons behind a call (the thesis, and any wallet addresses) are not shown, but their hash is, so they can be shown later and checked.

**The page** (`/r`, no key; `synth/static/receipts.html`): the record for people arriving from X, phone first, dark and light. It shows the share of calls that moved their way at 7 and 1 days, every call by kind, and every call newest first with its 1/3/7-day results, misses and voided calls included. The reader's own browser recomputes the whole chain and says whether it is unbroken.

**Checkable by anyone.** Every change to a pick (logged, scored, voided, posted) is an entry in one hash chain (the `ledger` table): each entry's hash is `sha256(prev + ":" + sha256(body))`, and the first `prev` is 64 zeros. `GET /r/ledger.json` publishes the whole chain. Entries about calls still inside the delay keep their hashes but not their bodies. So anyone can recompute the chain, see that nothing was changed, dropped or added later, and, once a body is shown, check it is the one hashed back then. Picks logged before the chain existed joined it once, marked `backfilled`.

## Token holders (the gate)

The desk opens to wallets holding at least `GATE_THRESHOLD` of the token (a fixed number of tokens, not a dollar value), checked on the server (`synth/gate.py`):
- The holder presses **Connect wallet**. The server writes a Sign-In with Ethereum message (EIP-4361: its domain, the chain, a single-use nonce, 10 minutes to sign), and the wallet signs it. No transaction, no gas.
- The server checks the signature (a plain wallet, or a smart-contract wallet such as a Safe through EIP-1271), then reads the token balance on-chain (`balanceOf`).
- Enough tokens: a session, a signed HttpOnly cookie, for 12 hours. The balance is read again every 10 minutes, so selling below the threshold closes the desk.
- Until `GATE_RPC_URL`, `GATE_TOKEN` and `GATE_THRESHOLD` are set (the token is not launched yet), only the owner's key opens the desk.
- Set `SYNTH_SESSION_SECRET` (any long random string) so sessions survive restarts, and `GATE_DOMAIN` to the desk's host name.
- `GATE_SYMBOL` names the token on the page ("For holders of 1,000 OUT"); `GATE_BUY_URL` (https only) is offered to a wallet short of it.
- Sign-in allows 10 challenges and 10 verifications a minute per visitor address. Behind the host's proxy the visitor's
  address comes from the proxy's forwarded header (`FORWARDED_ALLOW_IPS`, `*` by default; narrow it if the app is
  reachable directly), or every visitor would share one limit.
- Not covered yet: smart wallets that are not deployed yet (ERC-6492) and WalletConnect (phone wallets without their own browser). On a phone the holder opens the desk in their wallet's own browser.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env                 # fill in keys; export them into the environment
python -m synth cycle                # one poll; prints new events
python -m synth digest               # the compact data the model sees (no model call)
python -m synth serve                # API + background poller on $PORT (default 8000)
```

Deploy: the `Dockerfile` runs `python -m synth serve`. On Railway, mount a volume at `/data` so the SQLite state (`SYNTH_DB=/data/synth.db`) survives restarts.

- **Pinned dependencies:** the image installs `requirements.lock` (exact versions, hash-checked). After changing `requirements.txt`, regenerate both locks: `uv pip compile requirements.txt --python-version 3.12 --generate-hashes -o requirements.lock` (and the same for `requirements-dev.txt` into `requirements-dev.lock`).
- **CI** (`.github/workflows/test.yml`): every push and pull request runs the tests on Python 3.12 from the dev lock and builds the image.
- **Backups:** once a day the journal (every pick, voided ones too, every post, group and group member: what cannot be read again from anywhere) is written to `SYNTH_BACKUP_DIR` (`/data/backups` in the image) as gzipped JSON; the last 30 are kept, and nothing is written when the disk is nearly full. These sit on the same volume, so keep copies elsewhere too: `GET /backup` returns a consistent copy of the whole database (`curl -H "X-API-Key: …" -o synth.db https://…/backup`).
- **Health:** `/health` returns 503 when no poll has finished for three intervals; point an uptime monitor at it.

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
