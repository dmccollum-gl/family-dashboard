## WiFi Configuration

WiFi credentials are **baked into the image at build time** by `build-image.sh`.
You will be prompted for your SSID and password when you run the build script.
No manual file editing or Imager customisation step is needed.

### Why not Raspberry Pi Imager's "Edit Settings"?

Imager v2.x only shows the OS customisation popup for official Pi OS catalog
images, not for custom `.img` files. It silently skips the step for custom images.

### Changing WiFi after flashing

SSH into the Pi and use `nmcli`:

```bash
# Connect to a new network
sudo nmcli device wifi connect "SSID" password "PASSWORD"

# List saved connections
nmcli connection show

# Delete the baked-in connection
sudo nmcli connection delete dashboard-wifi
```

Or use `raspi-config`:

```bash
sudo raspi-config
# Navigate to: System Options -> Wireless LAN
```

### How it works under the hood

`build-image.sh` passes your WiFi credentials into the Docker chroot as
environment variables. `chroot-setup.sh` writes them as a NetworkManager
keyfile at `/etc/NetworkManager/system-connections/dashboard-wifi.nmconnection`
with permissions `600`. This is the native Bookworm method — wpa_supplicant
and cloud-init are not used by Pi OS Bookworm.
