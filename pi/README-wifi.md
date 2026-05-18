## Configuring WiFi on an already-flashed SD card

Pi OS Bookworm uses **cloud-init** for network configuration, not the old
`wpa_supplicant.conf` method. The file you need to edit is `network-config`
in the FAT32 boot partition.

### Steps

1. Insert the SD card into your Mac.
2. The boot partition mounts automatically -- it appears in Finder as **bootfs**.
3. Open `bootfs/network-config` in any text editor (TextEdit, VS Code, etc.).
4. Replace the contents with:

```yaml
version: 2
ethernets:
  eth0:
    dhcp4: true
    optional: true
wifis:
  wlan0:
    dhcp4: true
    optional: true
    access-points:
      "YOUR_WIFI_NAME":
        password: "YOUR_WIFI_PASSWORD"
```

5. Save the file, eject the SD card, insert into Pi and power on.

### Multiple networks

```yaml
version: 2
ethernets:
  eth0:
    dhcp4: true
    optional: true
wifis:
  wlan0:
    dhcp4: true
    optional: true
    access-points:
      "HomeNetwork":
        password: "homepassword"
      "GuestNetwork":
        password: "guestpassword"
```

### Ethernet only

If the Pi is connected via ethernet you do not need to edit anything --
`eth0` is already configured for DHCP.

### After first boot

Once the Pi has booted and run firstrun.sh, WiFi is managed by NetworkManager.
You can change networks at any time with:

```bash
sudo nmcli device wifi connect "SSID" password "PASSWORD"
```

Or add/remove connections:

```bash
sudo nmcli connection show
sudo nmcli connection delete "old-network"
```

### Why not wpa_supplicant.conf?

The `wpa_supplicant.conf` trick (placing the file in the boot partition) worked
on Pi OS Bullseye and earlier. Pi OS Bookworm (Debian 12) switched the network
stack to NetworkManager and cloud-init. Placing `wpa_supplicant.conf` in the
boot partition no longer has any effect on Bookworm.
