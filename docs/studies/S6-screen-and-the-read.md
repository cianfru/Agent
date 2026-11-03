# S6: does the screen rank, and does the read add to it? (proposed 3 November 2026, awaiting the owner's approval)

Nothing in this study has been computed. The data it needs is being kept from 3 November 2026; the rules below are
fixed only once the owner approves them, and no result is looked at before that.

## Why

Every read picks its setups mostly from the screen's candidates (`synth/screen.py`). The screen's weights (35 for the
band, 20 for a conviction bet, 15 for the engine, and so on) were set by judgement, not tested. Study S5 scores the
read's setups, but even a passing S5 could not say whether the edge came from the screen, from the model's choice
among its candidates, or from the market's direction. The owner pays a few cents a read for the model's step.

## What is kept (from 3 November 2026, no extra requests)

- `screen_log`: once a day after the levels refresh, every coin's row as the screen saw it: each side's score, its
  reasons by family (price at a level, traders positioning, the engine), its risk frame, band state, daily z-score,
  strength against BTC, beta, volume and whether it was too thin; plus the market's breadth.
- Shadow picks at each read that is sent, never sent to the owner: the code's top two candidates (`screen`), and for
  each view, a liquid coin drawn at random from the screen on the same side (`random`). Logged, priced and scored
  exactly as the read's setups (Hyperliquid's mid, 1, 3 and 7 days, against the market).

## Questions and declared bars (proposed)

1. **Does the screen rank?** For each day in `screen_log`, among coins not too thin: the Spearman correlation between
   each side's score and the coin's next 3-day move that way less the basket's move (synth/market.py). Reported:
   the mean over days, t = mean / standard deviation x square root of the number of days, and the top fifth against
   the bottom fifth. The same for each family's points and for the daily z-score alone (short side).
   **The screen ranks** only when the mean correlation is at least 0.03 with t of at least 2 over at least 60 days;
   otherwise context, not a signal. Weights change only after this verdict, and the change is declared first.
2. **Does the read add to the screen, and the screen to chance?** The 3-day move beyond the market of the read's
   setups, the `screen` shadows and the `random` controls over the same days, each counted in separate bets (the same
   coin and side within 3 days is one), averaged per day. **The read adds** only when its mean beats the screen's by at
   least one percentage point with the lower end of the difference's 95% interval (day-block bootstrap) above zero,
   over at least 60 bets each; the same test, screen against random, says whether the screen beats chance.

## Known limits

- The universe is today's most traded coins plus wherever traders are: coins that fell out of it are missing.
- The random control draws from the same screen, so it shares the screen's universe and its liquidity floor.
- 60 days of `screen_log` is the earliest a verdict can come (early January 2027).
