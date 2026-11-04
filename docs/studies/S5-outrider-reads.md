# S5: Outrider's reads: are its views on coins right more often than chance? (declared 20 October 2026, before any S5 result)

The owner wants Outrider to reason over the data and say what it sees and what it would do. The model weighs what each
group of traders is doing (the studies say how much each is worth) and gives up to three views per read: a coin, long
or short, light, firm or strong, and what would change it. This study fixes, before any view is scored, how those
views are judged, so a good week cannot be mistaken for an edge and a bad week cannot be explained away.

## What is logged

- Every view in a read that was sent to the owner (a held-back read logs nothing), as a pick with origin `read`: the
  coin, the side, the strength (1 light, 2 firm, 3 strong) and Hyperliquid's mid price when the read was written.
- The same coin and side is logged once a day: a later read repeating it does not add a second pick.
- What it said on the owner's positions (hold, trim, close, watch) is kept with the read, not scored here.

## Outcomes

- The coin's move on Hyperliquid's mid, in the view's direction, 1, 3 and 7 days after it was logged (the agent's
  journal, the same scoring as every other pick). Right means the move went the view's way.
- **Headline:** the 3-day result. 1 and 7 days are reported beside it.

## Verdict

- **Useful** only when at least 60 views have a 3-day result, at least 62% of them were right, and the median 3-day
  move their way is at least +1%. The same three conditions are reported for firm and strong views apart.
- Below that, or before 60 views: context, not a signal. The read and `/record` show the running record; the model is
  told its record before each read, and says so when it is thin.
- The rule stays as written. If it changes, the change is dated here and the views before it are reported apart.

## Known limits

- The views come from a model reading the same data the other studies cover, so it can inherit their weaknesses; the
  evidence guide it reads (synth/chat.py `EVIDENCE`) says which data has tested well and which has not.
- Market drift is not removed: in a strong trend, views with the trend will look right more often. The share of long
  and short views is reported with the record.

## Amendment 1 (27 October 2026): the market beside every view

After the first week (8 views logged, the 6 scored at 3 days all longs, 1 right) the owner asked whether the record
shows anything yet. Two changes to how results are shown; the verdict rule above is unchanged.

- Every pick, Outrider's views included, is also scored against the market (`synth/market.py`): an equal-weight basket
  of the 50 most traded Hyperliquid perps, chain-linked hourly (rebuilt from hourly candles back to the first pick,
  9 October). A view beats the market when its move its way is better than the basket's move its way over the same
  days. Over the 14 days to 27 October the basket fell 8% (BTC 3%), so shorts were right almost whatever was picked.
  The journal and `/record` show "beat the market" beside "right", and longs and shorts apart.
- The record shows how the reads went over 7 days (ran, sent, held back, failed, sent with no view, views logged), so
  a thin record can be told from a broken one.
- Changing the verdict to the market-relative result would be a new dated rule, decided before more results come in.

## Amendment 2 (3 November 2026): views become setups to look at

The owner asked for a companion that points at setups worth a look rather than one that tells him to close his
positions. From this date a read's views are framed as setups: each needs two independent things lining up (traders'
positioning with price action, a chart pattern or the engine), names what to look at on the chart, and reads with
the coin's price action and Reflex's chart patterns. They are logged and scored exactly as before (at most two a read,
once a day per coin and side). The verdict rule is unchanged; views before 3 November are reported apart from the
setups after it when the verdict is computed.

## Amendment 3 (3 November 2026): what is shown beside the record (display only)

From the quant audit. The verdict rule above is unchanged; these are reported beside it, computed the same way for
every setup from this date (and for the earlier views where the data exists):

- the 95% interval (Wilson) for the share right, the number of separate bets (the same coin and side logged within 3
  days of the one before counts once) and the number of days they span: repeated views of one coin on following days
  are one bet, not several;
- the return after a taker round trip (0.045% a side, Hyperliquid's base fee) and the funding the position would have
  paid or received over the horizon, from the hourly copy of every coin's funding;
- the worst move against the setup on the way (from hourly prices, so a little under the true extreme) and whether its
  named off level (the code's risk frame, logged with the setup) was touched before the horizon;
- the move beyond the coin's beta to BTC (beta over the 60 days before the setup, BTC's move over the same hours).

Each setup now carries a risk frame built in code (where it is wrong, the next level the other way, the room to it over
the risk), and every setup must sit on a coin the read was shown; a view on any other coin is dropped and noted.

## Amendment 3, part two (4 November 2026): the verdict on separate bets, beyond the market

Adopted on 4 November 2026, the owner having left the choice to the agent's judgement, before any setup since
Amendment 2 had a 3-day result (the first falls on 6 November). The raw bar above, met in a one-way market by
repeated views of a few coins, could pass with no skill; from this date the verdict is:

- **Unit:** separate bets. One coin and side logged again within 3 days of the one before is the same bet, scored from
  its first entry.
- **Outcome:** the 3-day move beyond the market (the basket of synth/market.py, its move turned to the bet's side).
- **Useful** only when all four hold: at least 60 separate bets with a 3-day market move; at least 62% of them beat
  the market; their median move beyond it is at least +1%; and the lower end of the 95% interval of their mean move
  beyond it is above zero (bets averaged per day, days resampled 2,000 times with a fixed seed).
- Firm and strong setups, longs and shorts, and the raw share right are reported beside it as description, not tested.
- Below that, or before 60 bets: context, not a signal. The views before 3 November stay reported apart, raw.
