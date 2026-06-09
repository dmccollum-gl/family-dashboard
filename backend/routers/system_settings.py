from fastapi import APIRouter, HTTPException
from pathlib import Path
from datetime import datetime
import json, subprocess, threading, time as _time

router = APIRouter()

CONFIG_PATH = Path(__file__).parent.parent / "dashboard_config.json"
ENV_PATH    = Path(__file__).parent.parent / ".env"
_MASKED = "••••••••"


def _read_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _write_env(updates: dict):
    env = _read_env()
    env.update({k: v for k, v in updates.items() if v})
    ENV_PATH.write_text("\n".join(f"{k}={v}" for k, v in env.items()) + "\n")


def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text())


def _write_config(data: dict):
    existing = _read_config()
    existing.update(data)
    CONFIG_PATH.write_text(json.dumps(existing, indent=2))


# ── OAuth ─────────────────────────────────────────────────────────────────────

@router.get("/oauth")
def get_oauth_config():
    env = _read_env()
    return {
        "client_id":     env.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": _MASKED if env.get("GOOGLE_CLIENT_SECRET") else "",
        "configured":    bool(env.get("GOOGLE_CLIENT_ID") and env.get("GOOGLE_CLIENT_SECRET")),
    }


@router.put("/oauth")
def save_oauth_config(body: dict):
    updates = {}
    if body.get("client_id"):
        updates["GOOGLE_CLIENT_ID"] = body["client_id"]
    if body.get("client_secret") and body["client_secret"] != _MASKED:
        updates["GOOGLE_CLIENT_SECRET"] = body["client_secret"]
    if updates:
        _write_env(updates)
    return {"status": "saved", "restart_required": True}


# ── Weather ────────────────────────────────────────────────────────────────────

@router.get("/weather")
def get_weather_config():
    cfg = _read_config()
    return {
        "location":   cfg.get("owm_location", ""),
        "units":      cfg.get("owm_units", "imperial"),
        "configured": bool(cfg.get("owm_location")),
    }


@router.put("/weather")
def save_weather_config(body: dict):
    updates = {}
    if "location" in body:
        new_loc = body["location"]
        updates["owm_location"] = new_loc
        # Clear geocoding cache if address changed so it re-geocodes on next fetch.
        if new_loc != _read_config().get("owm_location"):
            updates["_geo_for"] = None
            updates["_geo_lat"] = None
            updates["_geo_lon"] = None
    if "units" in body:
        updates["owm_units"] = body["units"]
    if updates:
        _write_config(updates)
    return {"status": "saved"}


# ── RSS Feeds ──────────────────────────────────────────────────────────────────

@router.get("/rss")
def get_rss_config():
    cfg = _read_config()
    return {
        "feeds":       cfg.get("rss_feeds",   []),
        "mode":        cfg.get("rss_mode",    "shuffle"),
        "dad_jokes":   cfg.get("dad_jokes",   True),
        "hacker_news": cfg.get("hacker_news", True),
    }


@router.put("/rss")
def save_rss_config(body: dict):
    feeds = body.get("feeds", [])
    cleaned = [
        {"url": f["url"].strip(), "label": f.get("label", "").strip()}
        for f in feeds
        if f.get("url", "").strip()
    ]
    updates: dict = {"rss_feeds": cleaned}
    if body.get("mode") in {"shuffle", "rotate"}:
        updates["rss_mode"] = body["mode"]
    if "dad_jokes" in body:
        updates["dad_jokes"] = bool(body["dad_jokes"])
    if "hacker_news" in body:
        updates["hacker_news"] = bool(body["hacker_news"])
    _write_config(updates)
    return {"status": "saved", "count": len(cleaned)}


# ── Display settings ───────────────────────────────────────────────────────────

VALID_THEMES        = {"auto", "light", "dark"}
VALID_VIEWS         = {"day", "week", "2week", "month", "rolling"}
VALID_WEATHER_VIEWS = {"daily", "hourly"}


