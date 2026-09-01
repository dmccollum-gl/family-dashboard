# WiFi Configuration

How WiFi gets set depends on which image builder you use.

## Two image builders

| Builder | How it installs | WiFi setup |
|---|---|---|
| `build-image.sh` (macOS-native, no Docker) | Clones the app from GitHub on **first boot** → needs internet immediately | **Set WiFi in Raspberry Pi Imager's "Edit Settings"** before flashing. Without it the first-boot install has no internet and aborts. |
| `build-image-docker.sh` (Docker + chroot) | App is **pre-installed** into the image → no internet needed at first boot | **On-device hotspot.** No WiFi in Imager — the Pi boots the **"Dashboard-Setup"** hotspot; you join it and configure WiFi in the browser. |

For the hotspot experience most people want, use **`build-image-docker.sh`**.

## First boot — the hotspot flow (Docker image)

1. Flash `pi/output/family-dashboard-hotspot.img` (Raspberry Pi Imager → Use custom → Write; **skip Edit Settings**).
2. Boot the Pi. It starts the **`Dashboard-Setup`** WiFi hotspot (the screen shows instructions).
3. Join that hotspot, open **`http://10.42.0.1`**, and enter your WiFi + device name + a login password.
4. The Pi connects to your network and reboots into the dashboard.

## Changing WiFi after setup

- **From the dashboard:** Settings → **WiFi** (owner-only) — scan, pick a network, connect. It falls back to the current network if the new password is wrong.
- **Over SSH / console** with `nmcli`:
  ```bash
  sudo nmcli device wifi connect "SSID" password "PASSWORD"
  nmcli connection show
  sudo nmcli connection delete dashboard-wifi   # remove a saved network
  ```

## Notes

- Pi Zero 2W is **2.4 GHz only** — a 5 GHz-only SSID won't connect.
- Pi OS Bookworm uses NetworkManager keyfiles at
  `/etc/NetworkManager/system-connections/*.nmconnection` (not wpa_supplicant/cloud-init).
- Raspberry Pi Imager's "Edit Settings" popup only appears for official catalog
  images — for a custom `.img` you must open it via the gear/⌘⇧X in Imager.
