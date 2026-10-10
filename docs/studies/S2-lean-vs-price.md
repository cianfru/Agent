# S2: Does the profitable traders' lean mark tops and bottoms? (declared 10 October 2026, before any data)

The owner's question: the agent now shows the share of profitable Hyperliquid traders' positions that are long.
How does that share relate to price? Earlier studies show these traders are late to trends (they follow spikes,
and keep their longs into tops: Reflex `docs/reviews/traders-vs-spikes-study.md`,
`docs/reviews/traders-turn-at-tops-study.md`). This study looks at the whole picture:
- are they late (their lean follows the past move rather than the next one);
- when they are most short, or most long, does price tend to turn?

Everything below is fixed before any data is pulled. Unless a result clears its bar it is context, not a signal.

## Two data sources

- **Part A: Reflex's live 4-hour record** (`trader_lean`: profitable traders long and short per coin at every 4-hour
  close, kept 400 days, split on `roster_version`). Recorded live, so each reading uses the roster Reflex tracked
  at that moment, with no hindsight. Run when the owner provides an export or a read-only endpoint; Reflex is not
  changed for this without the owner's approval.
- **Part B: a rebuild from Hyperliquid's public records, now.** Like Reflex's tops study:
  - roster: S1's R0 roster (leaderboard, market-maker filter volume / account value > 100, account value at least
    $10,000, top 300 by profit before 10 September), minus the wallets S1 amendment 2 marks bot-like;
  - fills: per wallet, every regular fill Hyperliquid serves since 1 October 2025 (it serves only the 10,000 most
    recent) and the latest 2,000 timed-order (TWAP) slices;
  - coverage: a wallet counts from the later of its two lists' cut-offs (the oldest fill, where a list is full),
    else from 1 October 2025;
  - prices: Hyperliquid 4-hour candles, perpetual coins only (no ':' builder markets, no spot or outcome markets).
  - **Bias, stated now:** the roster is chosen by profit up to 10 September, so before that date these are
    wallets known to have done well. That favours finding the traders right. Part B can therefore give the
    contrarian verdicts below, but "traders are right at extremes" in Part B is reported as consistent with that
    bias, not as a verdict. Only Part A can give it.

## Measures

- **Position** of a wallet on a coin at a 4-hour close t (00, 04, ..., 20 UTC): its position after its last fill on
  that coin before t (rounding below 1e-6 is flat), counted only if the wallet is covered at t.
- **Share long S(t)**: long positions / (long + short positions) over covered wallets.
  - market: all coins together (each wallet-coin position counts once, as in the agent's live pulse);
  - BTC alone; and each coin with at least 10 holders at t.
  - Market bars count only with at least 50 covered wallets and 100 positions.
- **Price**: BTC's 4-hour close, and an equal-weight basket (mean of log returns of every coin with a candle in the
  bar). Returns over the next 1, 3 and 7 days; past returns over 3 and 7 days.
- **Episodes**: the first bar of each run where a condition holds; a new episode needs 3 days since the last one.

## Tests

1. **Late or early (market).** Spearman correlation of the 3-day change in S with the basket's past 3-day return
   (follows) and with its next 3-day return (leads), with a 90% interval from a 7-day block bootstrap.
   "Late to trends" holds if the follow correlation is above 0 with its interval excluding 0, and the lead
   interval includes 0 or sits below it.
2. **Extremes (market).** S's percentile within its own trailing 30 days (no future data). "Most short": bottom
   10%; "most long": top 10%. Next 3-day basket return of the episodes against the median of all bars.
   - **Most short marks local bottoms:** median beats all bars by at least 1 point, at least 55% positive, at
     least 15 episodes.
   - **Most long marks local tops:** median below all bars by at least 1 point, at most 45% positive, at least 15
     episodes.
   - The mirror results ("traders right at extremes") follow the bias rule above.
   - Anything else: context. The same is reported for BTC, description only.
3. **Around tops and bottoms (description only).** Swing tops: a 4-hour close that is the highest within 3 days on
   each side and at least 5% above the lowest close of the 7 days before (BTC: 3%); bottoms mirrored. The median
   path of S from 7 days before to 7 days after. This uses future prices to find the swings, so it describes, it
   does not test.
4. **Coin level.** Coin-bars with at least 10 holders. Most short: S at most 20%; most long: at least 80%. Next
   3-day return minus the basket's. Same bars as test 2, with at least 30 episodes from at least 8 coins.

Wallet concentration (largest wallet's share of positions in each episode set) is reported beside every result.
