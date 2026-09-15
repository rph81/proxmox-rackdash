# Changelog

All notable changes to this project are documented here.
This project follows [semantic versioning](https://semver.org/).

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
