# Changelog

All notable changes to this project are documented here.
This project follows [semantic versioning](https://semver.org/).

## [1.5.1]

### Fixed
- The kiosk kept showing the old page after an upgrade until someone pressed
  F5, so new settings such as *Overview dials* never appeared on the Pi. The
  page now reloads itself when the server reports a new version.
- *Overview dials* moved to the top of Settings, where it is visible without
  scrolling on the 400 px panel, and Settings always opens at the top.
- The *Fan curves / All* switch is relabelled *Sensors available*, with a hint
  that it chooses which sensors are read, not which Overview dials are shown.

## [1.5.0]

### Added
- **Choose the Overview dials.** Settings has a new *Overview dials* panel with
  one button per dial: CPU, Memory, Root disk, Network and every temperature
  sensor. Tap to show or hide it on the Overview. *Automatic* hands the choice
  back. The Temps page is unaffected and always shows every sensor.

### Changed
- By default the Overview shows the four system dials and only the sensors
  that drive a fan. Sensors ticked under Graph in the fan app for interest
  appear on the Temps page but no longer crowd the Overview, so eight dials
  fit on one row instead of shrinking into two.

## [1.4.1]

### Fixed
- Switching fans to **fixed** mode in corsair-fanctl made every temperature
  vanish from the dashboard: the Overview dials, the Temps dials and the Temps
  history. Sensors were only taken from fans in curve mode, but a fixed fan
  still reads its sensors. The dashboard now shows exactly what the fan app
  graphs: every fan's sensors whatever its mode, plus anything ticked under
  Graph in the fan app's Sensors dialog.

## [1.4.0]

### Added
- **Usage detail views.** Tap Processor, Memory or Network to open it full
  page: a large dial, every figure for it, a large chart with an average line,
  and a ranked list — the busiest guests by CPU, the largest by memory, or the
  top network talkers. Each switches between the last hour, day and week.
- **Fan detail view.** Tap a fan on the Temps page for its duty and control
  temperature dials, its curve with the live operating point marked, and its
  duty, control temperature and speed history.
- **Temps range picker.** Tap the range beside History for 5, 15 or 30
  minutes, or 1, 5 or 10 hours. The choice is saved.
- Temperature and fan history now come from corsair-fanctl's own recorded
  history, proxied through `/api/fan-history`, rather than a five-minute
  buffer on the Pi. `/api/rrd` accepts a `timeframe`.

### Changed
- The three Usage charts now cover exactly the same last hour from the same
  source. Previously CPU and memory drew a five-minute live buffer beside an
  hour of network history, so their axes disagreed.
- Charts are positioned by time rather than by sample index, so series of
  different resolutions share an axis. Fills are gradients, the newest reading
  is marked, and the legend sits above the plot instead of over it.

### Fixed
- A 2-wire fan reads `0 rpm` while running because it has no tachometer. It
  is now shown as "no speed signal" rather than as a stalled fan, on both the
  fan card and its detail view.

## [1.3.3]

### Added
- **Header text size** is now adjustable under Settings → Screen, from 100%
  to 200%, defaulting to 130%. One factor drives every size in the bar
  including its height, so the strip grows together and stays centred. It
  previews live while you drag, so it can be judged on the panel itself
  rather than guessed at.

### Changed
- The supporting text in the header (version tag, the PI labels and units,
  the uptime and guest chips) now scales with that factor. Previously only
  the hostname and clock had grown, which is why 1.3.2 did not look much
  different.

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
