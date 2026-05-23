import asyncio
import json
import os
import subprocess
import threading

from fastapi import APIRouter
from pydantic import BaseModel
from database import SessionLocal, UserPrefs
from routers.calendar import _events_cache

router = APIRouter()

CONFIGURED_FLAG = "/opt/dashboard/.configured"
CONFIG_PATH     = "/opt/dashboard/backend/dashboard_config.json"
APPLY_SCRIPT    = "/opt/dashboard/pi-setup-apply.sh"
HOTSPOT_CON     = "dashboard-hotspot"


def _reset_user_data():
    """Delete all calendar users and clear RSS feeds — called on every fresh setup."""
    # Clear all signed-in users from the database.
    db = SessionLocal()
    try:
        db.query(UserPrefs).delete()
        db.commit()
    finally:
        db.close()

    # Invalidate the in-memory calendar events cache.
    _events_cache["data"] = None
    _events_cache["ts"]   = 0.0

    # Clear RSS feeds from config (keep OAuth creds and weather settings).
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)
            config["rss_feeds"] = []
            with open(CONFIG_PATH, "w") as f:
                json.dump(config, f, indent=2)
        except Exception:
            pass

@router.post("/reboot")
def reboot_pi():
    """Reboot the Pi by calling the apply script with no SSID (skips WiFi, just reboots)."""
    if not _on_pi():
        return {"success": True}
    try:
        hostname = subprocess.run(
            ["hostname"], capture_output=True, text=True, timeout=5
        ).stdout.strip() or "cal"

        def _do():
            import time
            time.sleep(1.5)
            try:
                proc = subprocess.Popen(
                    ["sudo", "-n", APPLY_SCRIPT, "", hostname],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.communicate(input=b"\n", timeout=120)
            except Exception:
                pass

        threading.Thread(target=_do, daemon=True).start()
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.post("/reset")
def reset_install():
    """Remove all calendar users and RSS feeds; keep OAuth and weather credentials."""
    _reset_user_data()
    return {"success": True}


# On non-Pi dev machines /opt/dashboard won't exist; treat as already configured.
def _on_pi() -> bool:
    return os.path.isdir("/opt/dashboard")


def _network_status() -> dict:
    """Return the current active non-hotspot connection, if any."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE,STATE", "con", "show", "--active"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) < 3:
                continue
            name, con_type, state = parts[0], parts[1], parts[2]
            if name == HOTSPOT_CON or state != "activated":
                continue
            if con_type == "802-3-ethernet":
                return {"connected": True, "connection_type": "ethernet", "ssid": None}
            if con_type == "802-11-wireless":
                return {"connected": True, "connection_type": "wifi", "ssid": name}
    except Exception:
        pass
    return {"connected": False, "connection_type": "none", "ssid": None}


# ---------------------------------------------------------------------------

@router.get("/status")
async def setup_status():
    if not _on_pi():
        return {
            "configured": True,
            "setup_mode": False,
            "connected": True,
            "connection_type": "ethernet",
            "ssid": None,
        }
    net, hotspot = await asyncio.gather(
        asyncio.to_thread(_network_status),
        asyncio.to_thread(_hotspot_active),
    )
    return {
        "configured": os.path.exists(CONFIGURED_FLAG),
        "setup_mode": hotspot,
        **net,
    }


@router.get("/wifi/scan")
def wifi_scan():
    # Trigger a rescan on wlan0.  In AP mode this may silently do nothing, but
    # --rescan yes on the list call below will also attempt it.
    try:
        subprocess.run(
            ["nmcli", "device", "wifi", "rescan", "ifname", "wlan0"],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass

    # iw-based fallback scan trigger: works even when NM won't rescan in AP mode
    try:
        subprocess.run(["iw", "dev", "wlan0", "scan", "trigger"],
                       capture_output=True, timeout=8)
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY",
             "device", "wifi", "list", "ifname", "wlan0", "--rescan", "yes"],
            capture_output=True, text=True, timeout=20,
        )
        networks, seen = [], set()
        for line in result.stdout.strip().splitlines():
            parts = line.split(":")
            ssid = parts[0].strip() if parts else ""
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            networks.append({
                "ssid":     ssid,
                "signal":   int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0,
                "security": parts[2] if len(parts) > 2 else "none",
            })
        networks.sort(key=lambda n: n["signal"], reverse=True)
        # Filter out the Pi's own hotspot SSID from the list
        networks = [n for n in networks if n["ssid"] != "Dashboard-Setup"]
        return {"networks": networks}
    except Exception as exc:
        return {"networks": [], "error": str(exc)}


class SetupRequest(BaseModel):
    ssid:              str
    password:          str
    device_name:       str
    city:              str
    activation_code:   str
    already_connected: bool = False   # skip WiFi reconfiguration


@router.post("/configure")
def configure(req: SetupRequest):
    if not _on_pi():
        # Dev machine — save config locally and simulate success
        local_config = os.path.join(os.path.dirname(__file__), "..", "dashboard_config.json")
        local_config = os.path.abspath(local_config)
        try:
            config: dict = {}
            if os.path.exists(local_config):
                with open(local_config) as f:
                    config = json.load(f)
            config["device_name"]     = req.device_name
            config["owm_location"]    = req.city
            config["activation_code"] = req.activation_code
            with open(local_config, "w") as f:
                json.dump(config, f, indent=2)
        except Exception:
            pass
        _reset_user_data()
        return {"success": True}

    try:
        # Write dashboard config
        config: dict = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                config = json.load(f)
        config["device_name"]     = req.device_name
        config["owm_location"]    = req.city
        config["activation_code"] = req.activation_code
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)

        # Clear calendar users and RSS feeds — fresh install starts clean.
        _reset_user_data()

        # Mark device as configured
        open(CONFIGURED_FLAG, "w").close()

        # Background thread so the HTTP response reaches the client first.
        # sudo -n: non-interactive (no TTY required) — works from subprocess context.
        ssid_arg     = "" if req.already_connected else req.ssid
        # Append newline so bash `read` returns 0; without it, read returns non-zero
        # at EOF and the `|| PASSWORD=""` fallback overwrites the correct password.
        password_in  = b"" if req.already_connected else (req.password.encode() + b"\n")

        def _apply(ssid=ssid_arg, pw=password_in):
            import time, logging
            time.sleep(1.5)
            try:
                proc = subprocess.Popen(
                    ["sudo", "-n", APPLY_SCRIPT, ssid, req.device_name],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                out, err = proc.communicate(input=pw, timeout=120)
                if proc.returncode != 0:
                    logging.error("[setup] apply script rc=%d stderr=%s", proc.returncode, err.decode(errors="replace"))
            except Exception as exc:
                logging.exception("[setup] apply script failed: %s", exc)

        threading.Thread(target=_apply, daemon=True).start()

        return {"success": True}

    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------

def _hotspot_active() -> bool:
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,STATE", "con", "show", "--active"],
            capture_output=True, text=True, timeout=5,
        )
        return HOTSPOT_CON in result.stdout
    except Exception:
        return False
