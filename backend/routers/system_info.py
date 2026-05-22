from fastapi import APIRouter
import socket

router = APIRouter()


@router.get("")
def get_system_info():
    # Primary outbound IPv4 address
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = socket.gethostbyname(socket.gethostname())
    finally:
        s.close()

    # FQDN — fall back to plain hostname if reverse lookup returns .arpa
    hostname = socket.gethostname()
    fqdn = socket.getfqdn(hostname)
    if fqdn.endswith(".arpa") or fqdn == ip:
        fqdn = hostname
    # Bare hostnames on Pi use mDNS (.local); add suffix so the URL is usable.
    if "." not in fqdn:
        fqdn = fqdn + ".local"

    return {"ip": ip, "fqdn": fqdn}
