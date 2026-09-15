# Changelog

All notable changes to this project are documented here.
This project follows [semantic versioning](https://semver.org/).

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
