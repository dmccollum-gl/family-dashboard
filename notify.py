#!/usr/bin/env python3
"""
Send a TTS message to a Google Home / Cast device on the local network.
Usage: python3 notify.py "message" [device_name]
Default device: Kitchen Display
"""
import sys, os, time, tempfile, threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
import pychromecast
from gtts import gTTS

DEVICE = sys.argv[2] if len(sys.argv) > 2 else "Kitchen Display"
MESSAGE = sys.argv[1] if len(sys.argv) > 1 else "Task complete"

# Generate TTS mp3
tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
gTTS(MESSAGE).save(tmp.name)
tmp.close()

# Serve the mp3 over a tiny local HTTP server
class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def translate_path(self, path): return tmp.name

server = HTTPServer(("", 0), QuietHandler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

# Find device and cast
chromecasts, browser = pychromecast.get_chromecasts(timeout=10)
cast = next((c for c in chromecasts if c.cast_info.friendly_name == DEVICE), None)

if not cast:
    print(f"Device '{DEVICE}' not found. Available:")
    for c in chromecasts: print(f"  {c.cast_info.friendly_name}")
    browser.stop_discovery()
    sys.exit(1)

cast.wait()
mc = cast.media_controller
host = cast.cast_info.host.split(".")[0]  # use IP of this Mac instead

import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.connect((cast.cast_info.host, 80))
local_ip = s.getsockname()[0]
s.close()

url = f"http://{local_ip}:{port}/notify.mp3"
mc.play_media(url, "audio/mp3")
mc.block_until_active(timeout=10)

# Wait for playback to finish
time.sleep(8)
server.shutdown()
browser.stop_discovery()
os.unlink(tmp.name)
print(f"Sent to {DEVICE}: \"{MESSAGE}\"")
