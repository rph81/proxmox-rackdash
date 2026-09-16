# proxmox-rackdash

[![tests](https://github.com/rph81/proxmox-rackdash/actions/workflows/test.yml/badge.svg)](https://github.com/rph81/proxmox-rackdash/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python: 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

*Package and service name: `rackdash`*

A Proxmox VE dashboard for a rack-mounted touchscreen, built for a **GeeekPi
7.84" 1280x400** panel in a DeskPi RackMate on a **Raspberry Pi 5**.

It shows CPU, memory, disk and network for the host, drills into any VM or
container, and displays **exactly the temperature sensors you assigned to a fan
curve** in [corsair-fanctl](https://github.com/rph81/corsair-fanctl) — tick a
new sensor there and it appears here on the next poll.

- Six pages, switched from a vertical touch strip: **Overview**, **Usage**,
  **Temps**, **VMs**, **Node**, **Settings**.
- Animated **arc dials** (a 288° sweep, not a full circle) whose colour moves
  smoothly from green through amber and orange to red as a value climbs.
- **Twelve global themes**, including a black-and-yellow *Bambu* to match a
  3D-printed rack and a *Flight Deck* built from the cv.hadife.com palette. A
  theme repaints every page, dial ramp and chart series at once.
- **Fifteen backdrops** — dot matrix, blueprint, circuit traces, topographic
  and more — with a strength slider and a colour picker for the corner glow.
- Tap any VM or container for its own CPU, memory, disk and network dials plus
  hour/day/week history.
- No pip packages. The Pi needs `python3` and nothing else.

## How it fits together

The Pi is only a display. Nothing is installed on the Proxmox host.

```
Raspberry Pi 5                          Proxmox host
┌──────────────────────────┐            ┌─────────────────────────┐
│ rackdash daemon          │──── API ──▶│ pvedaemon :8006         │
│  polls, caches, serves   │            │  (read-only API token)  │
│                          │──── API ──▶│ corsair-fanctl :8899    │
│ chromium --kiosk ────────┤            │  (fan curves + temps)   │
│  → 127.0.0.1:8080        │            └─────────────────────────┘
└──────────────────────────┘
   1280x400 touchscreen
```

The daemon holds the API token and serves the page on loopback; the browser
never talks to Proxmox directly, so the token never reaches the screen.

## Install

### 1. Make a read-only API token on the Proxmox host

```bash
pveum user token add root@pam rackdash --privsep 0
```

Copy the `value` it prints — it is shown once. For a token that cannot change
anything, create a dedicated user instead:

```bash
pveum user add rackdash@pve
pveum acl modify / --users rackdash@pve --roles PVEAuditor
pveum user token add rackdash@pve dash --privsep 0
```

### 2. Install on the Pi

```bash
git clone https://github.com/rph81/proxmox-rackdash && cd proxmox-rackdash
sudo ./install.sh \
  --pve https://192.168.1.10:8006 \
  --token-id 'rackdash@pve!dash' \
  --token-secret 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx' \
  --fanctl http://192.168.1.10:8899 \
  --kiosk
```

`--kiosk` installs a full-screen browser on tty1 that starts at boot. It runs
`cage`, a Wayland compositor that shows exactly one program full screen: no
desktop, no taskbar, no way to switch apps. Install it and set the Pi to boot
to a console rather than a desktop:

```bash
sudo apt install -y cage chromium-browser
sudo raspi-config nonint do_boot_behaviour B2    # console, autologin
```

The kiosk claims tty1 from the login prompt and restarts the browser if it
ever exits. SSH and the other text consoles (Ctrl+Alt+F2) are unaffected. To
go back to a desktop: `sudo systemctl disable --now rackdash-kiosk` and
`sudo raspi-config nonint do_boot_behaviour B4`.

**Prefer to keep the desktop?** Skip `--kiosk` and autostart the browser from
the desktop session instead:

```bash
mkdir -p ~/.config/autostart && cat > ~/.config/autostart/rackdash.desktop <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=rackdash
Exec=chromium-browser --kiosk --app=http://127.0.0.1:8080/?kiosk=1 --noerrdialogs --disable-infobars --disable-session-crashed-bubble --check-for-update-interval=31536000
DESKTOP
```

Without either, open `http://127.0.0.1:8080/` in any browser on the Pi.

### 3. Check it

```bash
sudo -u rackdash python3 -m rackdash --config /etc/rackdash/config.json --check
```

That polls both sources once and prints what came back, including which
temperature sensors were picked up from the fan controller.

## The pages

| Page | What it shows |
|---|---|
| **Overview** | One dial per metric: CPU, memory, root disk, network, then one per selected temperature sensor. Below: load average, guest count, fullest pool, hottest sensor, average fan speed. |
| **Header** | The Pi's own SoC temperature, CPU load and GPU load, badged `PI` to keep them apart from the hypervisor's numbers. |
| **Usage** | Processor, memory and network in detail — model, load averages, I/O wait, swap, receive/transmit, one-hour peak — each with a live chart. |
| **Temps** | Large dials for the selected sensors, a shared history chart, and every fan channel with its RPM and duty. |
| **VMs** | Every VM and container as a tile with live CPU and memory bars. Tap one for its own dials and hour/day/week history. |
| **Node** | Proxmox and kernel versions, CPU model, uptime, storage pools and recent tasks. |
| **Settings** | Themes, accent, dial colour ramps, screen behaviour and source status. |

## Temperatures follow your fan curves

This is the part that ties the two projects together. `corsair-fanctl` already
knows which sensors matter: each fan carries a list of sensor ids, a catalog of
id to human label, and the live readings. rackdash reads its `/api/state` and
shows the union of sensors bound to a fan that is **enabled and in curve
mode**, in fan order.

So if fan 1 follows the hottest drive, fan 2 follows the CPU package and fan 3
follows probe 1, the dashboard shows those three and nothing else. Assign a
fourth sensor in the fan UI and a fourth dial appears within two seconds. A
channel set to *fixed* or *off* is not following a temperature, so its sensors
are not shown.

Under **Settings → Temperature sensors** you can switch to **All** to show
every sensor the fan controller knows about instead.

## Themes

A theme is a whole look, not a tint: background, panels, lines, text, the dial
colour ramp and the chart series palette all change together, across every
page.

| Theme | Look |
|---|---|
| **Midnight** | Default slate blue |
| **Flight Deck** | The cv.hadife.com navy, cyan and violet |
| **Bambu** | Black and yellow, like the printed rack |
| **Ice** | Deep navy and pale cyan |
| **Synthwave** | Purple and magenta |
| **Terminal** | Phosphor green on black |
| **Nord** | Cool arctic blue-grey |
| **Carbon** | Neutral graphite |
| **Ember** | Warm dark and orange |
| **Slate** | Soft neutral dark |
| **Daylight** | Light, high contrast |
| **Paper** | Warm light |

Picking one sets the accent, both colour ramps and the chart palette. Anything
can then be adjusted individually: the accent has eight presets, a full picker
and ten favourite slots, and each colour ramp is an editable list of stops.

## This Pi's own vitals

The header carries three readouts for the machine the screen is bolted to,
badged `PI` so they are never confused with the hypervisor's figures:

| Readout | Where it comes from |
|---|---|
| Temperature | The SoC thermal zone, matched by type rather than assuming zone 0 |
| CPU | `/proc/stat`, as busy percentage between polls |
| GPU / V3D | Real utilisation if the kernel exposes it, otherwise the V3D clock |

**About the GPU figure.** It comes from the V3D driver's own counters at
`/sys/devices/platform/axi/*.v3d/gpu_stats`, which is the same source the
Raspberry Pi desktop's GPU widget uses. That file publishes accumulated busy
nanoseconds for each of the V3D scheduling queues (bin, render, tfu, csd,
cache_clean) alongside the clock they are measured against, so differencing
two reads gives true utilisation. Because it is a ratio of two values from the
same clock, the units cancel and nothing has to be assumed.

The queues run concurrently, so summing them could exceed the wall clock. The
readout shows the busiest queue, which is the honest answer to "how hard is
the GPU working" and is naturally bounded at 100%.

The file is world-readable, so no privileges are needed. On a kernel too old
to have it the reader falls back to debugfs, then devfreq, then the V3D clock
via `vcgencmd` — and if only a clock is available it says `V3D 960 MHz` rather
than dressing a clock up as a percentage. Hover the readout to see which
source answered.

Turn the whole group off under **Settings → Screen**, where **Header text**
also scales the whole top bar between 100% and 200%. The default is 130%,
because at ~170 dpi a bar sized for a laptop is half the physical size in a
rack.

## Backdrops

The pages sit on a flat ground by default. **Settings → Backdrop** offers
fifteen patterns, each one CSS declaration with no image files, on a single
layer behind the content:

| Quiet | Medium | Strong |
|---|---|---|
| Dots, Grid, Plate, Vignette | Blueprint, Hatch, Crosshairs, Carbon, Corner glow | Hex, Circuit, Topo, Scanlines, Grain |

Every tile is seamless: each trace or contour that leaves one edge re-enters
the opposite edge at the same coordinate, so a pattern reads as one continuous
surface rather than a grid of repeated blocks. Circuit is modelled on a board
fanning out from a connector, with radiused jogs and round pads.

A **strength** slider scales any of them from off to double, which matters
because this panel is about 170 dpi — roughly twice a desktop monitor — so a
pattern renders at half the size it looks on a laptop. The busier ones read
best at 60% or below.

Every pattern is drawn in one neutral slate at low alpha rather than a fixed
light or dark colour. Over a dark ground it lightens, over a light ground it
darkens, so a single value carries all twelve themes with no per-theme
variants. **Corner glow** is the exception: it takes its own colour, chosen
from eight presets or a full picker, and follows the accent until you pin it.

A backdrop is independent of the theme, so switching theme keeps whichever one
you picked.

### Dial colours

The arc colour is interpolated between stops rather than snapped, so a value
sweeps through the ramp as it climbs. Usage stops are percentages; temperature
stops are degrees, because that is how you think about a drive.

Set **Arc colour** to *Accent* for a single flat colour instead.

## Configuration file

`/etc/rackdash/config.json`, owned by the `rackdash` user and mode 0600
because it holds the API token.

```jsonc
{
  "http": { "bind": "127.0.0.1", "port": 8080 },
  "proxmox": {
    "host": "https://192.168.1.10:8006",
    "node": "",                 // blank = the first node the API reports
    "token_id": "rackdash@pve!dash",
    "token_secret": "…",        // file-only, never settable over HTTP
    "verify_tls": false,        // PVE ships a self-signed certificate
    "interval": 2.0,
    "link_mbit": 1000           // NIC speed, scales the network dial
  },
  "fanctl": {
    "enabled": true,
    "url": "http://192.168.1.10:8899",
    "token": "",                // only if http.auth_token is set there
    "source": "fanctl-selected" // fanctl-selected | all | list
  },
  "ui": {
    "theme": "midnight",
    "accent": "#4aa3ff",
    "pattern": "none",          // backdrop; see Settings → Backdrop
    "pattern_strength": 100,    // percent, 0-200
    "glow_color": "",           // corner glow; blank follows the accent
    "dial_color": "threshold",  // threshold | accent
    "temp_min": 20, "temp_max": 90,
    "rotate_seconds": 0,        // >0 cycles pages automatically
    "dim_after": 0              // >0 dims the screen after N idle seconds
  }
}
```

`proxmox.host`, `proxmox.token_secret` and `fanctl.token` are **file-only**.
Everything that reaches the dashboard port could otherwise repoint the daemon
at another server or read the secret back, so those three always keep their
file values and the API ignores them. Change them by editing the file and
restarting.

## Access control

The daemon binds `127.0.0.1`, so by default only the kiosk browser on the Pi
can reach it. To view it from a phone, set `http.bind` to `0.0.0.0` and
restart — on a management VLAN that is reasonable, on anything routable it is
not, since the dashboard exposes host metrics and guest names.

State-changing requests must be same-origin and `Content-Type:
application/json`, so a page in another browser tab cannot reconfigure it.

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/state` | Everything the pages draw, config included (secrets redacted) |
| `GET` | `/api/live` | Short high-resolution series for the sparklines |
| `GET` | `/api/rrd` | The node's Proxmox RRD series |
| `GET` | `/api/guest/{qemu,lxc}/<vmid>?timeframe=hour` | One guest's status and history |
| `GET` / `PUT` | `/api/config` | Read or patch the config (partial PUTs merge) |
| `POST` | `/api/refresh` | Poll both sources immediately |
| `GET` | `/api/healthz` | Liveness |

## Polling

Different things change at different rates, and a dashboard that hammers a
hypervisor makes a bad day worse:

| What | Every |
|---|---|
| Node status, CPU, memory | 2 s |
| Fan controller temperatures | 2 s |
| Guests, storage, tasks, per-guest network | 15 s |
| Proxmox RRD history | 60 s (it only advances that often) |
| Physical disks | 5 min |
| One guest's detail, while its page is open | 3 s |

Node network throughput comes from the RRD, because Proxmox exposes no live
per-node byte counter. It is a one-minute average and the Usage page says so.
Per-guest rates are differenced from cumulative counters and are live.

## Troubleshooting

**"Proxmox rejected the API token".** The token id must include the user and
the token name, like `rackdash@pve!dash`. Check the role is applied:

```bash
pveum acl list
```

**"TLS certificate rejected".** Proxmox uses a self-signed certificate. Set
`proxmox.verify_tls` to `false` (the default).

**The Temps page is empty.** Either the fan controller is unreachable, or no
fan is in curve mode with a sensor assigned. The banner says which. Confirm
with:

```bash
curl -s http://<pve-ip>:8899/api/state | head -c 400
```

**A VM shows a dash for disk.** QEMU guests report disk usage only with the
guest agent installed. The dial is labelled `DISK (NO AGENT)` rather than
showing a misleading 0%. Containers always report it.

**The screen is blank or the browser did not start.**

```bash
journalctl -u rackdash-kiosk -n 40 --no-pager
```

**Logs:** `journalctl -u rackdash -f`

## Development

`tools/devsim.py` runs the whole dashboard against a simulated Proxmox host and
fan controller — nine guests, wandering load, drive temperatures that respond
to fan duty. No hardware needed:

```bash
python3 tools/devsim.py --port 8080    # then open http://127.0.0.1:8080/
python3 tools/selftest.py              # config, shaping, selection and API tests
```

Size a browser window to 1280x400 to see what the panel will show.

## Layout

```
rackdash/
  config.py      defaults, normalisation/clamping, redaction, atomic save
  proxmox.py     Proxmox VE API client and response shaping
  fanctl.py      corsair-fanctl client; which sensors a fan curve uses
  collector.py   two polling threads, the snapshot, per-guest fetch
  history.py     short ring buffers for the live sparklines
  httpd.py       stdlib HTTP server, JSON API, static files
web/             single page: no framework, no build step
tools/           devsim.py (simulator), selftest.py
systemd/         daemon and kiosk units
```

## Licence

MIT.
