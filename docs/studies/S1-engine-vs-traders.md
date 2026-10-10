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

## Amendment 1 (10 October 2026, after the fill download began, before any convergence or outcome was computed)

The owner pointed out that a roster wallet with about 20,000 fills since 26 September is a market maker or a
bot, not a trader. The volume-to-account-value filter does not catch large accounts. R0 therefore also
excludes, as Reflex's live tracker does:
- wallets with more than 2,000 fills in the window (about 140 a day);
- wallets that at any moment hold more than 25 coins at once (Reflex's "at most 25 positions").

Results are reported with these exclusions (headline) and without them (as declared originally), side by side.

## Amendment 2 (10 October 2026, replaces amendment 1's tests; before any outcome on the full roster)

A partial smoke run on 18 wallets was seen (one convergence with every wallet, none after amendment 1), too
few to inform anything. The owner pointed out that Hyperliquid splits one order into many fills (10-20 for
about $10,000, hundreds for $1 million), so the fill count measures order size, not behaviour: a large trader
scaling in several times a week reaches thousands of fills. What separates a bot from a trader is whether its
fills come in one-direction bursts that stay put, or small trades that keep reversing.

So fills are first grouped into **trades**: same wallet, same coin, same side, no gap over 2 minutes between
fills. A wallet is **bot-like**, and excluded, if over the window it:
- averages 40 or more trades a day; or
- averages 10 or more trades a day and at least 25% of its trades reverse the previous trade on the same coin
  within 15 minutes.

The two amendment 1 tests (more than 2,000 fills; more than 25 coins at once) are dropped. Behaviour on the
first 67 active wallets, before this rule was fixed and with no prices used: the 20,000-fill wallet made 270
trades (19 a day, about $20,000 each, 93% selling, 1% reversals), a trader building a position; the clearest
bots made 200-400 trades a day of a few hundred dollars, or reversed nearly half their trades.

Results are reported with these exclusions (headline) and with none (as declared originally), side by side.

## R0 results (10 October 2026; descriptive, no verdict)

Only 72 of the 300 roster wallets traded on Hyperliquid perps since 26 September; amendment 2 marks 12 of
them bot-like (the 20,000-fill wallet stays: a trader). Returns are in the trade's direction, from hourly
closes, median and share positive.

| | Bots excluded (headline) | All 72 (as declared) |
|---|---|---|
| Convergences | 10 on 7 coins (5 long, 5 short), all of 2 wallets | 21 on 13 coins (16 long, 5 short) |
| Convergence 1d / 3d / 7d | +0.16% 50% / +0.71% 56% (n 9) / -0.16% 50% (n 8) | -1.12% 38% / -1.34% 38% (n 16) / -1.70% 46% (n 13) |
| Fast (6h or less) 1d / 3d | n 3: +0.47% / -5.30% | n 8: -1.24% / -3.92% |
| Single opens 1d / 3d / 7d | +0.16% 54% / -0.52% 46% / -0.80% 45% | 0.00% 50% / +0.19% 52% / +0.64% 53% |

Context, not a signal: n is far below Part L's minimums, the window is two weeks of one market, and every
convergence is two wallets. With bots included, convergences look like Reflex's history (3d -0.82%, 42%
positive); without them they are flat, and no better than single opens.
