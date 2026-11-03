import pytest

from synth import hyperliquid


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never reach Hyperliquid: a call that is not faked fails at once (and the code logs and carries on)."""
    async def refuse(self, body, tries=3):
        raise RuntimeError("no network in tests")
    monkeypatch.setattr(hyperliquid.HL, "_post", refuse)
