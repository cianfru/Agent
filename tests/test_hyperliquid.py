import asyncio

import httpx
import respx

from synth import hyperliquid as H


REAL_SLEEP = asyncio.sleep


class Clock:
    """A fake clock: sleeping advances it at once (but still lets other tasks run)."""

    def __init__(self):
        self.t, self.slept = 0.0, []

    def __call__(self):
        return self.t

    async def sleep(self, s, *args):
        if s:
            self.slept.append(s)
            self.t += s
        await REAL_SLEEP(0)


def test_every_read_shares_one_budget(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(H.asyncio, "sleep", clock.sleep)
    b = H.Budget(900, clock=clock)                     # 15 weight a second, a burst of 225

    async def reads(n, w):
        for _ in range(n):
            await b.take(w)

    asyncio.run(reads(112, 2))                          # 224: within the burst, no wait
    assert clock.t == 0
    asyncio.run(reads(10, 20))                          # then each heavy read waits for its weight
    assert b.spent == 424 and abs(clock.t - (424 - 225) / 15) < 1.0
    b.charge(300)                                       # 300 more owed for what an answer carried
    t0 = clock.t
    asyncio.run(reads(1, 2))
    assert clock.t - t0 >= 300 / 15                     # the next read waits it out
    assert b.spent <= 225 + 15 * clock.t + 2            # never more than the burst plus the refill


def test_429_waits_as_long_as_hyperliquid_asks(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(H.asyncio, "sleep", clock.sleep)
    with respx.mock:
        route = respx.post(H.INFO_URL)
        route.side_effect = [httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(200, json=[{"t": 1}] * 45)]

        async def go():
            async with httpx.AsyncClient() as http:
                hl = H.HL(http, budget=H.Budget(900, clock=clock))
                res = await hl._post({"type": "userFillsByTime", "user": "0x0", "startTime": 0})
                return res, hl.budget.spent
        res, spent = asyncio.run(go())
    assert len(res) == 45 and 7 in clock.slept
    assert spent == 20 + 20 + 2                         # two tries at 20, plus 45 items returned


def test_weights():
    assert H.weight({"type": "clearinghouseState"}) == 2 and H.weight({"type": "userFillsByTime"}) == 20
    assert H.extra_weight({"type": "candleSnapshot"}, [0] * 130) == 2 and H.extra_weight({"type": "x"}, {}) == 0
