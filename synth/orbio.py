"""Publish to X through Orbio's gateway (`social.post`), the same way Block0's agent does.

POST https://api.orbio.so/api/v1/tools/social.post  Authorization: Bearer ORBIO_API_KEY
body {platforms: ["x"], text, max_cost}. 401 bad key, 402 balance too low, 409 X account not
connected in the Orbio dashboard, 202 still running (never resubmit). Orbio refuses posts with links.
"""

from __future__ import annotations

import httpx

ORBIO_API = "https://api.orbio.so"


class OrbioError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def post_x(api_key: str, text: str, max_cost: float = 0.03,
                 client: httpx.AsyncClient | None = None, base: str = ORBIO_API) -> dict:
    if not api_key:
        raise OrbioError("nokey", "ORBIO_API_KEY is not set")
    own = client is None
    client = client or httpx.AsyncClient(timeout=60)
    try:
        r = await client.post(
            f"{base}/api/v1/tools/social.post",
            headers={"authorization": f"Bearer {api_key}"},
            json={"platforms": ["x"], "text": text, "max_cost": f"{max_cost:.6f}"},
        )
    finally:
        if own:
            await client.aclose()
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 401:
        raise OrbioError("auth", "Orbio rejected the API key (401)")
    if r.status_code == 402:
        raise OrbioError("balance", "Orbio balance too low (402)")
    if r.status_code == 409:
        raise OrbioError("connect", "X account not connected in the Orbio dashboard (409)")
    if r.status_code == 202:
        return {"status": "running", "post_id": (body.get("result") or body).get("post_id"), "url": None}
    if r.status_code >= 400:
        raise OrbioError("http", f"social.post -> HTTP {r.status_code}: {str(body)[:200]}")
    result = body.get("result") or body
    p = (result.get("platforms") or [{}])[0]
    return {
        "status": result.get("status"),
        "post_id": result.get("post_id"),
        "url": p.get("platformPostUrl"),
        "error": p.get("errorMessage"),
        "credit": (body.get("cost") or {}).get("credit"),
    }
