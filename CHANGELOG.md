# Changelog

All notable changes to this project are documented here.
This project follows [semantic versioning](https://semver.org/).

## [1.3.2]

### Changed
- Larger header text. The panel is about 170 dpi, so the top bar rendered at
  roughly half the physical size it appears at on a laptop and was hard to
  read across a room. The bar grows from 40px to 46px and its type scales
  with it: hostname and the Pi readouts to 17px, the clock to 20px, labels
  and chips up a step. The overview strip gives back the six pixels.

## [1.3.1]

### Changed
- Cut redundant browser work on every poll. Charts no longer reallocate the
  canvas backing store unless the size actually changed, dials skip the
  450ms tween when a reading moved by less than 0.15, and an arc whose
  position is unchanged no longer restarts its CSS transition. Host readings
  jitter constantly, so without these the page kept a render loop alive
  permanently for changes too small to see.

Note for anyone whose Pi shows high idle CPU: check `journalctl --since "1 min
ago" | wc -l` first. On the machine this was tuned against, a Raspberry Pi
Connect sign-in loop was consuming roughly 35% of the CPU on its own, and no
amount of dashboard tuning would have touched it.

## [1.3.0]

### Added
- **Real GPU utilisation.** The V3D driver publishes accumulated busy time per
  scheduling queue at `/sys/devices/platform/axi/*.v3d/gpu_stats`, alongside
  the clock those totals are measured against. Differencing two reads gives
  true utilisation, which is what the Raspberry Pi desktop's own GPU widget
  uses. The readout is a percentage again, beside the CPU one, and needs no
  privileges because the file is world-readable.
- Queues run concurrently, so the figure is the busiest queue rather than
  their sum, which keeps it bounded at 100% and meaningful.

The clock fallbacks remain for kernels without that file, and still label
themselves as a clock rather than pretending to be utilisation.

## [1.2.3]

### Fixed
- The GPU readout is finally live on a Pi. `DevicePolicy=closed` blocks
  `/dev/vcio` regardless of a matching `DeviceAllow=`, which bisecting the
  unit with `systemd-run` showed conclusively: the entire hardening set
  passes without it and fails with it. The device restriction is dropped;
  every other protection in the unit is unchanged, and file permissions plus
  an unprivileged user with no capabilities already govern what it can open.

## [1.2.2]

### Fixed
- The GPU readout was still blank after 1.2.1. `PrivateDevices=yes` builds the
  service a private `/dev` from a fixed list of pseudo-devices, and
  `DeviceAllow=` only widens the cgroup filter rather than adding nodes to
  that list, so `/dev/vcio` stayed invisible. `vcgencmd` then fell back to
  creating its own node and failed as an unprivileged user. The unit now uses
  `DevicePolicy=closed` with the mailbox allowed, which is narrower in device
  terms than a private `/dev` and actually lets the tool work.
- The installer warned "No Proxmox token configured yet" whenever it was
  re-run without flags, even with a token sitting in the config. It now asks
  the saved config rather than the command line.

## [1.2.1]

### Fixed
- The GPU readout was always blank on a real Pi. The service unit sets
  `PrivateDevices=yes`, which hid `/dev/vcio` and left `vcgencmd` unable to
  talk to the VideoCore firmware even with the `video` group. The unit now
  allows that node specifically, and a failure is logged with its reason
  instead of silently producing nothing.
- The GPU figure no longer pretends a clock is a utilisation percentage. A
  stock Pi kernel exposes no GPU load anywhere readable, and inferring one
  from the V3D clock read 100% whenever the clock sat at its ceiling, which
  on a Pi 5 is most of the time. The readout now shows `GPU 37%` only when
  something genuinely measures load, and `V3D 960 MHz` otherwise, labelled
  and coloured as the clock it is.

## [1.2.0]

### Added
- The header now shows **this Pi's own** SoC temperature, CPU load and GPU
  load, badged `PI` to keep them apart from the hypervisor's numbers. Turn
  them off under Settings → Screen.
- GPU load is read from whichever source answers: v3d debugfs, devfreq, or
  the V3D clock via `vcgencmd`. The readout names the source on hover, and
  shows a dash rather than a made-up number when none is available. The
  installer adds the service user to the `video` group for `vcgencmd`.

### Fixed
- **Circuit** and **Topo** backdrops tiled visibly: traces and contours did
  not line up across tile edges, so the pattern read as repeated blocks.
  Both are rebuilt as genuinely seamless tiles, and Circuit is redrawn as a
  board fanning out from a connector with radiused jogs and round pads. Hex
  was rebuilt the same way.
- Reading CPU busy twice inside one kernel tick returned nothing, which
  blinked the readout to a dash. It now holds the previous figure.

## [1.1.1]

### Fixed
- The kiosk unit was wanted by `graphical.target`, but a kiosk Pi boots to a
  console with no desktop, so that target is never reached and the kiosk
  silently never started. It is now wanted by `multi-user.target`.
- The kiosk and the tty1 login prompt both claimed the console. The unit now
  conflicts with `getty@tty1` so the kiosk takes the screen cleanly.
- README now documents both routes: `cage` for a console-only kiosk Pi, and a
  desktop autostart entry for people who want to keep the desktop.

## [1.1.0]

### Added
- **Backdrops**: fifteen background patterns under Settings — dots, grid,
  blueprint, plate, hatch, crosshairs, hex, circuit traces, topographic,
  scanlines, carbon weave, grain, corner glow and vignette — each a single
  CSS declaration with no image files.
- A **strength** slider scales any backdrop from off to double. It previews
  live while dragging and saves on release.
- **Corner glow** takes its own colour, from eight presets or a full picker,
  and follows the accent until pinned.

Backdrops are independent of the theme, so switching theme keeps the one you
picked. Each pattern is drawn in one neutral at low alpha, which reads on both
dark and light grounds without per-theme variants.

## [1.0.0]

Initial release.

### Added
- Six touch pages for a 1280x400 rack panel: Overview, Usage, Temps, VMs,
  Node and Settings, switched from a vertical strip.
- Animated arc dials with a 288° sweep whose colour is interpolated through
  configurable stops, so a value sweeps green to red as it climbs.
- Host CPU, memory, root disk and network, with live sparklines and Proxmox
  RRD history.
- Temperature sensors taken from corsair-fanctl: the dashboard shows exactly
  the sensors bound to an enabled fan curve, and follows changes made there.
- Per-VM and per-container drill-down with its own dials and hour/day/week
  history, reached by tapping a tile.
- Twelve global themes, including Bambu (black and yellow) and Flight Deck
  (the cv.hadife.com palette). A theme sets the palette, accent, both dial
  ramps and the chart series colours across every page.
- Auto-cycle between pages and an idle dim overlay, both optional.
- `--check` polls both sources once and prints what came back.
- `tools/devsim.py` simulates a Proxmox host and fan controller so the whole
  dashboard can be developed with no hardware.
