"""CLI.

  python -m synth cycle      one poll cycle (for cron / GitHub Actions); prints new events
  python -m synth brief      generate and push the daily brief
  python -m synth digest     print the compact digest the model sees (no model call)
  python -m synth serve      run the API + background poller (uvicorn)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cmd = argv[1] if len(argv) > 1 else "cycle"
    if cmd == "serve":
        import os

        import uvicorn

        uvicorn.run("synth.api:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
        return 0

    from . import config
    from .service import build_digest, make_agent

    agent = make_agent(config.load())
    if cmd == "cycle":
        out = asyncio.run(agent.cycle())
        for e in out["events"]:
            print(f"{e['score']:5.1f}  {e['title']}")
        return 0
    if cmd == "brief":
        print(json.dumps(asyncio.run(agent.brief()), indent=2))
        return 0
    if cmd == "digest":
        snaps = asyncio.run(agent.collect())
        print(json.dumps(build_digest(snaps, agent.store.events(time.time() - 86400)), indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
