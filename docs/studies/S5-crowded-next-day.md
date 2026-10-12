# S5: When the crowd is very long, or most coins are in an uptrend, does the market correct the next day? (declared 12 October 2026, before any outcome was computed)

The owner's question: the share of profitable traders' positions that are long (the Telegram pulse, the desk's
"Crowd long") has been near 80%. Yesterday most were long and today the price is correcting. When the crowd is
very long, say 80% or more, or when most coins are in an uptrend, is the market stretched, so that it tends to
correct the next day?

Everything below is fixed before any outcome is computed. Unless a result clears its bar it is context, not a signal.

## What was seen before this was written

- The motivating day (a correction on 12 October after a day near 80% long) is one observation, outside the data
  below.
- In S2's stored daily series (`synth/data/research.json`) the days at or above 80% were listed before writing
  this: 66 days, almost all between 9 July and late August 2026. No return after them was computed. So test A is
  close to one long episode; it is stated now that it can hardly reach the 15 episodes its bar needs.
- S2 (10 October) already found the share long does not lead the next 3 days and its extremes (top 10% of its own
  trailing 30 days) do not mark tops. S5 differs: a fixed line (80%), the next day, and market breadth.

## Data

- **A. The crowd's share long.** S2 part B's headline daily series (Reflex's roster rule, rebuilt from Hyperliquid
  fills; 2 October 2025 to 10 October 2026), one value per UTC day as stored. It is the same measure as the live
  pulse (positions long over all positions, every coin) on a rebuilt roster, not Reflex's live record. The live
  record (S2 part A) needs Reflex's export and is the better test once available; this test is rerun on it then.
- **B. Market breadth.** For each UTC day: the share of Hyperliquid perpetual coins (no ':' builder markets) whose
  daily close is above the simple average of their last 50 daily closes, counting coins with at least 50 closes by
  that day; days with at least 30 such coins. From Hyperliquid's daily candles, as far back as it serves them.
  Delisted coins count where Hyperliquid still serves their candles. This is a stand-in for Reflex's uptrend share
  (its engine's Uptrend and Overheated regimes), which needs Reflex's market history.
- **Prices.** Hyperliquid daily candles (UTC). BTC: close of day D to close of D+1 (next day) and to D+3. Basket:
  mean of the log returns of every coin with both closes (description only).

## Conditions

- A: share long at or above 80% on day D. B: breadth at or above 80% on day D.
- **Episodes:** the first day of a run at or above the line, after at least 3 days below it.
- Beside it, no verdict: every day at or above the line (persistent, so not independent), and lines of 75% and 85%.
- **Baseline:** every day in the same data's period.

## Bar for "tends to correct the next day"

All of:
1. at least 15 episodes;
2. the episodes' median next-day BTC return at least 0.5 points below the baseline median;
3. at most 40% of episodes followed by a positive next day;
4. the 90% interval of (episode median minus baseline median), from resampling the episodes 10,000 times, below 0.

For 3 days the same with a gap of at least 1 point. A result the other way (higher after crowded days) is reported,
with the same bar, as "no correction; the move continued". Anything else: **context, not a signal**.

Wallet roster bias (S2): the roster is picked on later profit, which favours finding the traders right. A correction
after crowded days says they were wrong, against that bias, so A may give that verdict; "the move continued" from A
is reported as consistent with the bias, not as a verdict. B uses no wallets and has no such bias.

## Results (run 12 October 2026)

**Short answer.** No. A crowd 80% or more long was not followed by a correction the next day more often than any
other day, and neither was a market where 80% or more of coins sat above their 50-day average. In both, the next
day was up about half the time, as on every other day. No result clears its bar: **context, not a signal**.

### Fix made during the run

S2's stored rows are dated by their 00:00 UTC reading: a row dated D is BTC's open of D (it matches the close of
D-1 exactly), so its share long is the reading at the end of D-1. The first run of test A paired it with the move
from close D to close D+1, one day late. Each reading now belongs to the day it closes (D-1), and "the next day" is
the move right after it, as declared. The first run's numbers were seen and point the same way: every day at 80%+
was followed by a median of +0.06% (53% up), against -0.05% (49% up) for all days. B is built from daily closes
and was not affected.

### A. The crowd's share long (2 October 2025 to 9 October 2026, 374 days)

| | Next day, BTC | 3 days, BTC |
|---|---|---|
| All days (baseline) | -0.05% median, 49% up | -0.09%, 50% up |
| Every day at 80%+ (66 days, 8 July to late August 2026) | +0.09%, 56% up | +0.41%, 52% up |
| First day at 80%+ (2 episodes: 8 and 20 July 2026) | +1.75%, both up | +1.12%, 1 of 2 up |
| Every day at 75%+ (110 days) | +0.06%, 54% up | +0.10%, 52% up |
| Every day at 85%+ (30 days) | +0.16%, 53% up | -0.04%, 50% up |

Two episodes cannot reach the 15 the bar needs, as stated before the run: the crowd stayed above 80% for about
seven weeks in one stretch. Over those days the next day was up slightly more often than usual, not less.

### B. Breadth: coins above their 50-day average (19 February 2022 to 11 October 2026, 1,696 days, 41 to 190 coins a day)

| | Next day, BTC | 3 days, BTC |
|---|---|---|
| All days (baseline) | -0.01% median, 50% up | +0.19%, 53% up |
| First day at 80%+ (26 episodes) | +0.41%, 65% up; gap to baseline 90% [-0.16, +1.12] | +0.76%, 62% up; [-0.34, +1.33] |
| Every day at 80%+ (326 days) | -0.01%, 50% up | +0.12%, 52% up |
| First day at 75%+ (25 episodes) | +0.13%, 52% up | -0.23%, 48% up |
| First day at 85%+ (25 episodes) | -0.34%, 48% up | +0.53%, 60% up |

The equal-weight basket the day after an 80% episode: -0.03% median (50% up), against +0.04% (50% up) on all days.
Breadth on 11 October 2026: 77.8%.

The first days at 80% leaned up, not down (65% up), but the interval includes no difference, so it does not
clear the "move continued" bar either. Every day at 80%+ looks exactly like every other day.

### What this means

- A crowd very long, or a market where most coins are trending, says the mood is stretched; it does not say the
  next day turns. After such days the market fell about half the time, as it does after any day. A correction the
  day after a crowded day, like 12 October, is that half, not a pattern.
- This fits S2: the crowd's lean describes the crowd (it buys dips and trims into rallies) and does not time the
  market.

### Limits and next steps

- A rests on one seven-week stretch and a rebuilt roster, not the live pulse itself. Rerun on Reflex's live 4-hour
  record (S2 part A) when exported, and on the agent's own pulse history once it holds months (`S5-run.py`).
- B is a stand-in for Reflex's uptrend share; rerun on Reflex's market history (the desk's market cycle) when it is
  available here.
- Next day and 3 days, BTC only, as declared. Longer horizons or other coins would be a new declaration.