@router.get("/display")
def get_display_config():
    cfg = _read_config()
    return {
        "theme":        cfg.get("display_theme",        "auto"),
        "view":         cfg.get("display_view",         "week"),
        "weather_view": cfg.get("display_weather_view", "daily"),
        "custom_fqdn":  cfg.get("custom_fqdn",          ""),
        # Flag-file check — display.py reads this and goes black when True.
        # The flag file is also polled directly in display.py for instant response.
        "display_off":  Path(_APP_DIR / ".display_off").exists(),
    }


@router.put("/display")
def save_display_config(body: dict):
    updates = {}
    if body.get("theme") in VALID_THEMES:
        updates["display_theme"] = body["theme"]
    if body.get("view") in VALID_VIEWS:
        updates["display_view"] = body["view"]
    if body.get("weather_view") in VALID_WEATHER_VIEWS:
        updates["display_weather_view"] = body["weather_view"]
    if "custom_fqdn" in body:
        updates["custom_fqdn"] = body["custom_fqdn"].strip()
    if updates:
        _write_config(updates)
    return {"status": "saved"}


# ── Permissions ───────────────────────────────────────────────────────────────

SECTIONS = [
    "weather_location",
    "pi_display",
    "display_schedule",
    "family_calendars",
    "family_members",
    "rss_feeds",
    "restart_services",
]
DEFAULT_PERMISSIONS = {
    "admin": SECTIONS[:],
    "user":  ["family_calendars"],
}


@router.get("/permissions")
def get_permissions():
    cfg = _read_config()
    perms = cfg.get("permissions", {})
    return {
        "sections": SECTIONS,
        "admin":    perms.get("admin", DEFAULT_PERMISSIONS["admin"]),
        "user":     perms.get("user",  DEFAULT_PERMISSIONS["user"]),
    }


@router.put("/permissions")
def save_permissions(body: dict):
    cfg = _read_config()
    perms = dict(cfg.get("permissions", {}))
    if "admin" in body and isinstance(body["admin"], list):
        perms["admin"] = [s for s in body["admin"] if s in SECTIONS]
    if "user" in body and isinstance(body["user"], list):
        perms["user"] = [s for s in body["user"] if s in SECTIONS]
    _write_config({"permissions": perms})
    return {"status": "saved"}


# ── Restart ────────────────────────────────────────────────────────────────────

def _after(delay: float, fn):
    import time
    time.sleep(delay)
    fn()


@router.post("/restart/backend")
def restart_backend():
    threading.Thread(
        target=_after,
        args=(1.0, lambda: subprocess.run(["sudo", "systemctl", "restart", "dashboard-backend"], check=False)),
        daemon=True,
    ).start()
    return {"status": "restarting"}


@router.post("/restart/display")
def restart_display():
    # display.py runs as the same user (dashboard), so pkill works without sudo.
    # The .bash_profile while-loop restarts it automatically after 5 s.
    threading.Thread(
        target=_after,
        args=(1.0, lambda: subprocess.run(["pkill", "-f", "display.py"], check=False)),
        daemon=True,
    ).start()
    return {"status": "restarting"}


# ── Cloudflare Tunnel ──────────────────────────────────────────────────────────

