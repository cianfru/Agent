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
