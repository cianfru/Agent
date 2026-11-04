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
- **Telegram:** with `SYNTH_READS=off` (or no model key), the market read (06, 10, 14, 18 UTC) comes as a picture: the 24h line of the share long, and a map of the most active coins with their L / S change and net size in the last 1h, 6h and 24h, coloured by direction. Send `/now` at any time for the same picture with a two-line summary.
- **API:** `GET /flow?hours=6` (JSON) and `GET /flow.png` (the picture).

## Tracked traders: proven and riding winners

Two groups, both read every poll from Hyperliquid's public API, one light request per trader (`synth/position_traders.py`):
- **Proven** (study S3 with the owner's amendment; started from `synth/data/position_traders.json`, 100 on 10 October 2026): 5+
  positions held 3+ days, **open ones counted at their unrealised PnL**, those positions at least half the size the
  trader trades, in profit with a profit factor of 1.5+. Closed positions are not the only record: the trader who opened
  NEAR near the bottom and still holds it counts.
  **Kept current** (`synth/proven.py`): the rule is rerun in the background on a pool rebuilt daily from the leaderboard
  (Reflex's profitable-trader rule, accounts of $500K+ in profit, the listed). New names are read first, listed traders
  every 2 days so their records stay current, the rest weekly; a first read covers 365 days, later reads only the fills
  since (merged per order, pages joined on the boundary millisecond). Paced to at most 30% of Hyperliquid's per-IP
  budget. Someone who stops qualifying drops after 30 days; bots stay out. The same reads keep the owner map current:
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
  up on price, return on margin and leverage as Hyperliquid shows them, open profit, share of the account. Outrider can
  post the ones held a month or more (up 50%+ on price, $50K+).

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
- **Forward record:** every new position of $10K+ is a pick at Hyperliquid's mid, scored at 14 and 30 days.

Where it shows: the dashboard's **Traders** tab (by coin; what they are adding; each trader with group, record,
leverage and positions; filters for proven, riding and leverage 5x or less), `GET /position-traders`, and `/positions`
in Telegram (holdings by coin and what they added this week).

## Outrider's read (Telegram)

With a model key, Outrider reads the market instead of posting the same numbers on a clock (`synth/analyst.py`):

- **Scan, every poll (code):** BTC and ETH as direction; the market's mood; where the market stands in its cycle as
  Reflex's market history Chart view reads it (`synth/turns.py`, from the market history fetched once a day, no extra
  Reflex reads): a broad uptrend (55%+ of coins in uptrend) or a downturn, its day against past ones, its high or low,
  the median z and its 14-day change, and the marks (greed or extreme fear in the last 30 days, the crowd 86%+ long or
  30% or less, the median z turned, rolled over or turned up), with the last run's turns; the 14 coins where most is happening, each
  with what the crowd of profitable traders, the tracked traders (and what they added or cut in 24 hours), big money
  by owner and the open winners hold, the engine's reading and Derive's options; and your own positions with what is
  against each.
- **When it reads:** the morning read after `SYNTH_BRIEF_HOUR_UTC` (always sent); when enough changed since the last
  read (groups flipping, several tracked traders piling into one coin, BTC moving, new alerts, the cycle flipping or one
  of its marks lighting), at most every 2 hours and not during `SYNTH_READ_QUIET_UTC` (default 22-6). Your positions
  never bring a read of their own (owner, 3 November 2026: they are your decisions); a proven trader's conviction bet
  or a build-up of tracked traders against one of them comes as a one-line note on that alert, and `/mine` gives a
  briefing when you ask.
