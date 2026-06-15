import asyncio
import json
import os

from fastapi import APIRouter, WebSocket
from sqlalchemy.orm import Session

from database import get_db, UserPrefs

router = APIRouter()

_SHELL_ENV = {
    "TERM": "xterm-256color",
    "COLORTERM": "truecolor",
    "HOME": "/home/dashboard",
    "USER": "dashboard",
    "LOGNAME": "dashboard",
    "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    "LANG": "en_US.UTF-8",
}


def _require_owner_ws(websocket: WebSocket) -> bool:
    """Return True if the WebSocket session belongs to the owner."""
    try:
        from database import SessionLocal
        email = websocket.session.get("email")
        if not email:
            return False
        db: Session = SessionLocal()
        try:
            user = db.get(UserPrefs, email)
            return bool(user and not user.blocked and (user.role or "user") == "owner")
        finally:
            db.close()
    except Exception:
        return False


@router.websocket("/ws")
async def terminal_ws(websocket: WebSocket):
    if not _require_owner_ws(websocket):
        await websocket.close(code=4403)
        return

    await websocket.accept()

    try:
        import ptyprocess
        p = ptyprocess.PtyProcess.spawn(
            ["/bin/bash", "--login"],
            env={**os.environ, **_SHELL_ENV},
        )
    except Exception as exc:
        await websocket.send_text(f"\r\n[Failed to start shell: {exc}]\r\n")
        await websocket.close()
        return

    loop = asyncio.get_running_loop()

    async def pty_reader():
        while p.isalive():
            try:
                data = await loop.run_in_executor(None, lambda: p.read(4096))
                await websocket.send_bytes(data)
            except EOFError:
                break
            except Exception:
                break

    async def ws_reader():
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("bytes"):
                    p.write(msg["bytes"])
                elif msg.get("text"):
                    try:
                        ctrl = json.loads(msg["text"])
                        if ctrl.get("type") == "resize":
                            p.setwinsize(int(ctrl["rows"]), int(ctrl["cols"]))
                    except Exception:
                        pass
        except Exception:
            pass

    reader = asyncio.create_task(pty_reader())
    writer = asyncio.create_task(ws_reader())
    await asyncio.wait([reader, writer], return_when=asyncio.FIRST_COMPLETED)
    reader.cancel()
    writer.cancel()

    try:
        p.close(force=True)
    except Exception:
        pass
