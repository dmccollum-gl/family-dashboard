from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import asyncio
import json
from pathlib import Path
import socket
import time

router = APIRouter()

CONFIG_PATH = Path(__file__).parent.parent / "dashboard_config.json"

# Background-refreshed cache — same pattern as display.py's _state["sysinfo"].
# Endpoint returns cached values instantly; SSE stream pushes updates every 500 ms.
_cache: dict = {"cpu": None, "ram": None, "ts": 0.0}
_bg_task = None

# ── /proc readers ─────────────────────────────────────────────────────────────

def _parse_cpu_stat() -> tuple[int, int]:
    with open("/proc/stat") as f:
        parts = f.readline().split()
    user   = int(parts[1])
    nice   = int(parts[2])
    system = int(parts[3])
    idle   = int(parts[4])
    iowait = int(parts[5]) if len(parts) > 5 else 0
    busy   = user + nice + system
    total  = busy + idle + iowait
    return busy, total


def _read_ram() -> dict:
    mem: dict = {}
    with open("/proc/meminfo") as f:
        for line in f:
            cols = line.split()
            if len(cols) >= 2:
                mem[cols[0].rstrip(":")] = int(cols[1])
    total_mb = mem.get("MemTotal",     0) // 1024
    avail_mb = mem.get("MemAvailable", 0) // 1024
    used_mb  = total_mb - avail_mb
    pct      = round(100 * used_mb / total_mb) if total_mb > 0 else 0
    return {"used": used_mb, "total": total_mb, "pct": pct}


# ── Background refresh loop (500 ms) ──────────────────────────────────────────

async def _bg_refresh() -> None:
    prev_busy = prev_total = 0
    first = True
    while True:
        try:
            b, t = _parse_cpu_stat()
            if not first:
                dt = t - prev_total
                db = b - prev_busy
                _cache["cpu"] = round(100 * db / dt) if dt > 0 else 0
            prev_busy, prev_total = b, t
            first = False
        except Exception:
            pass
        try:
            _cache["ram"] = _read_ram()
        except Exception:
            pass
        _cache["ts"] = time.monotonic()
        await asyncio.sleep(0.5)


def _ensure_bg() -> None:
    global _bg_task
    if _bg_task is None:
        _bg_task = asyncio.create_task(_bg_refresh())


# ── Hostname helpers ───────────────────────────────────────────────────────────

def _get_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return socket.gethostbyname(socket.gethostname())
    finally:
        try: s.close()
        except Exception: pass


def _auto_fqdn(ip: str) -> str:
    hostname = socket.gethostname()
    fqdn = socket.getfqdn(hostname)
    if fqdn.endswith(".arpa") or fqdn == ip:
        fqdn = hostname
    if "." not in fqdn:
        fqdn = fqdn + ".local"
    return fqdn


def _custom_fqdn() -> str:
    try:
        return json.loads(CONFIG_PATH.read_text()).get("custom_fqdn", "").strip()
    except Exception:
        return ""


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("")
async def get_system_info():
    _ensure_bg()
    ip   = _get_ip()
    fqdn = _custom_fqdn() or _auto_fqdn(ip)
    return {"ip": ip, "fqdn": fqdn, "cpu": _cache["cpu"], "ram": _cache["ram"]}


@router.get("/live")
async def sysinfo_live():
    _ensure_bg()

    async def event_stream():
        while True:
            yield f"data: {json.dumps({'cpu': _cache['cpu'], 'ram': _cache['ram']})}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",   # prevents nginx from buffering the stream
            "Connection":        "keep-alive",
        },
    )
