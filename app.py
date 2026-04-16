#!/usr/bin/env python3
from __future__ import annotations

import json
import random
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

APP_NAME = "Native Clippy"
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
AGENT_JSON = ASSETS_DIR / "clippy_agent.json"
MAP_PNG = ASSETS_DIR / "clippy_map.png"
SOUNDS_DIR = ASSETS_DIR / "sounds"
CLIPPY_SOUNDS_DIR = SOUNDS_DIR / "clippy"
WATCH_DIRS = [Path.home() / name for name in ("Desktop", "Downloads", "Documents")]
WINDOW_SCALE = 1.55
IDLE_SECONDS = 10.0
MAX_RECENT_AGE_SECONDS = 60.0

EVENT_ANIMATIONS: dict[str, list[str]] = {
    "created_dir": ["GetAttention", "Searching", "Explain"],
    "created_file": ["Writing", "GetAttention", "Save"],
    "modified": ["Processing", "Writing", "Thinking"],
    "deleted": ["EmptyTrash", "GetArtsy", "LookDown"],
    "moved": ["Searching", "GestureLeft", "GestureRight"],
    "opened": ["Greeting", "GetAttention", "Explain"],
    "idle": ["Idle1_1", "IdleAtom", "LookUp", "LookRight", "RestPose"],
}

EVENT_SOUNDS: dict[str, str] = {
    "created_dir": "folder-created",
    "created_file": "file-created",
    "modified": "file-modified",
    "deleted": "file-deleted",
    "moved": "file-moved",
    "opened": "file-opened",
    "idle": "idle",
}

FALLBACK_SOUND_THEMES: dict[str, tuple[str, ...]] = {
    "folder-created": ("complete", "dialog-information", "service-login"),
    "file-created": ("complete", "dialog-information", "service-login"),
    "file-modified": ("message", "dialog-information", "bell"),
    "file-deleted": ("trash-empty", "dialog-warning", "bell"),
    "file-moved": ("window-attention", "dialog-information", "bell"),
    "file-opened": ("button-pressed", "dialog-information", "bell"),
    "idle": ("bell", "dialog-information"),
}

EVENT_MESSAGES: dict[str, list[str]] = {
    "created_dir": [
        "A new folder appeared.",
        "Looks like you created a new folder.",
    ],
    "created_file": [
        "A new file just showed up.",
        "I noticed a freshly created file.",
    ],
    "modified": [
        "Something was updated.",
        "That file changed just now.",
    ],
    "deleted": [
        "Something disappeared.",
        "That file was removed.",
    ],
    "moved": [
        "I saw a file move.",
        "That item changed its location.",
    ],
    "opened": [
        "That file was opened recently.",
        "I noticed a recently opened file.",
    ],
    "idle": [
        "I'm keeping an eye on your files.",
        "Nothing new yet. I'm still here.",
    ],
}

TRANSPARENT_CSS = b"""
window, box, overlay, eventbox {
    background-color: transparent;
    background-image: none;
    box-shadow: none;
    border: none;
}
#bubble {
    background: rgba(18, 18, 18, 0.70);
    border-radius: 12px;
    padding: 8px 10px;
}
#bubble label {
    color: white;
}
#control-pill {
    background: rgba(18, 18, 18, 0.58);
    border-radius: 12px;
    padding: 3px;
}
#control-pill button {
    background: rgba(255, 255, 255, 0.12);
    color: white;
    border: none;
    box-shadow: none;
    border-radius: 8px;
}
#control-pill button:hover {
    background: rgba(255, 255, 255, 0.20);
}
"""


