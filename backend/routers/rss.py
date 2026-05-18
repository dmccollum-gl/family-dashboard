from fastapi import APIRouter
from pathlib import Path
import json, feedparser, asyncio, httpx
from concurrent.futures import ThreadPoolExecutor

router = APIRouter()

CONFIG_PATH = Path(__file__).parent.parent / "dashboard_config.json"
_executor = ThreadPoolExecutor(max_workers=4)


def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text())


def _parse_feed(feed_cfg: dict) -> dict:
    try:
        # Use httpx to fetch so we control SSL; feedparser's urllib fails on macOS Python 3.14
        with httpx.Client(verify=False, timeout=15, follow_redirects=True) as client:
            resp = client.get(feed_cfg["url"])
            resp.raise_for_status()
        d = feedparser.parse(resp.text)
        label = feed_cfg.get("label") or d.feed.get("title", "")
        items = [
            {"title": e.get("title", "").strip(), "link": e.get("link", ""), "source": label}
            for e in d.entries[:15]
            if e.get("title", "").strip()
        ]
        return {"items": items, "error": None}
    except Exception as e:
        return {"items": [], "error": str(e)}


@router.get("/feed")
async def get_rss_feed():
    cfg   = _read_config()
    feeds = cfg.get("rss_feeds", [])
    if not feeds:
        return {"items": [], "errors": []}
    loop = asyncio.get_event_loop()
    results = await asyncio.gather(
        *[loop.run_in_executor(_executor, _parse_feed, f) for f in feeds]
    )
    items  = [item for r in results for item in r["items"]]
    errors = [r["error"] for r in results if r["error"]]
    return {"items": items, "errors": errors}