@router.get("/tunnel")
def get_tunnel():
    cfg   = _read_config()
    token = cfg.get("tunnel_token", "")
    try:
        out    = subprocess.run(
            ["systemctl", "is-active", "cloudflared"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        active = out == "active"
    except Exception:
        active = False
    return {
        "configured": bool(token),
        "token":      _MASKED if token else "",
        "active":     active,
    }


@router.put("/tunnel")
def save_tunnel(body: dict):
    updates = {}
    if body.get("clear"):
        updates["tunnel_token"] = ""
    elif body.get("token") and body["token"] != _MASKED:
        updates["tunnel_token"] = body["token"].strip()
    if updates:
        _write_config(updates)
    return {"status": "saved"}


@router.post("/tunnel/{action}")
def control_tunnel(action: str):
    if action not in ("start", "stop", "restart"):
        raise HTTPException(status_code=400, detail="Invalid action. Use start, stop, or restart.")
    threading.Thread(
        target=_after,
        args=(0.5, lambda: subprocess.run(
            ["sudo", "systemctl", action, "cloudflared"], check=False
        )),
        daemon=True,
    ).start()
    return {"status": action + "ing"}


# ── FQDN auto-detection ───────────────────────────────────────────────────────

@router.get("/fqdn/detect")
def detect_fqdn():
    """Try to detect the Pi's public hostname from Tailscale or Cloudflare Tunnel."""
    import shutil, json as _json, base64 as _b64

    result: dict = {"tailscale": None, "cloudflare_tunnel_id": None}

    # ── Tailscale ─────────────────────────────────────────────────────────────
    ts = shutil.which("tailscale")
    if ts:
        try:
            r = subprocess.run([ts, "status", "--json"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                data = _json.loads(r.stdout)
                dns = data.get("Self", {}).get("DNSName", "").rstrip(".")
                if dns:
                    result["tailscale"] = dns
        except Exception:
            pass

    # ── Cloudflare Tunnel — decode JWT to confirm a tunnel is configured ───────
    # The tunnel token doesn't encode the public hostname (that lives in
    # Cloudflare's dashboard), but we can extract the tunnel ID as confirmation.
    cfg   = _read_config()
    token = cfg.get("tunnel_token", "")
    if token:
        try:
            parts = token.split(".")
            if len(parts) >= 2:
                pad     = parts[1] + "=" * (-len(parts[1]) % 4)
                payload = _json.loads(_b64.b64decode(pad))
                tid     = payload.get("t") or payload.get("tunnel_id")
                if tid:
                    result["cloudflare_tunnel_id"] = str(tid)
        except Exception:
            pass

    return result


# ── Cloudflare API Setup ──────────────────────────────────────────────────────

_CF_API = "https://api.cloudflare.com/client/v4"


def _cf_hdrs(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@router.post("/cloudflare/verify")
def cf_verify(body: dict):
    """Verify a Cloudflare API token and list zones for the given account."""
    import httpx as _httpx

    api_token  = (body.get("api_token")  or "").strip()
    account_id = (body.get("account_id") or "").strip()
    if not api_token or not account_id:
        raise HTTPException(400, "api_token and account_id are required")

    hdrs = _cf_hdrs(api_token)

    # 1. Verify token
    try:
        r    = _httpx.get(f"{_CF_API}/user/tokens/verify", headers=hdrs, timeout=10)
        data = r.json()
        if not data.get("success") or data.get("result", {}).get("status") != "active":
            errs = data.get("errors", [])
            msg  = errs[0].get("message") if errs else "Token is invalid or inactive"
            return {"valid": False, "error": msg}
    except Exception as exc:
        return {"valid": False, "error": str(exc)}

    # 2. List zones for this account
    try:
        r    = _httpx.get(f"{_CF_API}/zones",
                          params={"account.id": account_id, "per_page": 50},
                          headers=hdrs, timeout=10)
        data = r.json()
        if not data.get("success"):
            errs  = data.get("errors", [])
            error = errs[0].get("message") if errs else "Could not list zones"
            return {"valid": True, "zones": [], "error": error}
        zones = [{"id": z["id"], "name": z["name"]}
                 for z in data.get("result", [])]
    except Exception as exc:
        return {"valid": True, "zones": [], "error": str(exc)}

    # 3. Check Cloudflare Tunnel permissions (requires "Cloudflare Tunnel: Edit")
    tunnel_ok = False
    try:
        r = _httpx.get(
            f"{_CF_API}/accounts/{account_id}/cfd_tunnel",
            params={"per_page": 1},
            headers=hdrs, timeout=10,
        )
        tunnel_ok = r.json().get("success", False)
    except Exception:
        pass

    if not tunnel_ok:
        return {
            "valid":      True,
            "zones":      zones,
            "tunnel_ok":  False,
            "error": (
                "Zones loaded but this token cannot manage tunnels. "
                "Edit the token in Cloudflare → My Profile → API Tokens and add "
                "the 'Cloudflare Tunnel: Edit' permission, then verify again."
            ),
        }

    # Persist so /setup can reuse without re-sending credentials
    _write_config({"cf_api_token": api_token, "cf_account_id": account_id})

    return {"valid": True, "zones": zones, "tunnel_ok": True}


@router.post("/cloudflare/setup")
def cf_setup(body: dict):
    """
    Full Cloudflare tunnel setup:
      0. Tear down any previously-created tunnel + DNS record
      1. Resolve zone name → build FQDN
      2. Create the tunnel
      3. Fetch connector token
      4. Configure API-managed ingress rules
      5. Create (or update) the proxied CNAME DNS record
      6. Persist config
      7. Start cloudflared
    """
    import httpx as _httpx

    cfg        = _read_config()
    api_token  = (body.get("api_token")   or cfg.get("cf_api_token",  "")).strip()
    account_id = (body.get("account_id")  or cfg.get("cf_account_id", "")).strip()
    zone_id    = (body.get("zone_id")     or "").strip()
    subdomain  = (body.get("subdomain")   or "dashboard").strip().lower()
    tname      = (body.get("tunnel_name") or "family-dashboard").strip()

    if not all([api_token, account_id, zone_id, subdomain]):
        raise HTTPException(400, "api_token, account_id, zone_id, and subdomain are required")

    hdrs = _cf_hdrs(api_token)

    # 0. Tear down previous tunnel + DNS if this is a re-run ─────────────────
    old_tid      = cfg.get("cf_tunnel_id", "")
    old_fqdn     = cfg.get("custom_fqdn", "")
    old_zone_id  = cfg.get("cf_zone_id", "")

    if old_tid:
        # Close active connections first (best-effort)
        try:
            _httpx.delete(
                f"{_CF_API}/accounts/{account_id}/cfd_tunnel/{old_tid}/connections",
                headers=hdrs, timeout=10,
            )
        except Exception:
            pass
        # Delete the old tunnel
        try:
            _httpx.delete(
                f"{_CF_API}/accounts/{account_id}/cfd_tunnel/{old_tid}",
                headers=hdrs, timeout=10,
            )
        except Exception:
            pass
        # Delete old DNS CNAME record
        if old_fqdn and old_zone_id:
            try:
                r2 = _httpx.get(
                    f"{_CF_API}/zones/{old_zone_id}/dns_records",
                    params={"name": old_fqdn, "type": "CNAME"},
                    headers=hdrs, timeout=10,
                )
                for rec in r2.json().get("result", []):
                    _httpx.delete(
                        f"{_CF_API}/zones/{old_zone_id}/dns_records/{rec['id']}",
                        headers=hdrs, timeout=10,
                    )
            except Exception:
                pass

    # 1. Resolve zone name → build FQDN ──────────────────────────────────────
    try:
        r = _httpx.get(f"{_CF_API}/zones/{zone_id}", headers=hdrs, timeout=10)
        d = r.json()
        if not d.get("success"):
            raise HTTPException(400, "Invalid zone_id")
        zone_name = d["result"]["name"]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to get zone: {exc}")

    fqdn = f"{subdomain}.{zone_name}"

    # 2. Create tunnel ────────────────────────────────────────────────────────
    try:
        r = _httpx.post(
            f"{_CF_API}/accounts/{account_id}/cfd_tunnel",
            headers=hdrs,
            json={"name": tname, "config_src": "cloudflare"},
            timeout=20,
        )
        d = r.json()
        if not d.get("success"):
            errs = d.get("errors", [])
            raw  = errs[0].get("message") if errs else "Failed to create tunnel"
            code = errs[0].get("code", 0)  if errs else 0
            if code in (10000, 10001) or "auth" in raw.lower():
                msg = (
                    "Authentication error — the token does not have "
                    "'Cloudflare Tunnel: Edit' permission. "
                    "Update the token at Cloudflare → My Profile → API Tokens, "
                    "then re-verify."
                )
            else:
                msg = raw
            raise HTTPException(500, msg)
        tunnel    = d["result"]
        tunnel_id = tunnel["id"]
        ttoken    = tunnel.get("token") or ""
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to create tunnel: {exc}")

    # 3. Fetch token separately if not returned by create ─────────────────────
    if not ttoken:
        try:
            r      = _httpx.get(
                f"{_CF_API}/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token",
                headers=hdrs, timeout=10,
            )
            ttoken = r.json().get("result", "")
        except Exception as exc:
            raise HTTPException(500, f"Tunnel created but could not fetch token: {exc}")

    # 4. Configure API-managed ingress (non-fatal) ────────────────────────────
    try:
        _httpx.put(
            f"{_CF_API}/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
            headers=hdrs,
            json={"config": {"ingress": [
                {"hostname": fqdn, "service": "http://localhost:80"},
                {"service": "http_status:404"},
            ]}},
            timeout=15,
        )
    except Exception:
        pass

    # 5. Create proxied CNAME — update if the record already exists ───────────
    # 5. Create proxied CNAME ─────────────────────────────────────────────────
    # Note: when config_src="cloudflare" and ingress is configured (step 4),
    # Cloudflare often auto-creates the CNAME. The POST may therefore fail with
    # "record already exists". We always fall back to a lookup to confirm the
    # record is present, and consider any existing CNAME a success.
    dns_ok    = False
    dns_error = None
    _cname    = f"{tunnel_id}.cfargotunnel.com"

    def _dns_verify():
        """Return True if a CNAME for fqdn already exists in this zone."""
        try:
            rv = _httpx.get(
                f"{_CF_API}/zones/{zone_id}/dns_records",
                params={"name": fqdn, "type": "CNAME"},
                headers=hdrs, timeout=10,
            )
            records = rv.json().get("result", [])
            if records:
                # Best-effort: update content to point at the new tunnel
                _httpx.put(
                    f"{_CF_API}/zones/{zone_id}/dns_records/{records[0]['id']}",
                    headers=hdrs,
                    json={"type": "CNAME", "name": subdomain,
                          "content": _cname, "proxied": True, "ttl": 1},
                    timeout=10,
                )
                return True
        except Exception:
            pass
        return False

    try:
        r = _httpx.post(
            f"{_CF_API}/zones/{zone_id}/dns_records",
            headers=hdrs,
            json={"type": "CNAME", "name": subdomain,
                  "content": _cname, "proxied": True, "ttl": 1},
            timeout=15,
        )
        d = r.json()
        if d.get("success"):
            dns_ok = True
        else:
            # POST failed — capture error then always check if record exists
            errs      = d.get("errors", [])
            post_msg  = (errs[0].get("message") or "") if errs else ""
            dns_error = post_msg or "DNS record creation failed"
            if _dns_verify():
                dns_ok    = True
                dns_error = None   # record is there; don't warn
    except Exception as exc:
        dns_error = str(exc)
        if _dns_verify():
            dns_ok    = True
            dns_error = None

    # 6. Persist everything ───────────────────────────────────────────────────
    _write_config({
        "tunnel_token":  ttoken,
        "custom_fqdn":   fqdn,
        "cf_api_token":  api_token,
        "cf_account_id": account_id,
        "cf_zone_id":    zone_id,
        "cf_tunnel_id":  tunnel_id,
    })

    # 7. Start cloudflared ────────────────────────────────────────────────────
    threading.Thread(
        target=_after,
        args=(0.5, lambda: subprocess.run(
            ["sudo", "systemctl", "restart", "cloudflared"], check=False
        )),
        daemon=True,
    ).start()

    return {
        "success":     True,
        "fqdn":        fqdn,
        "tunnel_id":   tunnel_id,
        "dns_created": dns_ok,
        "dns_error":   dns_error,
    }


@router.get("/cloudflare/tunnels")
def cf_list_tunnels():
    """List all non-deleted Cloudflare Tunnels for the stored account."""
    import httpx as _httpx

    cfg        = _read_config()
    api_token  = cfg.get("cf_api_token",  "")
    account_id = cfg.get("cf_account_id", "")
    current_id = cfg.get("cf_tunnel_id",  "")
    current_fqdn = cfg.get("custom_fqdn", "")

    if not api_token or not account_id:
        return {"tunnels": [], "error": "No Cloudflare credentials stored. Use Auto-Setup to connect first."}

    hdrs = _cf_hdrs(api_token)
    try:
        r = _httpx.get(
            f"{_CF_API}/accounts/{account_id}/cfd_tunnel",
            params={"per_page": 100, "is_deleted": "false"},
            headers=hdrs, timeout=10,
        )
        d = r.json()
        if not d.get("success"):
            errs = d.get("errors", [])
            msg  = errs[0].get("message") if errs else "Failed to list tunnels"
            return {"tunnels": [], "error": msg}

        tunnels = []
        for t in d.get("result", []):
            if t.get("deleted_at"):
                continue
            tid         = t["id"]
            conns       = t.get("connections") or []
            active_conn = [c for c in conns if not c.get("is_pending_reconnect")]
            tunnels.append({
                "id":         tid,
                "name":       t.get("name", ""),
                "active":     len(active_conn) > 0,
                "created_at": t.get("created_at", ""),
                "is_current": tid == current_id,
                "fqdn":       current_fqdn if tid == current_id else None,
            })

        return {"tunnels": tunnels, "current_tunnel_id": current_id}
    except Exception as exc:
        return {"tunnels": [], "error": str(exc)}


@router.delete("/cloudflare/tunnel/{tunnel_id}")
def cf_delete_tunnel(tunnel_id: str):
    """Delete a Cloudflare Tunnel (and its DNS record + local token if it's the Pi's tunnel)."""
    import httpx as _httpx

    cfg        = _read_config()
    api_token  = cfg.get("cf_api_token",  "")
    account_id = cfg.get("cf_account_id", "")
    zone_id    = cfg.get("cf_zone_id",    "")
    current_id = cfg.get("cf_tunnel_id",  "")
    old_fqdn   = cfg.get("custom_fqdn",   "")

    if not api_token or not account_id:
        raise HTTPException(400, "No Cloudflare credentials stored.")

    hdrs = _cf_hdrs(api_token)

    # 1. Close active connections (best-effort)
    try:
        _httpx.delete(
            f"{_CF_API}/accounts/{account_id}/cfd_tunnel/{tunnel_id}/connections",
            headers=hdrs, timeout=10,
        )
    except Exception:
        pass

    # 2. Delete the tunnel
    try:
        r = _httpx.delete(
            f"{_CF_API}/accounts/{account_id}/cfd_tunnel/{tunnel_id}",
            headers=hdrs, timeout=10,
        )
        d = r.json()
        if not d.get("success"):
            errs = d.get("errors", [])
            msg  = errs[0].get("message") if errs else "Failed to delete tunnel"
            raise HTTPException(500, msg)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to delete tunnel: {exc}")

    # 3. Delete DNS CNAME if this is/was the Pi's tunnel
    is_current = tunnel_id == current_id
    if is_current and old_fqdn and zone_id:
        try:
            r2 = _httpx.get(
                f"{_CF_API}/zones/{zone_id}/dns_records",
                params={"name": old_fqdn, "type": "CNAME"},
                headers=hdrs, timeout=10,
            )
            for rec in r2.json().get("result", []):
                _httpx.delete(
                    f"{_CF_API}/zones/{zone_id}/dns_records/{rec['id']}",
                    headers=hdrs, timeout=10,
                )
        except Exception:
            pass

    # 4. Clear Pi-local config and stop cloudflared if it was the active tunnel
    if is_current:
        _write_config({
            "tunnel_token": "",
            "cf_tunnel_id": "",
            "custom_fqdn":  "",
        })
        threading.Thread(
            target=_after,
            args=(0.5, lambda: subprocess.run(
                ["sudo", "systemctl", "stop", "cloudflared"], check=False
            )),
            daemon=True,
        ).start()

    return {"success": True, "was_current": is_current}


# ── Software Update ────────────────────────────────────────────────────────────

_APP_DIR     = Path("/opt/dashboard")
_UPDATE_LOCK = threading.Lock()
_update_state: dict = {
    "running":   False,
    "log":       [],
    "exit_code": None,
    "started":   None,
}


def _git_run(*args, timeout: int = 30):
    """Run a git command inside APP_DIR. Returns (stdout, stderr, returncode)."""
    r = subprocess.run(
        ["git", "-C", str(_APP_DIR), *args],
        capture_output=True, text=True, timeout=timeout,
    )
    return r.stdout.strip(), r.stderr.strip(), r.returncode


@router.get("/update/version")
def get_version():
    """Return the currently-installed git commit (if any)."""
    git_dir = _APP_DIR / ".git"
    if not git_dir.exists():
        return {"installed": False, "commit": None, "date": None, "branch": None}
    try:
        commit, _, _ = _git_run("rev-parse", "--short", "HEAD")
        date,   _, _ = _git_run("log", "-1", "--format=%ci")
        branch, _, _ = _git_run("rev-parse", "--abbrev-ref", "HEAD")
        return {"installed": True, "commit": commit, "date": date, "branch": branch}
    except Exception as e:
        return {"installed": False, "error": str(e)}


@router.get("/update/check")
def check_update():
    """Fetch from origin and compare with local HEAD. May take up to 30 s."""
    if not (_APP_DIR / ".git").exists():
        return {"error": "Not a git installation — updates not available."}
    try:
        _, err, rc = _git_run("fetch", "origin", timeout=45)
        if rc != 0:
            return {"error": f"git fetch failed: {err or 'no internet?'}"}

        local,  _, _ = _git_run("rev-parse", "--short", "HEAD")
        remote, _, _ = _git_run("rev-parse", "--short", "origin/main")
        log,    _, _ = _git_run("log", "HEAD..origin/main", "--oneline")
        changes = [l for l in log.splitlines() if l.strip()]
        return {
            "local_commit":  local,
            "remote_commit": remote,
            "up_to_date":    local == remote,
            "changes":       changes,
            "count":         len(changes),
        }
    except subprocess.TimeoutExpired:
        return {"error": "Timed out — check your internet connection."}
    except Exception as e:
        return {"error": str(e)}


@router.post("/update/apply")
def apply_update():
    """Start the update script in the background."""
    global _update_state
    with _UPDATE_LOCK:
        if _update_state["running"]:
            raise HTTPException(status_code=409, detail="An update is already in progress.")
        if not (_APP_DIR / ".git").exists():
            raise HTTPException(status_code=400, detail="Not a git installation.")
        update_script = _APP_DIR / "pi" / "update.sh"
        if not update_script.exists():
            raise HTTPException(status_code=400, detail=f"Update script not found: {update_script}")

    def _run():
        global _update_state
        with _UPDATE_LOCK:
            _update_state = {
                "running":   True,
                "log":       [],
                "exit_code": None,
                "started":   datetime.now().isoformat(),
            }
        try:
            proc = subprocess.Popen(
                ["sudo", "bash", str(update_script)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for line in proc.stdout:
                with _UPDATE_LOCK:
                    _update_state["log"].append(line.rstrip())
            proc.wait(timeout=900)   # 15-min ceiling
            with _UPDATE_LOCK:
                _update_state["exit_code"] = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            with _UPDATE_LOCK:
                _update_state["log"].append("ERROR: Update timed out after 15 minutes.")
                _update_state["exit_code"] = -1
        except Exception as e:
            with _UPDATE_LOCK:
                _update_state["log"].append(f"ERROR: {e}")
                _update_state["exit_code"] = -1
        finally:
            with _UPDATE_LOCK:
                _update_state["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@router.get("/update/status")
def get_update_status():
    """Poll this endpoint to track update progress."""
    with _UPDATE_LOCK:
        snap = dict(_update_state)
    return {
        "running":   snap["running"],
        "exit_code": snap["exit_code"],
        "started":   snap["started"],
        "log":       snap["log"][-200:],   # last 200 lines
        "success":   (snap["exit_code"] == 0) if snap["exit_code"] is not None else None,
    }


# ── Display Schedule ────────────────────────────────────────────────────────────
#
# vcgencmd display_power does NOT work under vc4-kms-v3d (the KMS driver used on
# all Pi OS Bookworm installs).  The dpms sysfs node is read-only, and SDL's
# kmsdrm backend holds DRM master so no other process can issue DRM ioctls.
#
# Solution: write a flag file that display.py polls every frame.  display.py owns
# the display, so it blanks itself (solid black).  The flag file approach is:
#   • Instant (display.py runs at 2 FPS, so ≤0.5 s lag)
#   • Works with any driver — no kernel/firmware cooperation needed
#   • No root / sudo required
#
_DISPLAY_OFF_FLAG = _APP_DIR / ".display_off"


def _set_display_power(on: bool) -> bool:
    """Create or remove the flag file that signals display.py to blank the screen."""
    try:
        if on:
            _DISPLAY_OFF_FLAG.unlink(missing_ok=True)
        else:
            _DISPLAY_OFF_FLAG.touch()
        return True
    except Exception:
        return False


def _should_display_be_on(sched: dict) -> bool:
    """Return True if the display should be on right now according to the schedule."""
    if not sched.get("enabled"):
        return True                           # schedule disabled → always on

    now   = datetime.now()
    today = now.weekday()                     # 0 = Monday … 6 = Sunday
    days  = sched.get("days", list(range(7)))

    if today not in days:
        return True                           # today not scheduled → always on

    on_time  = sched.get("on_time",  "07:00")
    off_time = sched.get("off_time", "22:00")
    cur_min  = now.hour * 60 + now.minute

    on_h,  on_m  = map(int, on_time.split(":"))
    off_h, off_m = map(int, off_time.split(":"))
    on_min  = on_h  * 60 + on_m
    off_min = off_h * 60 + off_m

    if on_min <= off_min:
        # Normal same-day window  e.g. 07:00 → 22:00
        return on_min <= cur_min < off_min
    else:
        # Overnight window  e.g. 22:00 → 07:00
        return cur_min >= on_min or cur_min < off_min


def _display_schedule_ticker():
    """Background thread: enforce display schedule at transition points only.

    Sleeps first so startup never overrides a manual on/off action.
    Only fires _set_display_power when the desired state *changes* (e.g.
    crossing the 22:00 off-time or the 07:00 on-time).  Between transitions
    the flag file is left alone, so manual overrides from the UI persist.
    """
    try:
        cfg = _read_config()
        last_want = _should_display_be_on(cfg.get("display_schedule", {}))
    except Exception:
        last_want = True   # safe default

    while True:
        _time.sleep(60)   # sleep FIRST — don't touch display immediately on startup
        try:
            cfg  = _read_config()
            want = _should_display_be_on(cfg.get("display_schedule", {}))
            if want != last_want:
                last_want = want
                _set_display_power(want)
        except Exception:
            pass


# Start scheduler when the router module loads (daemon thread dies with the process)
threading.Thread(target=_display_schedule_ticker, daemon=True).start()


@router.get("/display_schedule")
def get_display_schedule():
    cfg   = _read_config()
    sched = cfg.get("display_schedule", {})
    return {
        "enabled":        sched.get("enabled",  False),
        "on_time":        sched.get("on_time",  "07:00"),
        "off_time":       sched.get("off_time", "22:00"),
        "days":           sched.get("days",     list(range(7))),
        "display_is_off": _DISPLAY_OFF_FLAG.exists(),   # current live state
    }


@router.put("/display_schedule")
def save_display_schedule(body: dict):
    days = sorted({int(d) for d in body.get("days", list(range(7))) if 0 <= int(d) <= 6})
    _write_config({
        "display_schedule": {
            "enabled":  bool(body.get("enabled", False)),
            "on_time":  body.get("on_time",  "07:00"),
            "off_time": body.get("off_time", "22:00"),
            "days":     days,
        }
    })
    return {"status": "saved"}


@router.post("/display_schedule/power")
def manual_display_power(body: dict):
    """Immediately turn the display on or off via the flag-file mechanism."""
    on = bool(body.get("on", True))
    ok = _set_display_power(on)
    return {"status": "on" if on else "off", "command_ok": ok}
