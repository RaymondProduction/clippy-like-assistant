#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import random
import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gi

gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

APP_NAME = 'Office Assistant Clone'
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / 'assets'
AGENTS_DIR = ASSETS_DIR / 'agents'
WATCH_DIRS = [Path.home() / name for name in ('Desktop', 'Downloads', 'Documents')]
WINDOW_SCALE = 1.55
IDLE_SECONDS = 10.0
MAX_RECENT_AGE_SECONDS = 60.0

RETRO_CSS = b'''
window.office-window {
    background: #c0c0c0;
    border: 2px solid #7f7f7f;
}
#office-root { background: #c0c0c0; }
#title-bar { background: #008080; color: white; padding: 3px 6px; }
#title-bar label { color: white; font-weight: 700; }
.win95-button {
    background: #c0c0c0;
    color: black;
    border-top: 2px solid #ffffff;
    border-left: 2px solid #ffffff;
    border-right: 2px solid #404040;
    border-bottom: 2px solid #404040;
    border-radius: 0;
    padding: 4px 8px;
    box-shadow: none;
}
.win95-button:active {
    border-top: 2px solid #404040;
    border-left: 2px solid #404040;
    border-right: 2px solid #ffffff;
    border-bottom: 2px solid #ffffff;
}
#panel-sunken {
    background: #c0c0c0;
    border-top: 2px solid #808080;
    border-left: 2px solid #808080;
    border-right: 2px solid #ffffff;
    border-bottom: 2px solid #ffffff;
    padding: 6px;
}
#gallery-preview {
    background: white;
    border-top: 2px solid #808080;
    border-left: 2px solid #808080;
    border-right: 2px solid #ffffff;
    border-bottom: 2px solid #ffffff;
}
#retro-notebook header,
#retro-notebook tabs,
#retro-notebook tab,
#retro-notebook tabbox {
    margin: 0;
    padding: 0;
}
#retro-notebook tab {
    background: #c0c0c0;
    border-top: 2px solid #ffffff;
    border-left: 2px solid #ffffff;
    border-right: 2px solid #808080;
    border-bottom: none;
    border-radius: 0;
    padding: 2px 10px;
    margin-right: 1px;
    min-height: 0;
}
#retro-notebook tab:checked {
    background: #c0c0c0;
    margin-bottom: -1px;
}
#retro-notebook > stack {
    background: #c0c0c0;
    border-top: 2px solid #808080;
    border-left: 2px solid #808080;
    border-right: 2px solid #ffffff;
    border-bottom: 2px solid #ffffff;
}
#speech-label, #bubble-caption { color: black; }
combobox, entry {
    background: white;
    color: black;
    border-top: 2px solid #808080;
    border-left: 2px solid #808080;
    border-right: 2px solid #ffffff;
    border-bottom: 2px solid #ffffff;
    border-radius: 0;
}
'''

AGENT_LIBRARY = [
    {'id': 'clippy', 'folder': 'clippy', 'name': 'Clippit', 'description': 'The classic paperclip assistant who keeps everything together.'},
    {'id': 'dog', 'folder': 'rover', 'name': 'Dog', 'description': 'A cheerful dog assistant who reacts with playful curiosity.'},
    {'id': 'merlin', 'folder': 'merlin', 'name': 'Merlin', 'description': 'A wizard assistant for moments that need a little magic.'},
    {'id': 'genius', 'folder': 'genius', 'name': 'Genius', 'description': 'A scholarly helper with a thoughtful, bookish look.'},
]

EVENT_MESSAGES = {
    'created_dir': ['A new folder appeared.', 'You created a new folder.'],
    'created_file': ['A new file just showed up.', 'A fresh file was created.'],
    'modified': ['Something was updated.', 'That file changed just now.'],
    'deleted': ['Something disappeared.', 'That file was removed.'],
    'moved': ['I saw a file move.', 'That item changed its location.'],
    'opened': ['That file was opened recently.', 'I noticed a recently opened file.'],
    'idle': ["I'm keeping an eye on your files.", "Nothing new yet. I'm still here."],
}

EVENT_ANIMATIONS_DEFAULT = {
    'created_dir': ['GetAttention', 'Searching', 'Explain'],
    'created_file': ['Writing', 'GetAttention', 'Save'],
    'modified': ['Processing', 'Writing', 'Thinking'],
    'deleted': ['EmptyTrash', 'GetArtsy', 'LookDown'],
    'moved': ['Searching', 'GestureLeft', 'GestureRight'],
    'opened': ['Greeting', 'GetAttention', 'Explain'],
    'idle': ['Idle1_1', 'IdleAtom', 'LookUp', 'LookRight', 'RestPose'],
}