- **What it writes:** a headline, at most three sentences on what it sees and how much it is worth (the studies'
  evidence guide, which includes what Reflex's market-cycle studies found and how little it rests on), and up to two
  setups to look at: a coin, long or short, light, firm or strong, why, what to look at on the chart (the band, the
  200-day average, a swing low, a pattern's neckline) and what would change it. A setup needs two independent things
  lining up (traders' positioning with price action, a chart pattern or the engine), not everything. Before a read,
  each coin gets its price action (`synth/levels.py`) and the chart patterns Reflex's chart finds on its daily candles
  (forming, or confirmed or failed in the last 30 days, with their track record). One call with no tools, and it
  holds a read back when nothing is worth your attention. The record rides on the morning read.
- **Watch list:** positions or coins you want followed whatever your wallets show. `/watch ENA long 0.09 5k` (side,
  entry and size optional; `/watch HYPE` follows a coin), `/watch` lists them with the price, the move since entry,
  the groups on them and what is against you (built in code, no model), `/unwatch ENA`. In the chat, "I'm long ENA
  from 0.09" does the same. Watched positions count like wallet positions: an alert against one says so.
- **Its record:** every view sent is logged at Hyperliquid's mid (only a mid read within the last two polls) and scored
  at 1, 3 and 7 days at the price of that hour: from the hourly copy of every coin's price (kept 8 days), or the price
  now while the horizon is under two hours old; never at a later price as if it were the horizon's. The setups since
  S5's Amendment 2 (3 November 2026) are reported apart from the views before it. The verdict (S5 Amendment 3,
  4 November 2026, before any setup had a 3-day result) is judged on separate bets (the same coin and side within 3
  days counts once) and their 3-day move beyond the market: useful only after 60 bets, 62% beating the market, a
  median +1% beyond it, and the lower end of the 95% interval of the mean above zero; until then context, not a
  signal. Each read and `/record` show how far the setups are from that bar, with the raw share right beside it, the
  return after a taker round trip and the funding paid, how often the setup's own off level was touched before the
  horizon, and the move beyond the coin's beta to BTC. `/record` also shows study S6: whether the screen ranks coins,
  whether the read beats the code's own picks and random coins, and whether the screen ranks longs better than
  Reflex's own priority score.
- **Your positions:** `SYNTH_OWNER_WALLETS` (comma-separated Hyperliquid addresses) are read each poll, and the watch
  list is added to them.
- **Cost:** `SYNTH_READ_MODEL` (default `claude-sonnet-5-5`; `claude-haiku-5-5` is cheaper still, `claude-opus-5-5`
  deeper), `SYNTH_READ_EFFORT` (default `medium`), `SYNTH_READ_DAILY_USD` (default 0.5; the morning read still goes
  out), `SYNTH_READS_PER_DAY` (default 3 sent: the morning read and at most two more). About 2 to 3 cents a read on Sonnet 5.5 (a prompt of about 4,000
  tokens). `/read` asks for one now (it answers you, so it never counts against the daily cap or the day's reads);
  `/cost` shows talking, reads and messages sent.
- It replaces the code-built pulse and daily brief below; `SYNTH_READS=off` brings them back. `/now` still shows the
  pulse picture.

### What the read is checked for, in code

- **Its figures:** every level in a setup's chart note, or in what would end it, must be one of that coin's own levels
  in the scan (within 0.75%); a sentence with any other level (a round number, his own entry, another coin's level) is
  dropped before it is sent. Figures in the headline and the read that match nothing in the scan are noted in the
  read's brief (`lint`). No second model call.
- **What brings one:** the crowd's and big money's swings are context (study S2): told to a read, never the reason for
  one. A new setup in the screen (two independent things lining up, liquid) counts for a read.
- **No codes:** the engine's regime, signal and consensus codes reach the model in words.
- **Paths:** each scored setup's worst and best move and whether its off level was touched use Hyperliquid's hourly
  highs and lows (one candle request per pick), not only the hourly closes.

### Pictures, at no cost

`synth/chart.py` and `synth/pictures.py` draw in code (matplotlib, no paid service), sized for a phone: 1,800 px
wide, the long side under Telegram's 2,560 for a photo, no text under 8.5 points, in Inter (installed in the image);
rendered off the poll in a worker thread with no shared plotting state. The look: a deep indigo background with light
from the top, vivid mint and coral candles, the band in violet, the 200-day in sky blue, the off level in amber, the
next level in cyan, each level labelled on a tag at the right edge.
- **A setup on its daily chart**, with every read that carries one: the last 120 days of candles, the support band as
  it stood each day, the 200-day average, where the setup is wrong (amber) and the next level (cyan), the price now,
  and under it the daily z-score against the owner's lines once enough of its history is kept (from the daily screen
  log, so it fills over the weeks). One candle request per coin. `/chart SOL` (or `/chart SOL short`) for any coin.
  When tracked traders hold the coin, the crowd reads at a glance:
  - Under the title: who holds it ("Traders 71% long by money", "split" between 45% and 55%, never rounding a
    minority away), a mint and coral bar split by money with each side's traders and dollars, and per side its
    average entry (weighted by money, so by the coins held), the price now against it ("price +10.3% · in profit":
    the price move only, before leverage and funding) and when half its money was in ("half in by 19 Jul", or "over
    half from before tracking").
  - On the chart: each side's average entry as a solid line from that day, and its positions as triangles (up mint
    for longs, down coral for shorts) at their open day (by UTC day, on its candle) and average entry. Their area is
    proportional to money. Triangles that would cover each other merge, then down to four a side but only within
    three weeks of each other, at their members' money-weighted day and entry, with the number of traders on or beside
    them where it covers nothing else. The panel widens at either end so every triangle sits whole.
  - Under the chart: the money in positions opened each week (the last week ending today), longs up and shorts down.
  - Positions opened before the 120 days shown, or before tracking began, sit in an "earlier" column at the left,
    one triangle a side. An entry or an average far outside the candles (more than 35% of their range) is pinned to
    the edge with its price, so one old position never squashes the candles.
  - A tall page renders at a lower dpi so its long side stays within 2,560 pixels.

- **The market map**, with the morning read: every screened coin by where it sits against its band and its daily
  z-score, the owner's lines drawn, the corners named (stretched over the band, washed out under it), long candidates
  mint and short coral with a halo, sized by open interest, names placed so none covers another. `/map`.
- **The record against its bar**, with `/record`: the setups' running share right and share that beat the market
  against S5's 62% and 60 needed, and each setup's 3-day move with the running median against +1%. Never a summed
  return curve.
- **Chart patterns** Reflex finds on a setup's coin are drawn on its chart: the pattern's shape, its break level
  (labelled) and its name; the shapes never reach the model.
- **The market cycle** (`/cycle`, and on Monday's morning read): the share of coins in an uptrend from Reflex's daily
  history, past runs shaded, BTC beside it, the median z-score under it.
- **Breadth over time** (`/breadth`, and on Monday's morning read): the share of coins over their band and over their
  200-day for the last 90 days, rebuilt from each coin's own candles.
- A read's text and its pictures go as one message (an album; a long text first, the pictures silently under it), and
  count once against the daily cap. Dashboard: `GET /map.png`, `/setup/{coin}.png`, `/journal.png`.

### Your positions, in code

`/mine` lists his wallet positions and watch list in code, at once and at no cost: entry to now, size and share of the
account, leverage, how far the liquidation is, where the price sits against its band and its z-score, and under each
only what the studies count against it (a conviction bet the other way in 7 days, two or more tracked traders adding
the other way). `/mine more` asks the model for its read on them.

### The board and why a read came

The morning read opens with the board, built in code and the same lines every day: where the market cycle stands, BTC
and ETH against their band with their z-score, the breadth, and the coins at the z-score's extremes (`/board` any
time). Every read's first line says why it came (morning, asked, or the market change that brought it) and the model's
lean on the market.

### The screen: every liquid coin against its levels

`synth/screen.py`, from what the agent keeps (no model): the most traded Hyperliquid perps and wherever traders are
(levels read once a day after 00:30 UTC, a few seconds apart; prices from the last poll), each with its band state and
distance, its 200-day average, how much of its last leg it has given back (or of its last drop it has bounced), its
nearest swing low and high, its volatility, strength against BTC and beta, open interest against price, funding, how
thin its book is, the engine's daily reading, what tracked traders hold, added in 24 hours and bet with conviction in 7
days, and for each side a score with the reasons and a risk frame.
- **The band** (owner, 3 November 2026): inside it is testing it, a zone to watch, not a block; just above (within 5%)
  is holding it; 15% or more above is extended, not an entry; under it the trend is lost until reclaimed. The band is
  built on Monday-to-Sunday weeks (TradingView's crypto weeks) from 1,000 daily closes, the forming week included;
  Hyperliquid's own weekly candles start on Thursday and gave a different band near its edges.
- **The z-score** (owner, 4 November 2026: the first thing for shorts): every coin's daily and 4-hour engine z-score,
  read from Reflex's scan list once an hour. The owner's lines: 2.5 blow-off, 3 trim, 3.5 hard trim, -1 capitulation
  (the engine scales its own by volatility, 0.85x to 1.25x, and trims only in its blow-off regime; a read sees the
  engine's own lines for a coin when it has its full row). A coin stretched past 2.5 is a short setup on its own route
  (35, 40 past 3, 45 past 3.5, plus 10 when 15%+ over the band, no downtrend needed: it fades a run), lifted by shorts
  building and longs paying funding; a long on a stretched coin loses 15.
- **In the coin's own volatility:** "near" a level is within one 14-day average true range (between 1% and 5%), not a
  fixed 3%. Each row carries the average true range, 30-day realised volatility and the last day's volume against its
  median.
- **Which coins:** the 100 most traded by 24-hour volume (re-picked daily, so it follows the market) plus every coin
  where traders are: what tracked traders hold or added in 7 days, conviction bets in 7 days, coins 5+ profitable
  traders hold, and the watch list (up to 180).
- **The score** (a ranking of what is worth a look, not a tested signal): where the price sits (up to 55: in or just
  over the band 35, near the 200-day 25, on a swing low 20, plus 10 for a 30-70% pullback; a stretched short 35-45);
  who is positioning (a conviction bet 20, two or more tracked traders adding 10, their lean 5, open interest against
  price 5); the engine (15, or -10 against); crowding on the day's average funding (-5, or +5 for a short when longs
  pay 30%+ a year); strength against BTC over 30 days (+5 or -5). A long under its band or extended over it, or a short
  15%+ under it, gets no location points.
- **Funding in context:** each coin's funding against its own last 20 days (in standard deviations; one Hyperliquid
  request per coin with its daily levels) and against Binance and Bybit (one request an hour for every coin): a jump
  for that coin counts as crowding, and funding high only on Hyperliquid is local leverage.
- **Breadth over time:** each coin's band and 200-day state for each of the last 90 days is rebuilt from the candles its
  levels already read, so the board says where breadth was a week ago (today's coins only, so it carries survivorship).
- **Two independent things:** each side's reasons are kept by family (price at a level, traders positioning, the
  engine, which counts only beside one of the others). A candidate needs two, 40 points, at least $3M a day of volume
  and a book no wider than 15 basis points.
- **Risk frame:** for each side, where it is wrong (the level the setup sits on, in % and in average true ranges), the
  next level the other way and the room to it over the risk. Each setup a read sends shows them, built in code.
- **Where it is used:** each read gets the 6 best candidates and the market's breadth (coins under, inside and over
  their band, over their 200-day, stretched and washed out) and picks its setups mostly from them; every coin in a read
  carries its screen figures; the chat's `screen` tool answers any question across coins in one look (sort by score,
  band, 200-day, retracement, open interest, funding, z or strength); the dashboard's **Levels** tab shows the table;
  `GET /screen?sort=band&band=inside`.
- **Kept for a test:** once a day after the levels refresh, every coin's row as the screen saw it (scores by family,
  risk frames, band, z, strength) goes into `screen_log`, and at each read the code's top two candidates and a random
  liquid coin on each view's side are logged as shadow picks (never sent). Study S6 (declared 4 November 2026)
  would test from these whether the screen ranks and whether the read adds anything over the code and over chance.
  Its Amendment 1 (4 November 2026) also tests the screen's long ranking against Reflex's own `priority_score`, kept
  daily per coin from Reflex's scan list (`priority_1d` in the screen log) and left out of the read's candidates.

### Open interest, funding, the 200-day average and the support band

`synth/levels.py`, from Hyperliquid's public API (no key, no cost):
- **Each poll** one read of every perp's price, open interest, funding and 24-hour volume; an hourly copy is kept 8
  days, so the read gets open interest's change over 24 hours and 7 days (in coins, so a price move is not new money)
  and funding now and over the last 24 hours (a year's worth in percent; above zero longs pay). The market block adds
  the change in open interest across the 50 most traded perps and their median funding. The changes appear once a day
  (and a week) of copies has built up: Hyperliquid keeps no open interest history to fill them from.
- **Price action**, from the same daily candles: the high of the last 120 days, where the leg up to it started, how
  much of that leg the price has given back, the nearest swing low under it and swing high over it, and which levels
  (band, 200-day average, swing low) sit within 3%.
- **When a read runs or a coin is asked about**, the 200-day simple average from daily candles and the bull market
  support band (20-week simple and 21-week exponential averages of weekly closes, the forming bar included as on
  TradingView), read once a day per coin: where the price sits against each (above, inside or below the band, and how
  far).
- Context, not a signal: none of these is tested in the studies; the evidence guide says how to read them.

## Daily brief (Telegram, after 08:00 UTC; only with SYNTH_READS=off or no model key)

The brief has three short sections and is under 15 lines:
- **Derive smart wallets:** BTC, ETH, HYPE. One line each: the lean of top option wallets (7d, else 30d), their net delta, and whether calls or puts were bought today. Built in code.
- **Hyperliquid today:** where profitable traders are opening. Convergences from the last 24 hours come first, fastest at the top, then the busiest coins by new opens. Built in code.
- **Open groups:** smart traders who converged and are still in, with how many are still in, their entry range, the price now, and the losing line. Built in code; there is no model in the brief.

What pings Telegram (owner, 3 November 2026: only essential and important updates): the reads (the morning read, a read when a group turns against one of your positions, at most two more a day), conviction bets of proven traders, and updates on groups you follow. `SYNTH_PUSH` lists the alert kinds that ping (default `conviction`; add `build`, `smart`, `group`). Above all of it, `SYNTH_MAX_PUSHES_PER_DAY` (default 6) caps unprompted messages over any 24 hours: past it they are kept as held (briefs `push_held`), not sent: `/held` lists them and the morning read says how many; the morning read, a read you ask for and a note against one of your positions always go (a read you ask for answers you and does not count). Every scheduled sender (the code-built brief and pulse when reads are off, X drafts when on) goes through the same cap; a send Telegram fails does not use up the allowance, and a long message is split, never cut. A read on your position comes once per reason per day (a count moving, 4 traders to 5, is the same reason), at most 3 a day. A muted kind is still logged, scored, shown on the dashboard and read by Outrider. With `smart` on there are at most 2 alerts per poll and `SYNTH_MAX_ALERTS_PER_DAY` (default 6) per 24 hours.

Reflex flags a convergence when 2+ profitable traders open the same side within 24 hours. The agent alerts only on fast ones, where all the opens came within `SYNTH_FAST_CONVERGENCE_HOURS` (default 6). Slower ones stay on the dashboard and in the pulse but don't ping you.

## Talking to Outrider

Write to the bot in plain words and Outrider answers from the agent's own data (`synth/chat.py`): the market now, one
coin (engine, profitable traders, tracked traders, big money, Derive options, open winners, recent adds and alerts),
tracked traders, one trader by address or codename, open winners, where money is moving, recent alerts, the studies,
your positions and its latest read. It has views: ask what it would do with a position and it says so, with how strong
the evidence is and what would change its mind (the same evidence guide as the reads). It keeps short notes when asked
("remember I hold ENA") and never trades or posts. The desk's Ask tab uses the same conversation, kept apart.

- Needs `ANTHROPIC_API_KEY` on Railway (without it the bot says so and nothing else changes).
- `SYNTH_CHAT_MODEL` (default `claude-sonnet-5-5`, with Anthropic's server-side refusal fallback), `SYNTH_CHAT_EFFORT`
  (default `medium`), `SYNTH_CHAT_DAILY_USD` (default 1: talking stops for the day at that spend, 00:00 UTC reset).
- About 2 to 4 cents an answer on Sonnet 5.5 (tool rounds read the earlier rounds from the prompt cache); `/cost`
  shows today's spend, `/reset` starts afresh (notes stay).
- Each conversation keeps its last 6 exchanges as plain text; tool results and reasoning are not carried between
  turns, so a long chat stays cheap.

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
- **Off for now** (owner, 3 November 2026): no X drafts (persona posts, the daily desk post, the Monday scorecard)
  unless `SYNTH_X_DRAFTS=on`.
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

On Mondays a scorecard built in code (no model) posts the 7-day results: hits, average move, best and worst, misses included.

- `SYNTH_POST_MODE=draft` (default): posts are stored and sent to your Telegram. `POST /posts/{id}/publish` approves and publishes one.
- `SYNTH_POST_MODE=live`: posts publish through Orbio's gateway (`social.post`, about $0.019 per post).
  - Needs `ORBIO_API_KEY`, and the desk X account connected in Orbio's dashboard (only the owner can do that).
  - A post is never sent twice.

The agent has no token. Launching one on Orbio waits until the posts show they are useful.

## Dashboard (the desk)

Open the service's root URL (`/`). If `SYNTH_API_KEY` is set, the page asks for the key once and keeps it in the
browser. Bright, chart-first, dark and light, phone and desktop (`synth/static/dashboard.html`, `synth/desk.py`).

- **Desk** (home, `GET /desk`, `GET /desk/board`):
  - the latest read's headline and the board (the same lines as the morning read), then Outrider's setups of the
    last 14 days: entry, price now, the move its way, where each is wrong, and its 1, 3 and 7-day results;
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
  verdict (S1 to S4, where the money came from), plus the forward record.
- **Journal:** brief, picks and desk posts. Picks opens on the record of every pick logged, by kind (Outrider's views,
  tracked traders' conviction bets and their other new positions, groups, alerts, posts): share right and median move
  at 1, 3, 7, 14 and 30 days, and under each the same against the market, longs and shorts apart; then how Outrider's
  reads went over 7 days (ran, sent, held back, failed, with no view); then the picks, filtered by kind. `/record` on
  Telegram sends the same.
- **The market** (`synth/market.py`): an equal-weight basket of the 50 most traded Hyperliquid perps, one reading an
  hour, rebuilt once from hourly candles back to the first pick. A pick beats the market when its move its way is
  better than the basket's over the same days (`mkt_1d` ... `mkt_30d` on each pick).
- **Ask:** a question answered from current data (uses the model).

## API

| Route | |
|---|---|
| `GET /health` | Liveness and last poll stats (no auth); `?strict=1` answers 503 when the poll has stalled or Reflex has failed for 30 minutes, for an outside monitor |
| `GET /status` | Every source and job: when it last worked, its last error, how old each reading is, Hyperliquid's weight budget |
| `GET /backup` | A consistent copy of the database (the picks, the reads, the trader lists) |
| `GET /map.png`, `/setup/{coin}.png`, `/journal.png` | The market map, a coin's setup chart, the record against S5's bar |
| `GET /` | Dashboard |
| `GET /pulse?hours=1` | Last hours' events by coin, with the pulse text |
| `GET /board` | Smart money board: where the layers agree per coin |
| `GET /events?hours=24&coin=&min_score=` | Detected events |
| `GET /coin/{coin}` | Both products' current view of a coin, plus 7 days of its events |
| `GET /brief` / `POST /brief?push=true` | Latest daily brief / write one now |
| `POST /ask` `{"question": "..."}` | Question answered from current data and the last 7 days of events |
| `POST /read?send=false` | Outrider's read now (send=true pushes it to Telegram) |
| `GET /reads?days=3` | Recent reads, sent or held back, and the record of their views |
| `GET /picks?origin=read` | Pick journal with 1d/3d/7d returns (origin: read, position, group, alert, post) |
| `GET /record` | Every pick logged, by kind: share right and median move per horizon, longs and shorts apart |
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

If `SYNTH_API_KEY` is set, every route except `/health` needs the header `X-API-Key`.

## Keeping it running

- **One Hyperliquid budget** (`synth/hyperliquid.py`): every read in the process (the poll, the trader refresh,
  discovery, the levels, the market index, charts) draws on one weight budget of 900 a minute, under the info API's
  1,200 per IP, so jobs that overlap never exceed it; a 429 pauses them all for half a minute.
- **Status:** each source and job (Reflex, Derive, Hyperliquid, the levels refresh, discovery, the market index,
  backups) keeps when it last worked and its last error. `/status` on Telegram and `GET /status` show them with the
  age of every reading; a source down for two hours sends one message (counted against the daily cap), and none on
  recovery. Point a free outside monitor (cron-job.org, as for Cowboy) at `GET /health?strict=1` to hear when the
  service itself stops. Railway's deploy check keeps using the plain `/health`.
- **Reads off the poll:** a read runs as its own task (the model's slowness never holds up the poll; client timeout 2
  minutes, one retry), one at a time; `/read` while one runs says so. A morning read that fails three times says so
  once. Telegram updates are handled as they come: a long answer never holds up a button or a command, and answers go
  out in the order asked.
- **Backups:** once a day a copy of the database goes beside it (`backups/outrider-YYYYMMDD.db`, the last 7 kept);
  `GET /backup` takes one off the machine. The S5 record, the reads and the trader lists cannot be rebuilt.
- **Missed market hours:** when the agent was down for more than two hours, the market index fills the gap from
  hourly candles, bent to land on the readings around it, so picks in the gap get their market move.

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
