# S3: Position traders: who holds size for weeks, what they hold now, and is it worth following? (declared 10 October 2026, before any S3 result)

The owner's scope: not scalpers, not the crowd's lean over 1 to 7 days. The traders who take a position with size and
modest leverage and keep it running through the noise (like the FET holder who opened before the run and still holds),
with a strong record of doing it. Who are they, what do they hold now and since when, what is their strategy, and do
their new positions pay over the horizons they trade? Later: line them up with Reflex's signals.

**Known before declaring** (`anatomy.py`, description, S2 headline roster, closed positions since 1 October 2025):
positions held over 7 days made $62M of the roster's $84M; the best 1% of positions made $97M and the other 99% lost
$13M together; 80% of the profit came in August to October 2026, around the month the wallets were picked on.

## Data

The S2 fills (Reflex's roster rule and S1's roster, every fill since 1 October 2025, the S2 fixes applied: complete
histories only, no TWAP slice before the window, wallet-coin histories whose end disagrees with the live position left
out), live positions on 10 October (E1 pull), Hyperliquid 4-hour candles. Bot-like wallets out (S1 amendment 2). The E1
big-account pool joins when its pull is complete (100 days of fills only, so its records are shorter).

## Definitions

- **Position:** a wallet's coin from flat back to flat (a flip closes one and opens the other), opened inside the window
  so its entry is known. Size: its largest value while open. Return: realised PnL over that size (leverage aside).
  PnL by average cost, before fees and funding.
- **Swing position:** held 3 days or more.
- **Position trader, proven at time T** (only positions closed before T count):
  - at least 5 swing positions;
  - swing positions are at least half of the size it traded (sum of position sizes);
  - swing PnL above zero with a profit factor (wins over losses, in dollars) of at least 1.5.
- **Leverage now:** open position value over account value (live read). Moderate: 5x or less. Shown, not a filter.
- **Conviction position:** opened at least twice the trader's median position size (from its record before T).

## Part 1: who they are and what they hold now (description)

Proven as of 10 October. For each: record (swing positions, PnL, profit factor, win rate, median hold, best trade),
style (long or short bias, coins, adds to winners or averages down), and every open position: coin, side, size, entry,
held since, unrealised PnL, leverage now. Per coin: how many proven position traders are long and short, and dollars.

## Part 2: their strategy (description)

On their swing positions: entry after a run or against it (the coin's move over the 7 and 30 days before), how far
price went against them while they held (worst point), how long they held winners against losers, whether they add to
winners, where they exit against the coin's later path (does the exit sit near a local top or bottom).

## Part 3: are their new positions worth following? (test)

- **Cut-offs T:** 1 January, 1 March, 1 May, 1 July 2026. Proven set from the record before T.
- **Positions followed:** every position opened in the 2 months after T by a proven trader (any length, since a
  follower cannot know at entry how long it will run); a wallet's re-entry on the same coin and side within 7 days
  counts once.
- **Control:** positions opened in the same months by roster traders with at least 5 closed positions who are not
  proven.
- **Outcome:** the coin's move in the position's direction 14 and 30 days after the open (what a follower holding that
  long gets), raw and against the equal-weight basket. Their own realised return at exit is described beside it.
- **Headline period:** positions opened up to 10 August 2026, so 30-day outcomes end before the month the roster was
  picked on. Later positions are reported apart as biased.
- **Verdict (30 days, pooled cut-offs):** "proven position traders are worth following" if their median move their
  way is at least +3%, at least 55% are positive, the median beats the control's by at least 2 points, with at least
  60 positions from at least 15 traders. The same bar for conviction positions with at least 30 positions from 10
  traders. Otherwise context, not a signal.
- **Bias:** the roster was picked on its last month, and wallets that blew up are not in it. That favours finding them
  right, so a positive verdict here is confirmed only by the forward record (the agent logs proven traders' new
  positions from now on, scored at 14 and 30 days).

## Results (run 10 October 2026)

Data: 585 wallets (both rosters; 6 bot-like and 7 incomplete out), 45,258 closed positions, 1,473 open now; 269
wallet-coin histories left out by the end check. **66 traders are proven position traders today.** At the
cut-offs: 7 (January), 14 (March), 14 (May), 29 (July).

### Part 3: is it worth following? (test)

| Positions opened (headline, to 10 August) | n / traders | 30 days their way | positive | vs basket |
|---|---|---|---|---|
| By proven position traders | 340 / 28 | -0.58% | 48.8% | +0.66% |
| **Conviction** (2x their usual size in the first 4 hours) | 40 / 10 | **+3.43%** | **65.0%** | +0.38% |
| Control (other traders with 5+ closed positions) | 8,617 / 262 | -0.85% | 48.1% | +0.93% |

- **Proven traders' positions in general: context, not a signal.** Following everything they open does no better than
  the control.
- **Conviction positions meet the declared bar** (at least +3%, 55% positive, 2 points over the control, 30 positions
  from 10 traders). Read with what came with it:
  - by cut-off the 30-day median runs +0.8% (January), -4.1% (March), +21.0% (May), +8.7% (July);
  - 3 traders make 29 of the 40 positions; without the largest, 27 positions, +9.8%, 70% positive;
  - against the basket the edge is +0.4%, under the control's +0.9%: most of the gain is the market's direction, not
    coin choice; 22 longs and 18 shorts, mostly BTC, ETH and SOL.
  - As declared, the roster's selection favours this result, so it stands only once the forward record confirms it.
    **Promising, not proven.**
- After 10 August (biased, the month they were picked on): every group, the control included, rose with the rally
  (+12.7% to +14.3% at 30 days). It says nothing about skill.

### Part 2: their strategy (description, 1,560 swing positions by the 66)

- **Entry:** neither chasing nor fading: the coin's move their way over the 7 days before is -0.2% (median, half
  between -7.7% and +8.6%); over 30 days +1.9%, slightly with the month's trend.
