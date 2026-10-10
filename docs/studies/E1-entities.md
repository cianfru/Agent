# E1: Who is who: wallets grouped into entities, entities sorted into classes (declared 10 October 2026)

The owner's direction: the agent watches single wallets, but many wallets belong to one trader, and a small
profitable wallet tells little. Group wallets that act as one into **entities**, sort entities into clear
**classes** (above all: big money that holds positions, as opposed to traders who go in and out), and read the
market through those classes: what big money is doing, its lean, and where price is against it.

Rules below are fixed before any data is pulled. The counts they produce are description; the classes become the
agent's lens once the owner has seen the map.

## Wallet pool

1. **Big accounts:** every leaderboard wallet (10 October 2026) with account value of at least $1 million that is
   not a market maker (monthly volume at most 100 times account value). About 2,700.
2. **Profitable traders:** Reflex's roster rule on the same day (544 wallets, see S2 amendment 1).
3. **S1's roster** (288 wallets after its bot filter).

## Data per wallet (Hyperliquid's public info API)

- open positions and perp equity now (`clearinghouseState`);
- its sub-accounts (`subAccounts`);
- transfers since 1 October 2025 (`userNonFundingLedgerUpdates`: internal transfers, sends, sub-account and spot
  transfers between wallets);
- fills: as in S2 (10,000 most recent regular fills since 1 October 2025 and 2,000 TWAP slices), for every wallet
  with an open perp position now or perp volume this month. Others are idle and are classed idle.

## Links: two wallets are one entity when any of these holds

1. **Sub-account:** one is the other's sub-account (Hyperliquid's own record). Same owner, certain.
2. **Money between them:** a direct transfer between the two of at least $10,000 in total; or both received at
   least $10,000 from the same third address, when that address transferred with at most 10 wallets in the pool
   (more makes it a service, an exchange or a payout account, and it links nobody).
3. **Trading together**, either of:
   - **a. Same minutes:** a wallet's fills in one coin and direction within 10 minutes of each other are one move.
     A move matches the other wallet's move in the same coin and direction within 5 minutes. Linked with at least
     5 matched moves covering at least 50% of the less active wallet's moves.
   - **b. Reflex's rule** (`backend/hl_entities.py`, constants unchanged), applied to each wallet's fills over the
     last 90 days instead of Reflex's 30-day log of readings: moves within 2 hours are one move; moves match
     within 48 hours, closest first; each wallet has at least 2 matched moves and at least 70% of its moves
     matched; the median gap between matched moves is 6 hours or less; and their dollar positions, sampled every
     4 hours at one price per coin, correlate at 0.8 or more. (On about 600 wallets Reflex's rule found 1 to 2
     groups; checked by hand, the known BTC group would miss rule a, since many of its trades land 10 minutes to
     hours apart, so b stays.)
   One operator or a copy of one: both are one voice for this purpose.

Links join into groups (A with B and B with C: one entity of three). Each entity records which link types joined it.

## Classes (per entity, all its wallets together)

- **Bot or market maker:** S1 amendment 2's rule on its fills since 26 September (40 or more grouped trades a
  day, or 10 or more with at least 25% quick reversals), or monthly volume over 100 times its account value. Left
  out of every reading.
- **Size**, from perp equity now plus its largest open position's value:
  - **Whale:** equity at least $5 million, or a single position worth at least $5 million;
  - **Big:** equity $1 to 5 million;
  - **Trader:** under $1 million (profitable traders from pools 2 and 3).
- **Style**, from the median holding time of its closed positions over the last 90 days (a position runs from flat
  back to flat, per coin): **position** (7 days or more), **swing** (1 to 7 days), **active** (under a day).
  Fewer than 3 closed positions: by its open positions' age, else unknown.
- **Record:** all-time PnL summed (profitable or not), and this month's.
- **Idle:** no open perp position and no perp volume this month.

**Big money**, the class the owner asked for, is: Whale or Big, position or swing style, profitable all-time. It
is the headline lens. Every other class stays visible on its own.

## What comes out

- wallets to entities: how many, how large, by link type, with the biggest entities listed;
- class counts and each class's open positions now, per coin: entities long and short, dollars each side;
- a check against Reflex's known group (0x2371...0883, 0x93ab...11f8, 0xec7f...df39), which must come out as one
  entity, or the rules are wrong.

## Amendment 1 (10 October 2026, before the pull for the pool, after reading the known group's trades)

Reflex's rule is calibrated on a 30-day window. Over 90 days a group that later drifts apart fails it: the known
BTC group traded as one from June to September, then from 1 October two of its wallets bought ETH while the third
kept trading BTC alone, which takes each wallet's matched share under 70% and lowers the position correlation.
So rule 3b is applied over 30-day windows ending now and every 15 days back within the 90 days (5 windows); a
pair that passes in any window is linked. Seen while checking the shapes of the data: the three wallets were all
funded in June by one address that has sent money to hundreds of wallets, so the hub rule rightly ignores it as a
link.
