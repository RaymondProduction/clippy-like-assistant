# Native Clippy for Ubuntu / Wayland

## Features
- small standalone GTK window;
- Clippy animation rendered natively from `assets/clippy_map.png` and `assets/clippy_agent.json`;
- reacts to file system events in `~/Desktop`, `~/Downloads`, and `~/Documents`;
- attempts to react to opened files via `Gtk.RecentManager`;
- works on both Wayland and X11 as a regular GTK application;
- no WebKit, no browser, and no JavaScript runtime.

## Limitations under Wayland
Wayland intentionally restricts global monitoring of other apps' windows.
So this project **does not** provide full old-school desktop-assistant tracking of the active external window.

## Installation
```bash
sudo apt update
sudo apt install -y python3-gi python3-watchdog gir1.2-gtk-3.0
```

## Run
```bash
cd native_clippy_json
python3 app.py
```

## Architecture idea
- `Gtk.Image` displays frames cropped from `assets/clippy_map.png`;
- `assets/clippy_agent.json` stores animation frame coordinates and timings;
- `watchdog` listens for file system events;
- events are queued and Clippy plays matching animations.

## Next steps
- add a `.desktop` file for autostart;
- add a settings panel;
- add more app-specific reactions through D-Bus integrations where available;
- add optional transparent or bubble-only presentation modes.