def install_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(TRANSPARENT_CSS)
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(
            screen,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


def clamp_text(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class SoundPlayer:
    def __init__(self, sounds_dir: Path, clippy_sounds_dir: Path) -> None:
        self.sounds_dir = sounds_dir
        self.clippy_sounds_dir = clippy_sounds_dir
        self.canberra = shutil.which("canberra-gtk-play")
        self.paplay = shutil.which("paplay")
        self.aplay = shutil.which("aplay")

    def play_event(self, event_type: str) -> None:
        sound_key = EVENT_SOUNDS.get(event_type)
        if not sound_key:
            return
        custom_sound = self._find_custom_sound(sound_key)
        if custom_sound is not None:
            self._spawn_file_player(custom_sound)
            return
        self._play_fallback_theme(sound_key)

    def play_sound_id(self, sound_id: str | int | None) -> None:
        if sound_id is None:
            return
        sound_path = self._find_clippy_sound(str(sound_id))
        if sound_path is not None:
            self._spawn_file_player(sound_path)

    def _find_custom_sound(self, sound_key: str) -> Path | None:
        for ext in (".wav", ".ogg", ".oga", ".mp3"):
            candidate = self.sounds_dir / f"{sound_key}{ext}"
            if candidate.exists():
                return candidate
        return None

    def _find_clippy_sound(self, sound_id: str) -> Path | None:
        for ext in (".ogg", ".oga", ".wav", ".mp3"):
            candidate = self.clippy_sounds_dir / f"{sound_id}{ext}"
            if candidate.exists():
                return candidate
        return None

    def _spawn_file_player(self, sound_path: Path) -> None:
        cmd = None
        suffix = sound_path.suffix.lower()
        if self.paplay and suffix in {".wav", ".ogg", ".oga"}:
            cmd = [self.paplay, str(sound_path)]
        elif self.aplay and suffix == ".wav":
            cmd = [self.aplay, str(sound_path)]
        elif self.canberra:
            cmd = [self.canberra, "-f", str(sound_path)]
        if cmd is None:
            self._system_beep()
            return
        self._spawn(cmd)

    def _play_fallback_theme(self, sound_key: str) -> None:
        names = FALLBACK_SOUND_THEMES.get(sound_key, ())
        if self.canberra:
            for name in names:
                if self._spawn([self.canberra, "-i", name]):
                    return
        self._system_beep()

    def _spawn(self, cmd: list[str]) -> bool:
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    def _system_beep(self) -> None:
        try:
            display = Gdk.Display.get_default()
            if display is not None:
                display.beep()
        except Exception:
            pass


class AgentData:
    def __init__(self, json_path: Path, map_path: Path) -> None:
        self.data = json.loads(json_path.read_text())
        self.animations: dict[str, dict[str, Any]] = self.data["animations"]
        self.frame_width, self.frame_height = self.data["framesize"]
        self.sprite = GdkPixbuf.Pixbuf.new_from_file(str(map_path))

    def get_frame_pixbuf(self, x: int, y: int, scale: float) -> GdkPixbuf.Pixbuf:
        sub = self.sprite.new_subpixbuf(x, y, self.frame_width, self.frame_height)
        if scale == 1.0:
            return sub
        return sub.scale_simple(
            int(self.frame_width * scale),
            int(self.frame_height * scale),
            GdkPixbuf.InterpType.BILINEAR,
        )

    def animation_has_embedded_sound(self, name: str) -> bool:
        anim = self.animations.get(name, {})
        for frame in anim.get("frames", []):
            if "sound" in frame:
                return True
        return False


class SpriteAnimator:
    def __init__(self, image: Gtk.Image, agent: AgentData, on_done, on_sound) -> None:
        self.image = image
        self.agent = agent
        self.on_done = on_done
        self.on_sound = on_sound
        self.timer_id: int | None = None
        self.active_animation = "RestPose"
        self.active_frames: list[dict[str, Any]] = []
        self.frame_index = 0
        self.scale = WINDOW_SCALE
        self.set_animation("RestPose")

    def cancel(self) -> None:
        if self.timer_id is not None:
            GLib.source_remove(self.timer_id)
            self.timer_id = None

    def set_animation(self, name: str) -> None:
        self.cancel()
        if name not in self.agent.animations:
            name = "RestPose"
        self.active_animation = name
        self.active_frames = self.agent.animations[name].get("frames", [])
        self.frame_index = 0
        self._show_current_frame()

    def _show_current_frame(self) -> None:
        if not self.active_frames:
            return
        frame = self.active_frames[self.frame_index]
        if "sound" in frame:
            self.on_sound(frame.get("sound"))
        images = frame.get("images") or [[0, 0]]
        x, y = images[0]
        pixbuf = self.agent.get_frame_pixbuf(x, y, self.scale)
        self.image.set_from_pixbuf(pixbuf)
        duration = max(int(frame.get("duration", 100)), 30)
        self.timer_id = GLib.timeout_add(duration, self._advance)

    def _advance(self) -> bool:
        self.timer_id = None
        self.frame_index += 1
        if self.frame_index >= len(self.active_frames):
            self.on_done(self.active_animation)
            return False
        self._show_current_frame()
        return False


class EventBridge(FileSystemEventHandler):
    def __init__(self, callback) -> None:
        super().__init__()
        self.callback = callback

    def on_created(self, event):
        self.callback("created_dir" if event.is_directory else "created_file", event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.callback("modified", event.src_path)

    def on_deleted(self, event):
        self.callback("deleted", event.src_path)

    def on_moved(self, event):
        self.callback("moved", getattr(event, "dest_path", event.src_path))


class ClippyWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title=APP_NAME)
        self.set_default_size(260, 270)
        self.set_resizable(False)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_app_paintable(True)
        self.stick()

        screen = self.get_screen()
        if screen is not None:
            visual = screen.get_rgba_visual()
            if visual is not None and screen.is_composited():
                self.set_visual(visual)

        self.connect("draw", self._on_window_draw)
        self.connect("destroy", self._on_destroy)
        self.connect("button-press-event", self._on_button_press)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)

        self.agent = AgentData(AGENT_JSON, MAP_PNG)
        self.sound_player = SoundPlayer(SOUNDS_DIR, CLIPPY_SOUNDS_DIR)
        self.queue: deque[tuple[str, str]] = deque()
        self.is_busy = False
        self.last_idle = time.monotonic()
        self.last_recent_uri: str | None = None
        self.last_recent_seen = 0.0
        self.observer: Observer | None = None

        root = Gtk.Overlay()
        root.set_halign(Gtk.Align.FILL)
        root.set_valign(Gtk.Align.FILL)
        self.add(root)

        drag_box = Gtk.EventBox()
        drag_box.set_visible_window(False)
        drag_box.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        drag_box.connect("button-press-event", self._on_button_press)
        root.add(drag_box)

        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        layout.set_margin_top(8)
        layout.set_margin_bottom(8)
        layout.set_margin_start(8)
        layout.set_margin_end(8)
        drag_box.add(layout)

        self.image = Gtk.Image()
        self.image.set_halign(Gtk.Align.CENTER)
        self.image.set_valign(Gtk.Align.CENTER)
        layout.pack_start(self.image, True, True, 0)

        self.bubble_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.bubble_box.set_name("bubble")
        self.bubble_box.set_halign(Gtk.Align.CENTER)
        self.bubble_box.set_valign(Gtk.Align.END)
        root.add_overlay(self.bubble_box)
        self.bubble_box.set_margin_bottom(12)

        self.message = Gtk.Label(label="Native Clippy is watching your folders.")
        self.message.set_line_wrap(True)
        self.message.set_justify(Gtk.Justification.CENTER)
        self.message.set_max_width_chars(28)
        self.message.set_width_chars(28)
        self.bubble_box.pack_start(self.message, True, True, 0)

        self.control_pill = Gtk.Box(spacing=4)
        self.control_pill.set_name("control-pill")
        self.control_pill.set_halign(Gtk.Align.CENTER)
        self.control_pill.set_valign(Gtk.Align.START)
        root.add_overlay(self.control_pill)
        self.control_pill.set_margin_top(6)

        greet_btn = Gtk.Button(label="Greet")
        greet_btn.connect("clicked", lambda *_: self.enqueue("opened", "Manual greet"))
        self.control_pill.pack_start(greet_btn, False, False, 0)

        idle_btn = Gtk.Button(label="Idle")
        idle_btn.connect("clicked", lambda *_: self.enqueue("idle", "Manual idle"))
        self.control_pill.pack_start(idle_btn, False, False, 0)

        close_btn = Gtk.Button(label="×")
        close_btn.connect("clicked", lambda *_: self.close())
        self.control_pill.pack_start(close_btn, False, False, 0)

        self.animator = SpriteAnimator(self.image, self.agent, self._on_animation_finished, self.sound_player.play_sound_id)

        self.show_all()
        self._start_watchdog()
        self._start_recent_monitor()
        GLib.timeout_add_seconds(2, self._idle_tick)
        self.enqueue("opened", "Application started")

    def _on_window_draw(self, _widget, cr):
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.0)
        cr.set_operator(1)
        cr.paint()
        cr.set_operator(2)
        return False

    def _on_button_press(self, _widget, event):
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 1:
            try:
                self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            except Exception:
                pass
        return False

    def _on_destroy(self, *_args) -> None:
        if self.observer is not None:
            self.observer.stop()
            self.observer.join(timeout=2)
        Gtk.main_quit()

    def _start_watchdog(self) -> None:
        existing = [path for path in WATCH_DIRS if path.exists()]
        if not existing:
            self.message.set_text("No Desktop, Downloads, or Documents folders were found.")
            return
        handler = EventBridge(self._watchdog_callback)
        observer = Observer()
        for path in existing:
            observer.schedule(handler, str(path), recursive=True)
        observer.daemon = True
        observer.start()
        self.observer = observer

    def _watchdog_callback(self, event_type: str, path: str) -> None:
        GLib.idle_add(self.enqueue, event_type, path)

    def _start_recent_monitor(self) -> None:
        self.recent_manager = Gtk.RecentManager.get_default()
        self.recent_manager.connect("changed", self._on_recent_changed)

    def _on_recent_changed(self, *_args) -> None:
        try:
            items = self.recent_manager.get_items()
        except Exception:
            return
        if not items:
            return
        item = max(items, key=lambda it: it.get_modified())
        modified = int(item.get_modified())
        now = int(time.time())
        uri = item.get_uri()
        if now - modified > MAX_RECENT_AGE_SECONDS:
            return
        if self.last_recent_uri == uri and now - self.last_recent_seen < 10:
            return
        self.last_recent_uri = uri
        self.last_recent_seen = now
        display = item.get_display_name() or uri
        self.enqueue("opened", display)

    def enqueue(self, event_type: str, payload: str) -> bool:
        self.queue.append((event_type, payload))
        self._try_start_next()
        return False

    def _try_start_next(self) -> None:
        if self.is_busy or not self.queue:
            return
        event_type, payload = self.queue.popleft()
        anim = random.choice(EVENT_ANIMATIONS.get(event_type, ["RestPose"]))
        text_options = EVENT_MESSAGES.get(event_type, ["Something happened."])
        message = random.choice(text_options)
        name = Path(payload).name if payload else payload
        if name:
            message = f"{message}\n{name}"
        self.message.set_text(clamp_text(message, 120))
        if not self.agent.animation_has_embedded_sound(anim):
            self.sound_player.play_event(event_type)
        self.is_busy = True
        self.last_idle = time.monotonic()
        self.animator.set_animation(anim)
        self.bubble_box.show()

    def _on_animation_finished(self, _animation_name: str) -> None:
        self.is_busy = False
        self.animator.set_animation("RestPose")
        GLib.timeout_add(50, self._continue_queue)

    def _continue_queue(self) -> bool:
        self._try_start_next()
        return False

    def _idle_tick(self) -> bool:
        if not self.is_busy and not self.queue and (time.monotonic() - self.last_idle) >= IDLE_SECONDS:
            self.enqueue("idle", "")
        return True


def main() -> None:
    install_css()
    win = ClippyWindow()
    win.present()
    Gtk.main()


if __name__ == "__main__":
    main()
