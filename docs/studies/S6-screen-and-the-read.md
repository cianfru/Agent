# S6: does the screen rank, and does the read add to it? (declared 4 November 2026)

Drafted 3 November 2026 and declared on 4 November 2026, the owner having left the choice to the agent's judgement.
Nothing had been computed: the data is kept from 3 November 2026 and no 3-day result existed. The rules below are fixed;
`synth/s6.py` computes them and `/record` shows how far each question is from its bar.

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

## Questions and declared bars

1. **Does the screen rank?** For each day in `screen_log`, among coins not too thin: the Spearman correlation between
   each side's score and the coin's next 3-day move that way (on one day, the move beyond the basket ranks the coins the
   same way, so it is not needed). Reported:
   the mean over days, t = mean / standard deviation x square root of the number of days, and the top fifth against
   the bottom fifth. The same for whether each family lines up (price at a level, traders positioning, the engine)
   and for the daily z-score alone (short side), as description.
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

## Amendment 1 (declared 4 November 2026): the screen against Reflex's ranking

Asked by the owner on 4 November 2026 ("the way we list priorities for coins is superior to what we use on Reflex"),
declared before anything was computed: no day carries both scores yet, and nothing below had been run on real data.

**Why.** Reflex ranks its scanner by `priority_score` (`backend/scanner.py`, `_compute_priority`): a fixed sum out of
100 (signal tier 40, share of conditions met 20, distance above the band 10, floor confirmed 8, momentum 7, low heat 5,
CVD or spot-led 5, volume or absorption 5). Neither ranking has been tested; the screen's design differs (both sides,
two independent families, the owner's band rule, the risk frame), but a better design is not a better ranking until
the data says so.

**What is kept (from 4 November 2026, no extra requests).** Reflex's daily `priority_score` per coin, read with the
hourly engine reading from its scan's list view (`engine_all`, as `priority_1d`) and logged in `screen_log` with each
coin's row, at the same moment as the screen's scores. It is kept out of the read's candidates, so the read does not
change.

**Question 3, and its bar.** Each day, among coins not too thin that carry both scores and a price 3 days later (at
least 8): the Spearman correlation of the screen's long score with the coin's next 3-day move, and of Reflex's
priority score with the same move, on the same coins. Reflex's score ranks longs only (a short signal scores below
accumulation), so the comparison is on longs; the screen's short side has no Reflex counterpart. Reported: each
ranking's mean correlation and t over days (and Reflex's top fifth against its bottom fifth), and the daily difference
(screen minus Reflex) with its mean and t = mean / standard deviation x square root of the number of days.
- **The screen ranks longs better** only when the mean difference is at least 0.02 with t of at least 2, over at
  least 60 days.
- **Reflex's priority ranks longs better** only when the mean difference is -0.02 or lower with t of -2 or lower,
  over at least 60 days.
- Otherwise context, not a signal. Each ranking also meets question 1's bar on its own or not (mean 0.03, t 2, 60
  days), reported beside.

A verdict that the screen ranks better is a case for porting it to Reflex, which stays the owner's decision; Reflex is
not changed by this study.

**Known limits.**
- The screen's long score already uses Reflex's engine (15 points for its daily setup that way), so the two rankings
  are not independent: the test is of the whole ordering, not of the engine.
- Reflex's score includes its ranking-policy adjustment as Reflex shows it (zero while that policy is in shadow).
- Only coins both rank, on days both were read: Reflex's scan list and the screen's universe (the most traded perps and
  wherever traders are) overlap but are not the same.
- The earliest verdict is 60 days after the first day carrying both scores, about early January 2027.
