from fastapi import APIRouter
from pathlib import Path
import json, feedparser, asyncio, httpx, random
from concurrent.futures import ThreadPoolExecutor

router = APIRouter()

CONFIG_PATH = Path(__file__).parent.parent / "dashboard_config.json"
_executor = ThreadPoolExecutor(max_workers=4)


def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text())


_RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

def _parse_feed(feed_cfg: dict) -> dict:
    try:
        with httpx.Client(verify=False, timeout=15, follow_redirects=True, headers=_RSS_HEADERS) as client:
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


async def _fetch_dad_jokes(limit: int = 20) -> dict:
    try:
        page = random.randint(1, 15)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://icanhazdadjoke.com/search",
                params={"limit": limit, "page": page},
                headers={"Accept": "application/json", "User-Agent": "FamilyDashboard/1.0 (home kiosk)"},
            )
            resp.raise_for_status()
            data = resp.json()
        items = [
            {"title": r["joke"], "link": "", "source": "Dad Jokes"}
            for r in data.get("results", [])
            if r.get("joke")
        ]
        return {"items": items, "error": None}
    except Exception as e:
        return {"items": [], "error": str(e)}


async def _fetch_hacker_news(limit: int = 20) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "FamilyDashboard/1.0"}) as client:
            resp = await client.get("https://hacker-news.firebaseio.com/v0/topstories.json")
            resp.raise_for_status()
            ids = resp.json()[: limit * 2]   # fetch extra to account for non-story types

            results = await asyncio.gather(
                *[client.get(f"https://hacker-news.firebaseio.com/v0/item/{id}.json") for id in ids],
                return_exceptions=True,
            )

        items = []
        for r in results:
            if isinstance(r, Exception):
                continue
            try:
                d = r.json()
                if d and d.get("type") == "story" and d.get("title"):
                    items.append({
                        "title": d["title"],
                        "link":  d.get("url") or f"https://news.ycombinator.com/item?id={d['id']}",
                        "source": "Hacker News",
                    })
                    if len(items) >= limit:
                        break
            except Exception:
                pass
        return {"items": items, "error": None}
    except Exception as e:
        return {"items": [], "error": str(e)}


@router.get("/feed")
async def get_rss_feed():
    cfg        = _read_config()
    feeds      = cfg.get("rss_feeds",  [])
    dad_jokes  = cfg.get("dad_jokes",  True)
    hacker_news = cfg.get("hacker_news", True)

    loop = asyncio.get_event_loop()
    awaitables = [loop.run_in_executor(_executor, _parse_feed, f) for f in feeds]
    if dad_jokes:
        awaitables.append(_fetch_dad_jokes())
    if hacker_news:
        awaitables.append(_fetch_hacker_news())

    if not awaitables:
        return {"items": [], "errors": []}

    results = await asyncio.gather(*awaitables)
    items  = [item for r in results for item in r["items"]]
    errors = [r["error"] for r in results if r["error"]]
    return {"items": items, "errors": errors}
