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
