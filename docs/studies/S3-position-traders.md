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
