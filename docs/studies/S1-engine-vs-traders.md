# S1: engine, traders, or both? (declared 10 October 2026, before any data is pulled or scored)

Question: as a lead for the next few days' price, are profitable Hyperliquid traders converging on a coin any
better than the RCCE engine's signal, and is the combination better than either alone?

Everything here is context, not a signal, until a part below reaches its declared verdict. Nothing in this
document changes after data is pulled; a variant needs its own declaration and its own count.

## Part R0: replay of Reflex's convergence forward test, 26 September to 10 October 2026

Reflex's live forward test (`RCCE_Scanner/docs/reviews/trader-convergence-forward-test.md`) has been recording
since 26 September 2026 and owns its own verdict. Its records live in Reflex's database, which this study
does not read. R0 rebuilds the same rule from Hyperliquid's public data for the same period, as description.

**Roster (chosen without looking at the test period).**
- The public Hyperliquid leaderboard (`stats-data.hyperliquid.xyz/Mainnet/leaderboard`), pulled once.
- Market makers removed as Reflex does: monthly volume over 100x account value.
- Account value at least $10,000.
- Ranked by profit **before** the test window: all-time PnL minus the leaderboard's month PnL. That month
  covers about 10 September to 10 October, so the ranking uses only trading before 10 September.
  Reflex's own roster ranks by this month's return, which would select traders for the very trades being
  tested.
- The top 300 with that pre-window profit above zero.

**Opens.** Each wallet's fills from 26 September 00:00 UTC (`userFillsByTime`, all pages). An open is a fill
that takes a coin from flat to a position, or flips its side, on a perpetual market (builder-dex and spot
excluded). It is timed at the fill, at the fill price.

**Convergence (Reflex's rule, unchanged).**
- At least 2 roster traders open the same side of one coin within 24 hours.
- The latest entry is within 5% of the first.
- At most 2 other roster traders already hold that side when the second open happens.
- Each of them still holds the position at that moment.
- Detected at the second open. A cluster counts once (its first detection).
- **Fast:** all of the cluster's opens within 6 hours (the agent's alert rule), reported separately.

**Outcome.** The return in the cluster's direction from the 1-hour close at detection to 1 and 3 days later
(7 days only where the window allows). Hyperliquid 1-hour candles.

**Comparison.** Every roster open that is not part of a convergence, measured the same way from its own fill.

**Reported.** Count, median directional return and share positive per horizon, for convergences, fast
convergences and single opens. Also the largest wallet's share of events. No verdict: the sample is two
weeks and Reflex's forward test owns the verdict.

## Part L: live head-to-head, from 10 October 2026 (recorded by the agent going forward)

**Arms.**
- **A, engine:** an RCCE entry signal on a coin (1D signal changes to STRONG_LONG, LIGHT_LONG or ACCUMULATE
  for long; RISK_OFF, TRIM or TRIM_HARD for short), as read by the agent from Reflex.
- **B, traders:** a fast convergence group (Hyperliquid, all opens within 6 hours).
- **C, both:** A and B on the same coin and side within 24 hours of each other.
- **D, against:** B while the engine's latest 1D signal points the other way.

**Outcome.** The 3-day return in the arm's direction from Hyperliquid's mid at detection, with the 1- and
7-day returns reported alongside.

**Minimum before any verdict.** 30 events in A and in B, and 15 in C, from at least 10 distinct coins each.
Below that the result is "too few".

**Verdicts (3-day return).**
- **Traders lead** if B's median beats A's by at least 1 point and at least 55% of B is positive.
- **Combination helps** if C's median beats both A's and B's by at least 1 point and at least 55% of C is
  positive.
- **Engine alone is enough** if neither of the above holds and A's median is above zero.
- Anything in between keeps collecting until 60 in A and B and 30 in C, then the same rules apply with no
  middle ground.
- D is description only: does going against the engine cost?

## Part P: proven wallets (declared now, scored once each wallet has a record)

A wallet is **proven** once the agent has recorded at least 10 of its closed positions (from its own fills)
with a profit factor (gross wins over gross losses) of at least 1.3. **B-proven:** fast convergences with at
least one proven wallet, judged against B with the same rules and minimums. Wallet records only count
positions closed after 10 October 2026.

## Caveats, stated before looking

- Two weeks is short and one market regime. R0 is description, not evidence.
- Survivorship: coins delisted inside a window drop out.
- Convergences on the same coin a few hours apart overlap; they are counted once per cluster.
