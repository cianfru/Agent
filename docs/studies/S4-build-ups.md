# S4: Build-ups: when several traders add to one coin while its price is still, does it move their way? (declared 10 October 2026, before any S4 result)

The owner's question, from NEAR: profitable traders bought it for weeks while the price sat around $1.6 to $2.4, held,
and then it ran to $5.3. Is that a pattern, or do we only remember the time it worked? The agent now alerts on this
shape (a build-up); this study measures how often it was followed by a move their way, and how often it fizzled.

Known before declaring: NEAR's cluster (roster traders holding longs opened in the last 30 days) grew from 4 to 18 traders
between 20 August and 3 September with the price flat (1.73 to 1.89), then the price went to 5.34 by 1 October. That is
one case, and it is the case that made the question; it falls in the period the headline leaves out.

## Data

S2's: every fill since 1 October 2025 for Reflex's roster rule (headline) and S1's roster (secondary), with S2's fixes
(complete histories only, no TWAP slice before the window, wallet-coin histories whose end disagrees with the live
position left out, bot-like wallets out). Hyperliquid 4-hour candles; the equal-weight basket as in S2.

## Event: a build-up (the live alert's rule)

- **Adds and cuts:** each fill that grows a position is an add of the dollars it adds (size times fill price) on that
  position's side; each fill that shrinks it is a cut; a flip is a cut and an add.
- At each 4-hour close t, per coin and side: the traders whose net adds over the 72 hours to t are at least $25,000.
- **Build-up:** at least 3 such traders, at least $250,000 net in all, and the coin's price moved less than 15% (either
  way) over those 72 hours.
- An event is the first close where this holds; the same coin and side counts again only 14 days later.

## Outcomes

- The coin's move in the build's direction 7, 14 and 30 days after t, raw and against the basket (excess).
- **Big move:** the price went at least 30% their way at some point within the 30 days. **Fizzle:** it went 15%
  against them first.
- **Base rates:** the same measures over every coin-bar of the same months, on the same side (all bars, long; all bars,
  short), so "30% within a month" is compared with how often any coin did that.

## Headline period and bias

Events up to 10 August 2026, so 30-day outcomes end before the month the roster was picked on (10 September to
10 October). Later events (NEAR among them) are reported apart, as biased.

## Verdict (headline, long and short build-ups pooled, direction-adjusted)

- **"Build-ups lead"** if the median 30-day excess is at least +3 points, at least 55% of events beat the basket, the
  big-move rate is at least 1.5 times its base rate, with at least 30 events from at least 10 coins.
- Otherwise context, not a signal. The roster's selection favours finding them right, so a positive verdict here is
  confirmed only by the live alert's forward record.
- Secondary (no verdict): S1's roster; long and short apart; NEAR's cluster rule (traders holding longs opened in the last
  30 days doubling to at least 8 within 14 days while the price moved under 15%).

## Results (run 10 October 2026)

**Verdict: context, not a signal.** A build-up does not lead to a move their way more often than chance, and it does
not produce more big runs than any coin.

| Headline (to 10 August) | Events / coins | 30 days their way | vs basket | Beat basket | Big move (+30%) | Fizzle (-15% first) |
|---|---|---|---|---|---|---|
| All build-ups | 217 / 28 | -3.5% | +0.0 pts | 50.5% | 17.1% | 43.8% |
| Long build-ups | 147 / 28 | -7.0% | **+4.0 pts** | **60.3%** | 17.7% | 46.3% |
| Short build-ups | 70 / 7 | +0.1% | -4.7 pts | 30.0% | 15.7% | 38.6% |
| Base rate, any coin, long | 57,748 / 196 | -8.9% | -1.1 pts | 46.2% | 16.1% | 54.6% |
| Base rate, any coin, short | 57,748 / 196 | +8.9% | +1.1 pts | 53.8% | 22.1% | 39.1% |

- **Long build-ups pick coins that hold up better than the market** (+4 points against the basket over 30 days, 60% of
  them beat it, and fewer fizzle), but in a falling market they still fell, and a +30% run was no more common than for
  any coin (17.7% against 16.1%).
- **Short build-ups lost** against the basket.
- **NEAR's cluster rule** (secondary): 26 events on 9 coins in the headline, mostly BTC, ETH, HYPE and SOL: +6.6 points
  against the basket, 69% beat it, fewer fizzles (23%), but fewer big runs (11.5%). It fired on NEAR on 29 August
  (+196% in 30 days) and on XPL on 4 September (-1%), both after the headline cut-off.
- **After 10 August (biased):** +14.3% at 30 days, big moves 37.7%: the rally and the month the traders were picked on.
- **S1's roster:** too few events (54 on 8 coins).

What it means: when several of these traders build a long, the coin tends to do better than the market, which is
useful context for which coins to favour. It is not a pump detector. NEAR was the exception that made the question.