@dataclass(slots=True)
class AgentMeta:
    agent_id: str
    folder: str
    name: str
    description: str

    @property
    def path(self) -> Path:
        return AGENTS_DIR / self.folder


class SoundPlayer:
    def __init__(self) -> None:
        self.canberra = shutil.which('canberra-gtk-play')
        self.paplay = shutil.which('paplay')
        self.aplay = shutil.which('aplay')
        self.sounds_dir: Path | None = None

    def set_agent_dir(self, sounds_dir: Path) -> None:
        self.sounds_dir = sounds_dir

    def play_sound_id(self, sound_id: str | int | None) -> None:
        if sound_id is None or self.sounds_dir is None:
            return
        for ext in ('.ogg', '.oga', '.wav', '.mp3'):
            path = self.sounds_dir / f'{sound_id}{ext}'
            if path.exists():
                self._spawn(path)
                return

    def _spawn(self, sound_path: Path) -> None:
        cmd = None
        if self.paplay and sound_path.suffix.lower() in {'.ogg', '.oga', '.wav'}:
            cmd = [self.paplay, str(sound_path)]
        elif self.aplay and sound_path.suffix.lower() == '.wav':
            cmd = [self.aplay, str(sound_path)]
        elif self.canberra:
            cmd = [self.canberra, '-f', str(sound_path)]
        if cmd:
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass


class AgentData:
    def __init__(self, json_path: Path, map_path: Path) -> None:
        self.data = json.loads(json_path.read_text())
        self.animations: dict[str, dict[str, Any]] = self.data['animations']
        self.frame_width, self.frame_height = self.data['framesize']
        self.sprite = GdkPixbuf.Pixbuf.new_from_file(str(map_path))

    def get_frame_pixbuf(self, x: int, y: int, scale: float) -> GdkPixbuf.Pixbuf:
        sub = self.sprite.new_subpixbuf(x, y, self.frame_width, self.frame_height)
        if scale == 1.0:
            return sub
        return sub.scale_simple(int(self.frame_width * scale), int(self.frame_height * scale), GdkPixbuf.InterpType.BILINEAR)

    def get_preview_pixbuf(self, scale: float = 1.2) -> GdkPixbuf.Pixbuf:
        rest = self.animations.get('RestPose') or next(iter(self.animations.values()))
        frame = (rest.get('frames') or [{'images': [[0, 0]]}])[0]
        x, y = (frame.get('images') or [[0, 0]])[0]
        return self.get_frame_pixbuf(x, y, scale)

    def has_animation(self, name: str) -> bool:
        return name in self.animations

    def list_animations(self) -> list[str]:
        return sorted(self.animations.keys())


