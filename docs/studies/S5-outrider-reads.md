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