- **They sit through drawdowns:** the worst point while held is -6.1% (median; a quarter worse than -11%); their
  winners went -4.9% against them before paying.
- **They hold winners longer than losers:** 10.6 days against 7.7.
- **Per position the gains are modest:** winners +6.7% of size, losers -5.1% (medians). The money is size on the
  winners and a high win rate on swing positions.
- **70% of their size is long.**

### Part 1: who and what now (description)

The list (record, style, every open position with since-when, unrealised PnL and leverage now) is in the run's
output. Examples: one trader with 13 swing positions, 92% won, $4.8M, holding 15 days on the median, now 0.2x
leverage; another long LIT since 8 June (+$1.5M) and short WLFI since 18 February. Coins with the most proven
position traders' money now: NEAR (7 long, $32.6M), ZEC (8 long $5.3M against 6 short $12.1M), BTC (11 long, 5
short), HYPE (14 long). Three of the 66 hold FET long, one since 7 September.

### Next

- The agent logs every new position of a proven position trader from now on, marks conviction ones, and scores
  them at 14 and 30 days (the forward record that can confirm part 3).
- A live board of what the proven position traders hold, with since-when, record and leverage.
- E1 (entities) folds wallets of one trader together before the records are counted.

## The live list (owner, 10 October 2026; changes the agent's list, not the verdict above)

Closed positions are not the only record. The owner: "closed positions are not the holy grail"; traders who open near
the bottom and hold for a month, sitting on large unrealised profits, are the ones to know. The NEAR trader
(0x4cae...7c34: +$4.4M, almost all unrealised) was not on the list: on closed positions alone his record was -$53K.
So the agent's list is now:
- **Proven:** this study's rule with open positions held 3+ days counted at their unrealised PnL. 100 traders
  (the 66 above all stay; 34 join, both NEAR whales among them).
- **Riding winners:** $250K+ unrealised profit with one position up 20%+ on its value, in profit all-time, not a market
  maker, found daily from Hyperliquid's leaderboard (accounts of $500K+). No history needed.
Leverage is shown, never used to leave anyone out. Unrealised profit can vanish, so the board says "on paper" and the
forward record (picks at 14 and 30 days) keeps scoring what they open next.

## The list kept current (owner, 10 October 2026; changes the agent's list, not the verdict above)

The rule is fixed; the list is not. `synth/proven.py` reruns the live-list rule above in the background on a pool
rebuilt daily from Hyperliquid's leaderboard (Reflex's profitable-trader rule, accounts of $500K+ in profit all-time,
the traders already listed). New names are read first, listed traders every 2 days (their records stay current), the
rest every 7 days; a first read covers the last 365 days, later reads only the fills since. A proven trader who stops
qualifying stays 30 days, then drops. Bot-like wallets (S1 amendment 2) stay out.

Found while building it: Hyperliquid stamps the pieces of one big order with the same millisecond, and the study's
downloads joined pages one millisecond after the last fill, so a page ending inside such an order lost its other
pieces. On one heavy trader, 30 days lost 2.7% of the dollars traded and 11% of the rebuilt profit. The studies' data
for the heaviest traders may be off by a few percent; the refresh reads fills merged per order and joins pages on
the boundary millisecond, and recomputes every listed record that way.

## Riding a long-held winner, any account size (owner, 12 October 2026; changes the agent's list, not the verdict)

"If a guy is up 200% in unrealised profit I need to know, because maybe he's a long-term trader." Riding winners were
found only among accounts of $500K+ with $250K+ of open profit. Now any wallet the refresh reads (Reflex's roster rule,
$500K+ accounts) is also listed as riding when it holds a position open 14+ days, up 30%+ on price its way and $10K+ in
profit, in an account in profit all-time. Hyperliquid's return on margin is shown beside the price move, never used for
the rule. A list place that rests on open profit (closed trades not in profit) reads "riding a winner" with the trade
that carries it: 13 of the 100 on the original list read that way.