class SpriteAnimator:
    def __init__(self, image: Gtk.Image, on_done, on_sound) -> None:
        self.image = image
        self.on_done = on_done
        self.on_sound = on_sound
        self.agent: AgentData | None = None
        self.timer_id: int | None = None
        self.active_animation = 'RestPose'
        self.active_frames: list[dict[str, Any]] = []
        self.frame_index = 0
        self.scale = WINDOW_SCALE

    def set_agent(self, agent: AgentData) -> None:
        self.agent = agent
        self.set_animation('RestPose')

    def cancel(self) -> None:
        if self.timer_id is not None:
            GLib.source_remove(self.timer_id)
            self.timer_id = None

    def set_animation(self, name: str) -> None:
        if self.agent is None:
            return
        self.cancel()
        if not self.agent.has_animation(name):
            name = 'RestPose' if self.agent.has_animation('RestPose') else self.agent.list_animations()[0]
        self.active_animation = name
        self.active_frames = self.agent.animations[name].get('frames', [])
        self.frame_index = 0
        self._show_current_frame()

    def _show_current_frame(self) -> None:
        if not self.agent or not self.active_frames:
            return
        frame = self.active_frames[self.frame_index]
        if 'sound' in frame:
            self.on_sound(frame.get('sound'))
        x, y = (frame.get('images') or [[0, 0]])[0]
        self.image.set_from_pixbuf(self.agent.get_frame_pixbuf(x, y, self.scale))
        self.timer_id = GLib.timeout_add(max(int(frame.get('duration', 100)), 30), self._advance)

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
        self.callback('created_dir' if event.is_directory else 'created_file', event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.callback('modified', event.src_path)

    def on_deleted(self, event):
        self.callback('deleted', event.src_path)

    def on_moved(self, event):
        self.callback('moved', getattr(event, 'dest_path', event.src_path))


class OfficeBubble(Gtk.Overlay):
    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(360, 128)
        self.body_margin = 6.0
        self.tail_height = 24.0
        self.tail_width = 34.0
        self.tail_offset = 58.0

        bg = Gtk.DrawingArea()
        bg.connect('draw', self._on_draw)
        self.add(bg)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_margin_top(16)
        content.set_margin_bottom(36)
        content.set_margin_start(24)
        content.set_margin_end(28)
        self.add_overlay(content)

        self.label = Gtk.Label(label='')
        self.label.set_name('speech-label')
        self.label.set_xalign(0.0)
        self.label.set_yalign(0.0)
        self.label.set_line_wrap(True)
        self.label.set_max_width_chars(38)
        content.pack_start(self.label, True, True, 0)

    def set_text(self, text: str) -> None:
        self.label.set_text(text)

    def _on_draw(self, _widget, cr):
        a = self.get_allocation()
        x = self.body_margin
        y = self.body_margin
        w = float(a.width) - self.body_margin * 2
        h = float(a.height) - self.tail_height - self.body_margin
        r = 5.0
        tail_center = max(x + 30.0, min(x + w - 30.0, x + self.tail_offset))
        tail_left = tail_center - self.tail_width / 2.0
        tail_right = tail_center + self.tail_width / 2.0
        tail_tip_x = tail_center - 1.0
        tail_tip_y = y + h + self.tail_height
        self._bubble_path(cr, x, y, w, h, r, tail_left, tail_right, tail_tip_x, tail_tip_y)
        cr.set_source_rgb(0.937, 0.917, 0.745)
        cr.fill_preserve()
        cr.set_source_rgb(0.0, 0.0, 0.0)
        cr.set_line_width(2.0)
        cr.stroke()
        return False

    @staticmethod
    def _bubble_path(cr, x, y, w, h, r, tail_left, tail_right, tail_tip_x, tail_tip_y):
        cr.new_path()
        cr.move_to(x + r, y)
        cr.line_to(x + w - r, y)
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.line_to(x + w, y + h - r)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.line_to(tail_right, y + h)
        cr.line_to(tail_tip_x, tail_tip_y)
        cr.line_to(tail_left, y + h)
        cr.line_to(x + r, y + h)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.line_to(x, y + r)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()


class OfficeActionsWindow(Gtk.Window):
    def __init__(self, owner: 'AssistantWindow') -> None:
        super().__init__(title='Office Assistant')
        self.owner = owner
        self.preview_index = owner.current_agent_index
        self.preview_agents = owner.agent_metas
        self.preview_cache: dict[str, AgentData] = {}
        self.set_default_size(780, 520)
        self.set_resizable(False)
        self.set_decorated(False)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.connect('button-press-event', self._on_drag)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.set_name('office-root')
        self.add(root)

        titlebar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        titlebar.set_name('title-bar')
        titlebar.set_margin_bottom(4)
        root.pack_start(titlebar, False, False, 0)

        title = Gtk.Label(label='Office Assistant')
        title.set_xalign(0.0)
        titlebar.pack_start(title, True, True, 6)

        help_btn = self._win95_button('?')
        help_btn.set_size_request(28, 24)
        help_btn.connect('clicked', lambda *_: self.owner.set_speech('Tip: pick an assistant in Gallery, then click OK.'))
        titlebar.pack_start(help_btn, False, False, 0)

        close_btn = self._win95_button('×')
        close_btn.set_size_request(28, 24)
        close_btn.connect('clicked', lambda *_: self.hide())
        titlebar.pack_start(close_btn, False, False, 4)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_start(10)
        outer.set_margin_end(10)
        outer.set_margin_top(6)
        outer.set_margin_bottom(10)
        root.pack_start(outer, True, True, 0)

        notebook = Gtk.Notebook()
        notebook.set_name('retro-notebook')
        notebook.set_scrollable(False)
        outer.pack_start(notebook, True, True, 0)

        notebook.append_page(self._build_actions_page(), Gtk.Label(label='Actions'))
        notebook.append_page(self._build_gallery_page(), Gtk.Label(label='Gallery'))

        bottom = Gtk.Box(spacing=8)
        outer.pack_end(bottom, False, False, 0)
        bottom.pack_start(Gtk.Box(), True, True, 0)

        ok_btn = self._win95_button('OK')
        ok_btn.set_size_request(120, 34)
        ok_btn.connect('clicked', self._on_ok)
        bottom.pack_start(ok_btn, False, False, 0)

        cancel_btn = self._win95_button('Cancel')
        cancel_btn.set_size_request(120, 34)
        cancel_btn.connect('clicked', lambda *_: self.hide())
        bottom.pack_start(cancel_btn, False, False, 0)

        self._refresh_gallery()
        self.show_all()
        self.hide()

    def _build_actions_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(10); box.set_margin_end(10); box.set_margin_top(10); box.set_margin_bottom(10)
        desc = Gtk.Label(label='Use the buttons below to make your assistant react right away. You can also pick a specific animation.')
        desc.set_xalign(0.0); desc.set_line_wrap(True)
        box.pack_start(desc, False, False, 0)

        panel = Gtk.Frame(); panel.set_name('panel-sunken'); box.pack_start(panel, True, True, 0)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        inner.set_margin_start(10); inner.set_margin_end(10); inner.set_margin_top(10); inner.set_margin_bottom(10)
        panel.add(inner)

        grid = Gtk.Grid(column_spacing=10, row_spacing=10)
        inner.pack_start(grid, False, False, 0)
        manual_actions = [('New file', 'created_file'), ('New folder', 'created_dir'), ('Modified', 'modified'), ('Deleted', 'deleted'), ('Moved', 'moved'), ('Opened', 'opened'), ('Idle', 'idle')]
        for idx, (label, event_type) in enumerate(manual_actions):
            btn = self._win95_button(label)
            btn.set_size_request(150, 34)
            btn.connect('clicked', lambda *_args, ev=event_type: self.owner.manual_event(ev))
            grid.attach(btn, idx % 2, idx // 2, 1, 1)

        anim_row = Gtk.Box(spacing=8); inner.pack_start(anim_row, False, False, 0)
        self.anim_combo = Gtk.ComboBoxText(); self._reload_animation_combo(); anim_row.pack_start(self.anim_combo, True, True, 0)
        play_btn = self._win95_button('Play'); play_btn.set_size_request(80, 32); play_btn.connect('clicked', self._on_play_animation); anim_row.pack_start(play_btn, False, False, 0)
        rest_btn = self._win95_button('RestPose'); rest_btn.set_size_request(120, 32); rest_btn.connect('clicked', lambda *_: self.owner.play_named_animation('RestPose')); anim_row.pack_start(rest_btn, False, False, 0)
        return box

    def _build_gallery_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(10); box.set_margin_end(10); box.set_margin_top(10); box.set_margin_bottom(10)
        desc = Gtk.Label(label='You can scroll through the different assistants by using the <Back and Next> buttons. When you are finished selecting your assistant, click the OK button.')
        desc.set_xalign(0.0); desc.set_line_wrap(True)
        box.pack_start(desc, False, False, 0)

        panel = Gtk.Frame(); panel.set_name('panel-sunken'); box.pack_start(panel, True, True, 0)
        panel_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        panel_box.set_margin_start(12); panel_box.set_margin_end(12); panel_box.set_margin_top(12); panel_box.set_margin_bottom(12)
        panel.add(panel_box)

        self.preview_frame = Gtk.Frame(); self.preview_frame.set_name('gallery-preview'); self.preview_frame.set_size_request(200, 180); panel_box.pack_start(self.preview_frame, False, False, 0)
        preview_box = Gtk.Box(); preview_box.set_halign(Gtk.Align.CENTER); preview_box.set_valign(Gtk.Align.CENTER); self.preview_frame.add(preview_box)
        self.preview_image = Gtk.Image(); preview_box.pack_start(self.preview_image, False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8); panel_box.pack_start(right, True, True, 0)
        self.preview_bubble = OfficeBubble(); self.preview_bubble.set_size_request(360, 140); right.pack_start(self.preview_bubble, False, False, 0)
        self.preview_name = Gtk.Label(); self.preview_name.set_xalign(0.0); self.preview_name.set_name('bubble-caption'); right.pack_start(self.preview_name, False, False, 0)
        self.preview_desc = Gtk.Label(); self.preview_desc.set_xalign(0.0); self.preview_desc.set_line_wrap(True); right.pack_start(self.preview_desc, False, False, 0)

        nav = Gtk.Box(spacing=10); box.pack_start(nav, False, False, 0)
        back_btn = self._win95_button('< Back'); back_btn.set_size_request(110, 34); back_btn.connect('clicked', lambda *_: self._step_gallery(-1)); nav.pack_start(back_btn, False, False, 0)
        next_btn = self._win95_button('Next >'); next_btn.set_size_request(110, 34); next_btn.connect('clicked', lambda *_: self._step_gallery(1)); nav.pack_start(next_btn, False, False, 0)
        nav.pack_start(Gtk.Box(), True, True, 0)
        return box

    def _reload_animation_combo(self) -> None:
        self.anim_combo.remove_all()
        for name in self.owner.agent_data.list_animations():
            self.anim_combo.append_text(name)
        self.anim_combo.set_active(0)

    def _step_gallery(self, step: int) -> None:
        self.preview_index = (self.preview_index + step) % len(self.preview_agents)
        self._refresh_gallery()

    def _refresh_gallery(self) -> None:
        meta = self.preview_agents[self.preview_index]
        data = self.preview_cache.get(meta.agent_id)
        if data is None:
            data = AgentData(meta.path / 'agent.json', meta.path / 'map.png')
            self.preview_cache[meta.agent_id] = data
        self.preview_image.set_from_pixbuf(data.get_preview_pixbuf(1.3))
        self.preview_bubble.set_text(self._gallery_quote(meta.agent_id))
        self.preview_name.set_text(f'Name:      {meta.name}')
        self.preview_desc.set_text(meta.description)

    def _gallery_quote(self, agent_id: str) -> str:
        return {
            'clippy': "How's life? All work and no play?",
            'dog': 'Want me to sniff around your files?',
            'merlin': 'A little wizardry can brighten any workflow.',
            'genius': 'Let us approach this with a truly brilliant plan.',
        }.get(agent_id, 'Choose the assistant you would like to use.')

    def _on_play_animation(self, *_args) -> None:
        text = self.anim_combo.get_active_text()
        if text:
            self.owner.play_named_animation(text)

    def _on_ok(self, *_args) -> None:
        self.owner.set_agent_by_index(self.preview_index)
        self._reload_animation_combo()
        self.hide()

    def present_for(self, owner: 'AssistantWindow') -> None:
        self.preview_index = owner.current_agent_index
        self._refresh_gallery()
        self._reload_animation_combo()
        self.show_all()
        self.present()

    def _win95_button(self, label: str) -> Gtk.Button:
        btn = Gtk.Button(label=label)
        btn.get_style_context().add_class('win95-button')
        return btn

    def _on_drag(self, _widget, event):
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS:
            try:
                self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            except Exception:
                pass
        return False


class AssistantWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title=APP_NAME)
        self.set_default_size(420, 360)
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

        self.connect('draw', self._on_window_draw)
        self.connect('destroy', self._on_destroy)
        self.connect('button-press-event', self._on_button_press)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)

        self.agent_metas = [AgentMeta(item['id'], item['folder'], item['name'], item['description']) for item in AGENT_LIBRARY]
        self.current_agent_index = 0
        self.agent_data = self._load_agent_data(self.agent_metas[self.current_agent_index])
        self.sound_player = SoundPlayer(); self.sound_player.set_agent_dir(self.agent_metas[self.current_agent_index].path / 'sounds')
        self.actions_window = OfficeActionsWindow(self)
        self.queue: deque[tuple[str, str]] = deque()
        self.is_busy = False
        self.last_idle = time.monotonic()
        self.last_recent_uri: str | None = None
        self.last_recent_seen = 0.0
        self.observer: Observer | None = None

        root = Gtk.Overlay(); self.add(root)
        drag_box = Gtk.EventBox(); drag_box.set_visible_window(False); drag_box.add_events(Gdk.EventMask.BUTTON_PRESS_MASK); drag_box.connect('button-press-event', self._on_button_press); root.add(drag_box)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        layout.set_margin_top(8); layout.set_margin_bottom(8); layout.set_margin_start(8); layout.set_margin_end(8)
        drag_box.add(layout)
        self.top_bubble = OfficeBubble(); self.top_bubble.set_halign(Gtk.Align.CENTER); layout.pack_start(self.top_bubble, False, False, 0)
        self.image = Gtk.Image(); self.image.set_halign(Gtk.Align.CENTER); self.image.set_valign(Gtk.Align.CENTER); layout.pack_start(self.image, True, True, 0)

        self.animator = SpriteAnimator(self.image, self._on_animation_finished, self.sound_player.play_sound_id)
        self.animator.set_agent(self.agent_data)
        self.show_all()
        self._set_intro_speech()
        self._start_watchdog(); self._start_recent_monitor(); GLib.timeout_add_seconds(2, self._idle_tick)
        self.enqueue('opened', 'Application started')

    def _load_agent_data(self, meta: AgentMeta) -> AgentData:
        return AgentData(meta.path / 'agent.json', meta.path / 'map.png')

    def set_agent_by_index(self, index: int) -> None:
        self.current_agent_index = index
        meta = self.agent_metas[index]
        self.agent_data = self._load_agent_data(meta)
        self.sound_player.set_agent_dir(meta.path / 'sounds')
        self.animator.set_agent(self.agent_data)
        self.set_speech(f'{meta.name} is now ready to help you.')

    def set_speech(self, text: str) -> None:
        self.top_bubble.set_text(clamp_text(text, 160))

    def _set_intro_speech(self) -> None:
        self.set_speech(f'{self.agent_metas[self.current_agent_index].name} is watching your files.')

    def manual_event(self, event_type: str) -> None:
        self.enqueue(event_type, f'Manual {event_type}')

    def play_named_animation(self, name: str) -> None:
        self.queue.clear(); self.is_busy = True; self.last_idle = time.monotonic(); self.set_speech(f'Playing animation: {name}'); self.animator.set_animation(name)

    def _on_window_draw(self, _widget, cr):
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.0)
        cr.set_operator(1)
        cr.paint()
        cr.set_operator(2)
        return False

    def _on_button_press(self, _widget, event):
        if event.button != 1:
            return False
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            self.actions_window.present_for(self)
            return True
        if event.type == Gdk.EventType.BUTTON_PRESS:
            try:
                self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            except Exception:
                pass
        return False

    def _on_destroy(self, *_args) -> None:
        if self.observer is not None:
            self.observer.stop(); self.observer.join(timeout=2)
        self.actions_window.destroy()
        Gtk.main_quit()

    def _start_watchdog(self) -> None:
        existing = [path for path in WATCH_DIRS if path.exists()]
        if not existing:
            self.set_speech('No Desktop, Downloads, or Documents folders were found.')
            return
        handler = EventBridge(self._watchdog_callback)
        observer = Observer()
        for path in existing:
            observer.schedule(handler, str(path), recursive=True)
        observer.daemon = True; observer.start(); self.observer = observer

    def _watchdog_callback(self, event_type: str, path: str) -> None:
        GLib.idle_add(self.enqueue, event_type, path)

    def _start_recent_monitor(self) -> None:
        self.recent_manager = Gtk.RecentManager.get_default()
        self.recent_manager.connect('changed', self._on_recent_changed)

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
        self.last_recent_uri = uri; self.last_recent_seen = now
        self.enqueue('opened', item.get_display_name() or uri)

    def enqueue(self, event_type: str, payload: str) -> bool:
        self.queue.append((event_type, payload)); self._try_start_next(); return False

    def _available_event_animations(self, event_type: str) -> list[str]:
        preferred = EVENT_ANIMATIONS_DEFAULT.get(event_type, ['RestPose'])
        available = [name for name in preferred if self.agent_data.has_animation(name)]
        return available or ['RestPose']

    def _try_start_next(self) -> None:
        if self.is_busy or not self.queue:
            return
        event_type, payload = self.queue.popleft()
        anim = random.choice(self._available_event_animations(event_type))
        message = random.choice(EVENT_MESSAGES.get(event_type, ['Something happened.']))
        name = Path(payload).name if payload else payload
        if name:
            message = f'{message}\n{name}'
        self.set_speech(message)
        self.is_busy = True; self.last_idle = time.monotonic(); self.animator.set_animation(anim)

    def _on_animation_finished(self, _animation_name: str) -> None:
        self.is_busy = False
        if self.agent_data.has_animation('RestPose'):
            self.animator.set_animation('RestPose')
        GLib.timeout_add(50, self._continue_queue)

    def _continue_queue(self) -> bool:
        self._try_start_next(); return False

    def _idle_tick(self) -> bool:
        if not self.is_busy and not self.queue and (time.monotonic() - self.last_idle) >= IDLE_SECONDS:
            self.enqueue('idle', '')
        return True


def clamp_text(text: str, limit: int = 120) -> str:
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + '…'


def install_css() -> None:
    provider = Gtk.CssProvider(); provider.load_from_data(RETRO_CSS)
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def main() -> None:
    install_css()
    win = AssistantWindow()
    win.get_style_context().add_class('office-window')
    win.present()
    Gtk.main()


if __name__ == '__main__':
    main()
