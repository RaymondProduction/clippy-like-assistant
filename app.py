#!/usr/bin/env python3
from __future__ import annotations

import fnmatch
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

try:
    gi.require_version('AyatanaAppIndicator3', '0.1')
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
except (ValueError, ImportError):
    AppIndicator3 = None

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

APP_NAME = 'Linux Assistant'
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / 'assets'
AGENTS_DIR = ASSETS_DIR / 'agents'
PROFILES_DIR = BASE_DIR / 'profiles'
WATCH_DIRS = [Path.home() / name for name in ('Desktop', 'Downloads', 'Documents')]
WINDOW_SCALE = 1.55
IDLE_SECONDS = 10.0
MAX_RECENT_AGE_SECONDS = 60.0
SUPPORTED_ARCHIVE_EXTENSIONS = {
    '.zip', '.rar', '.7z', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.tar.gz', '.tar.bz2', '.tar.xz', '.zst', '.tar.zst',
}
DEFAULT_PROFILE_FILENAME = 'default.json'

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
combobox, entry, spinbutton, textview, treeview {
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
    {'id': 'f1', 'folder': 'f1', 'name': 'F1', 'description': 'A confident office agent with a brisk, energetic style.'},
    {'id': 'links', 'folder': 'links', 'name': 'Links', 'description': 'A laid-back office helper dog with classic Microsoft charm.'},
    {'id': 'rocky', 'folder': 'rocky', 'name': 'Roky', 'description': 'A playful rocky assistant with bold expressions and lots of motion.'},
]

PROFILE_TEMPLATE = {
    'id': 'default',
    'name': 'Default',
    'version': 1,
    'description': 'Default behavior profile for Linux Assistant.',
    'settings': {
        'idle_every_seconds': 600,
        'global_min_gap_seconds': 12,
        'dedupe_window_seconds': 6,
        'randomize_weighted': True,
    },
    'fallback_idle_animations': ['LookDown', 'LookLeft', 'LookRight', 'Idle1_1', 'IdleAtom', 'RestPose'],
    'signals': {
        'ProgramStart': {
            'enabled': True,
            'cooldown_seconds': 1,
            'animations': [{'name': 'Greeting', 'weight': 100}],
            'speech': ['Hello there.', 'Ready to help.'],
        },
        'ProgramExit': {
            'enabled': True,
            'cooldown_seconds': 1,
            'animations': [{'name': 'GoodBye', 'weight': 100}],
            'speech': ['Goodbye.', 'See you next time.'],
        },
        'TypingBurst': {
            'enabled': True,
            'cooldown_seconds': 45,
            'animations': [{'name': 'Writing', 'weight': 60}, {'name': 'CheckingSomething', 'weight': 40}],
            'speech': ['You seem busy typing.', 'Working on something?', 'That looks like a lot of text.'],
        },
        'CompileStarted': {
            'enabled': True,
            'cooldown_seconds': 60,
            'animations': [{'name': 'CheckingSomething', 'weight': 70}, {'name': 'Writing', 'weight': 30}],
            'speech': ['Compiling… let\'s see how it goes.', 'Building project.'],
        },
        'CompileFailed': {
            'enabled': True,
            'cooldown_seconds': 20,
            'animations': [{'name': 'Wave', 'weight': 100}],
            'speech': ['Something went wrong.', 'That build did not finish cleanly.'],
        },
        'FileSaved': {
            'enabled': True,
            'cooldown_seconds': 20,
            'animations': [{'name': 'Save', 'weight': 50}, {'name': 'Congratulate', 'weight': 50}],
            'speech': ['Saved.', 'Nice, your work is stored.'],
            'filters': {
                'ignore_temp_files': True,
                'ignore_hidden_files': True,
                'debounce_seconds': 6,
                'ignore_patterns': ['*.tmp', '*.temp', '*.swp', '*.swo', '*.bak', '*~', '.~lock.*', '.goutputstream-*', '.#*', '*.part'],
            },
        },
        'ArchiveCreated': {
            'enabled': True,
            'cooldown_seconds': 8,
            'animations': [{'name': 'Save', 'weight': 100}],
            'speech': ['Archive created.', 'Packed and saved.'],
        },
        'TrashEmptied': {
            'enabled': True,
            'cooldown_seconds': 20,
            'animations': [{'name': 'EmptyTrash', 'weight': 100}],
            'speech': ['Trash is empty now.'],
        },
        'LargeDeleteInEditor': {
            'enabled': True,
            'cooldown_seconds': 30,
            'animations': [{'name': 'EmptyTrash', 'weight': 100}],
            'speech': ['That was a big cleanup.'],
        },
        'AudioPlayingLong': {
            'enabled': True,
            'cooldown_seconds': 180,
            'animations': [{'name': 'Hearing_1', 'weight': 100}],
            'speech': ['You are listening to something.', 'Music time?'],
        },
        'PrintJobStarted': {
            'enabled': True,
            'cooldown_seconds': 30,
            'animations': [{'name': 'Print', 'weight': 100}],
            'speech': ['Printing started.'],
        },
        'SystemSearch': {
            'enabled': True,
            'cooldown_seconds': 20,
            'animations': [{'name': 'Searching', 'weight': 100}],
            'speech': ['Looking for something?'],
        },
        'MessageSent': {
            'enabled': True,
            'cooldown_seconds': 20,
            'animations': [{'name': 'SendMail', 'weight': 100}],
            'speech': ['Message sent.'],
        },
        'WaveError': {
            'enabled': True,
            'cooldown_seconds': 20,
            'animations': [{'name': 'Wave', 'weight': 100}],
            'speech': ['Something looks wrong.'],
        },
        'IdlePulse': {
            'enabled': True,
            'cooldown_seconds': 600,
            'animations': [
                {'name': 'LookLeft', 'weight': 20},
                {'name': 'LookRight', 'weight': 20},
                {'name': 'LookDown', 'weight': 20},
                {'name': 'IdleAtom', 'weight': 20},
                {'name': 'RestPose', 'weight': 20},
            ],
            'speech': [],
        },
        'FolderCreated': {
            'enabled': True,
            'cooldown_seconds': 10,
            'animations': [{'name': 'Searching', 'weight': 50}, {'name': 'GetAttention', 'weight': 50}],
            'speech': ['A new folder appeared.', 'You created a new folder.'],
        },
        'FileOpened': {
            'enabled': True,
            'cooldown_seconds': 15,
            'animations': [{'name': 'Greeting', 'weight': 50}, {'name': 'Explain', 'weight': 50}],
            'speech': ['That file was opened recently.', 'I noticed a recently opened file.'],
        },
    },
}

MANUAL_SIGNALS = [
    'ProgramStart', 'ProgramExit', 'TypingBurst', 'CompileStarted', 'CompileFailed', 'FileSaved', 'ArchiveCreated',
    'TrashEmptied', 'LargeDeleteInEditor', 'AudioPlayingLong', 'PrintJobStarted', 'SystemSearch', 'MessageSent', 'WaveError', 'IdlePulse',
]


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
        self.global_sounds_dir = ASSETS_DIR / 'sounds' / 'clippy'

    def set_agent_dir(self, sounds_dir: Path) -> None:
        self.sounds_dir = sounds_dir if sounds_dir.exists() else None

    def play_sound_id(self, sound_id: str | int | None) -> None:
        if sound_id is None:
            return
        for sounds_dir in (self.sounds_dir, self.global_sounds_dir):
            if sounds_dir is None:
                continue
            for ext in ('.ogg', '.oga', '.wav', '.mp3'):
                path = sounds_dir / f'{sound_id}{ext}'
                if path.exists():
                    self._spawn(path)
                    return

    def _spawn(self, sound_path: Path) -> None:
        cmd: list[str] | None = None
        suffix = sound_path.suffix.lower()
        if self.paplay and suffix in {'.ogg', '.oga', '.wav'}:
            cmd = [self.paplay, str(sound_path)]
        elif self.aplay and suffix == '.wav':
            cmd = [self.aplay, str(sound_path)]
        elif self.canberra:
            cmd = [self.canberra, '-f', str(sound_path)]
        if cmd is None:
            return
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


class AgentData:
    def __init__(self, json_path: Path, map_path: Path) -> None:
        self.data = json.loads(json_path.read_text(encoding='utf-8'))
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


class BehaviorProfile:
    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self.data = data
        self._last_signal_ts: dict[str, float] = {}
        self._dedupe_ts: dict[str, float] = {}

    @property
    def name(self) -> str:
        return str(self.data.get('name', self.path.stem))

    @property
    def settings(self) -> dict[str, Any]:
        return dict(self.data.get('settings', {}))

    def signal_names(self) -> list[str]:
        return sorted(self.data.get('signals', {}).keys())

    def get_signal(self, signal_name: str) -> dict[str, Any]:
        return dict(self.data.get('signals', {}).get(signal_name, {}))

    def update_signal(self, signal_name: str, new_data: dict[str, Any]) -> None:
        self.data.setdefault('signals', {})[signal_name] = new_data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    def cooldown_for(self, signal_name: str) -> float:
        signal = self.data.get('signals', {}).get(signal_name, {})
        return float(signal.get('cooldown_seconds', 0))

    def choose_reaction(self, signal_name: str, context: dict[str, Any] | None = None) -> tuple[str | None, str | None]:
        context = context or {}
        signal = self.data.get('signals', {}).get(signal_name)
        if not signal or not signal.get('enabled', True):
            return None, None

        now = time.monotonic()
        last_fired = self._last_signal_ts.get(signal_name, 0.0)
        if now - last_fired < float(signal.get('cooldown_seconds', 0)):
            return None, None

        filters = signal.get('filters', {}) or {}
        dedupe_window = float(filters.get('debounce_seconds', self.settings.get('dedupe_window_seconds', 0)))
        dedupe_key = context.get('dedupe_key')
        if dedupe_key and dedupe_window > 0:
            last_seen = self._dedupe_ts.get(dedupe_key, 0.0)
            if now - last_seen < dedupe_window:
                return None, None

        options = [opt for opt in signal.get('animations', []) if opt.get('name')]
        if not options:
            return None, self._choose_speech(signal)

        names = [str(opt['name']) for opt in options]
        weights = [max(int(opt.get('weight', 1)), 1) for opt in options]
        animation = random.choices(names, weights=weights, k=1)[0]

        self._last_signal_ts[signal_name] = now
        if dedupe_key:
            self._dedupe_ts[dedupe_key] = now
        return animation, self._choose_speech(signal)

    def _choose_speech(self, signal: dict[str, Any]) -> str | None:
        speech_items = signal.get('speech', []) or []
        if not speech_items:
            return None
        return str(random.choice(speech_items))


class ProfileStore:
    def __init__(self, profiles_dir: Path) -> None:
        self.profiles_dir = profiles_dir
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.ensure_default_profile()

    def ensure_default_profile(self) -> None:
        default_path = self.profiles_dir / DEFAULT_PROFILE_FILENAME
        if not default_path.exists():
            default_path.write_text(json.dumps(PROFILE_TEMPLATE, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    def list_profiles(self) -> list[Path]:
        return sorted(self.profiles_dir.glob('*.json'))

    def load(self, path: Path | None = None) -> BehaviorProfile:
        profile_path = path or (self.profiles_dir / DEFAULT_PROFILE_FILENAME)
        return BehaviorProfile(profile_path, json.loads(profile_path.read_text(encoding='utf-8')))


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
        super().__init__(title='Linux Assistant')
        self.owner = owner
        self.gallery_index = owner.current_agent_index
        self.set_default_size(820, 560)
        self.set_resizable(False)
        self.set_decorated(False)
        self.set_transient_for(owner)
        self.set_modal(False)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.get_style_context().add_class('office-window')

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.set_name('office-root')
        self.add(root)

        title_bar_event = Gtk.EventBox()
        title_bar_event.set_visible_window(False)
        title_bar_event.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        title_bar_event.connect('button-press-event', self._on_titlebar_button_press)
        root.pack_start(title_bar_event, False, False, 0)

        title_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        title_bar.set_name('title-bar')
        title_bar.set_margin_bottom(10)
        title_bar_event.add(title_bar)

        title_lbl = Gtk.Label(label='Linux Assistant')
        title_lbl.set_xalign(0.0)
        title_bar.pack_start(title_lbl, True, True, 8)

        close_btn = self._win95_button(None, markup='<b>×</b>')
        close_btn.set_size_request(32, 26)
        close_btn.connect('clicked', lambda *_: self.destroy())
        title_bar.pack_end(close_btn, False, False, 0)

        help_btn = self._win95_button('?')
        help_btn.set_size_request(32, 26)
        help_btn.connect('clicked', self._on_help)
        title_bar.pack_end(help_btn, False, False, 0)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_start(14)
        outer.set_margin_end(14)
        outer.set_margin_top(0)
        outer.set_margin_bottom(12)
        root.pack_start(outer, True, True, 0)

        notebook = Gtk.Notebook()
        notebook.set_name('retro-notebook')
        notebook.set_scrollable(False)
        outer.pack_start(notebook, True, True, 0)

        notebook.append_page(self._build_actions_page(), Gtk.Label(label='Actions'))
        notebook.append_page(self._build_gallery_page(), Gtk.Label(label='Gallery'))
        notebook.append_page(self._build_behavior_page(), Gtk.Label(label='Behavior'))

        bottom = Gtk.Box(spacing=8)
        outer.pack_end(bottom, False, False, 0)

        ok_btn = self._win95_button('OK')
        ok_btn.set_size_request(120, 34)
        ok_btn.connect('clicked', self._on_ok)
        bottom.pack_end(ok_btn, False, False, 0)

        cancel_btn = self._win95_button('Cancel')
        cancel_btn.set_size_request(120, 34)
        cancel_btn.connect('clicked', lambda *_: self.destroy())
        bottom.pack_end(cancel_btn, False, False, 0)

        self._refresh_gallery_preview()
        self._reload_animation_combo()
        self._reload_behavior_profile_name()
        self._reload_behavior_signal_list()

    def present_for(self, transient_for: Gtk.Window) -> None:
        self.set_transient_for(transient_for)
        self.show_all()
        self.present()

    def _on_titlebar_button_press(self, _widget, event):
        if event.button != 1:
            return False
        try:
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            return True
        except Exception:
            return False

    def _win95_button(self, label: str | None = None, markup: str | None = None) -> Gtk.Button:
        btn = Gtk.Button()
        if markup is not None:
            lbl = Gtk.Label()
            lbl.set_markup(markup)
            btn.add(lbl)
        elif label is not None:
            btn.set_label(label)
        btn.get_style_context().add_class('win95-button')
        return btn

    def _build_actions_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        desc = Gtk.Label(label='Use the buttons below to make your assistant react right away. You can also pick a specific animation or signal.')
        desc.set_xalign(0.0)
        desc.set_line_wrap(True)
        box.pack_start(desc, False, False, 0)

        panel = Gtk.Frame()
        panel.set_name('panel-sunken')
        box.pack_start(panel, True, True, 0)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        inner.set_margin_start(10)
        inner.set_margin_end(10)
        inner.set_margin_top(10)
        inner.set_margin_bottom(10)
        panel.add(inner)

        grid = Gtk.Grid(column_spacing=10, row_spacing=10)
        inner.pack_start(grid, False, False, 0)

        manual_actions = [
            ('New file', 'FileSaved'),
            ('New folder', 'FolderCreated'),
            ('Opened', 'FileOpened'),
            ('Archive', 'ArchiveCreated'),
            ('Trash', 'TrashEmptied'),
            ('Compile', 'CompileStarted'),
            ('Error', 'CompileFailed'),
            ('Idle', 'IdlePulse'),
        ]
        for idx, (label, signal_name) in enumerate(manual_actions):
            btn = self._win95_button(label)
            btn.set_size_request(150, 34)
            btn.connect('clicked', lambda *_args, sig=signal_name: self.owner.trigger_signal(sig, {'source': 'manual'}))
            grid.attach(btn, idx % 2, idx // 2, 1, 1)

        signal_row = Gtk.Box(spacing=8)
        inner.pack_start(signal_row, False, False, 0)
        self.signal_combo = Gtk.ComboBoxText()
        for signal_name in self.owner.profile.signal_names():
            self.signal_combo.append_text(signal_name)
        if self.signal_combo.get_active() == -1 and self.signal_combo.get_model() is not None:
            self.signal_combo.set_active(0)
        signal_row.pack_start(self.signal_combo, True, True, 0)
        fire_signal_btn = self._win95_button('Fire signal')
        fire_signal_btn.set_size_request(120, 32)
        fire_signal_btn.connect('clicked', self._on_fire_signal)
        signal_row.pack_start(fire_signal_btn, False, False, 0)

        anim_row = Gtk.Box(spacing=8)
        inner.pack_start(anim_row, False, False, 0)
        self.anim_combo = Gtk.ComboBoxText()
        anim_row.pack_start(self.anim_combo, True, True, 0)

        play_btn = self._win95_button('Play')
        play_btn.set_size_request(80, 32)
        play_btn.connect('clicked', self._on_play_animation)
        anim_row.pack_start(play_btn, False, False, 0)

        rest_btn = self._win95_button('RestPose')
        rest_btn.set_size_request(120, 32)
        rest_btn.connect('clicked', lambda *_: self.owner.play_named_animation('RestPose'))
        anim_row.pack_start(rest_btn, False, False, 0)
        return box

    def _build_gallery_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        desc = Gtk.Label(label='You can scroll through the different assistants by using the <Back and Next> buttons. When you are finished selecting your assistant, click the OK button.')
        desc.set_xalign(0.0)
        desc.set_line_wrap(True)
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

    def _build_behavior_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        profile_header = Gtk.Box(spacing=8)
        box.pack_start(profile_header, False, False, 0)

        self.profile_name_entry = Gtk.Entry()
        profile_header.pack_start(self.profile_name_entry, True, True, 0)

        save_profile_btn = self._win95_button('Save profile')
        save_profile_btn.connect('clicked', self._on_save_profile)
        profile_header.pack_start(save_profile_btn, False, False, 0)

        content = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        box.pack_start(content, True, True, 0)

        left_panel = Gtk.Frame(); left_panel.set_name('panel-sunken'); content.pack1(left_panel, resize=True, shrink=False)
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        left_box.set_margin_start(8); left_box.set_margin_end(8); left_box.set_margin_top(8); left_box.set_margin_bottom(8)
        left_panel.add(left_box)

        self.signal_list = Gtk.ListBox()
        self.signal_list.connect('row-selected', self._on_signal_selected)
        left_box.pack_start(self.signal_list, True, True, 0)

        right_panel = Gtk.Frame(); right_panel.set_name('panel-sunken'); content.pack2(right_panel, resize=True, shrink=False)
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right_box.set_margin_start(8); right_box.set_margin_end(8); right_box.set_margin_top(8); right_box.set_margin_bottom(8)
        right_panel.add(right_box)

        self.signal_enabled_check = Gtk.CheckButton(label='Enabled')
        right_box.pack_start(self.signal_enabled_check, False, False, 0)

        cooldown_row = Gtk.Box(spacing=8)
        right_box.pack_start(cooldown_row, False, False, 0)
        cooldown_row.pack_start(Gtk.Label(label='Cooldown (s)'), False, False, 0)
        self.cooldown_spin = Gtk.SpinButton()
        self.cooldown_spin.set_adjustment(Gtk.Adjustment(
            value=0,
            lower=0,
            upper=9999,
            step_increment=1,
            page_increment=10,
            page_size=0,
        ))
        cooldown_row.pack_start(self.cooldown_spin, False, False, 0)

        debounce_row = Gtk.Box(spacing=8)
        right_box.pack_start(debounce_row, False, False, 0)
        debounce_row.pack_start(Gtk.Label(label='Debounce (s)'), False, False, 0)
        self.debounce_spin = Gtk.SpinButton()
        self.debounce_spin.set_adjustment(Gtk.Adjustment(
            value=0,
            lower=0,
            upper=9999,
            step_increment=1,
            page_increment=10,
            page_size=0,
        ))
        debounce_row.pack_start(self.debounce_spin, False, False, 0)

        self.speech_view = Gtk.TextView()
        self.speech_view.set_wrap_mode(Gtk.WrapMode.WORD)
        speech_sw = Gtk.ScrolledWindow()
        speech_sw.set_size_request(-1, 120)
        speech_sw.add(self.speech_view)
        right_box.pack_start(Gtk.Label(label='Speech lines (one per line)', xalign=0.0), False, False, 0)
        right_box.pack_start(speech_sw, False, False, 0)

        self.animations_view = Gtk.TextView()
        self.animations_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        anim_sw = Gtk.ScrolledWindow()
        anim_sw.set_size_request(-1, 120)
        anim_sw.add(self.animations_view)
        right_box.pack_start(Gtk.Label(label='Animations: one per line as AnimationName=Weight', xalign=0.0), False, False, 0)
        right_box.pack_start(anim_sw, False, False, 0)

        signal_btn_row = Gtk.Box(spacing=8)
        right_box.pack_start(signal_btn_row, False, False, 0)
        apply_signal_btn = self._win95_button('Apply signal')
        apply_signal_btn.connect('clicked', self._on_apply_signal)
        signal_btn_row.pack_start(apply_signal_btn, False, False, 0)
        reload_signal_btn = self._win95_button('Reload signal')
        reload_signal_btn.connect('clicked', lambda *_: self._populate_signal_editor(self._selected_signal_name()))
        signal_btn_row.pack_start(reload_signal_btn, False, False, 0)
        return box

    def _reload_animation_combo(self) -> None:
        self.anim_combo.remove_all()
        for animation_name in self.owner.agent_data.list_animations():
            self.anim_combo.append_text(animation_name)
        if self.anim_combo.get_model() is not None:
            self.anim_combo.set_active(0)

    def _refresh_gallery_preview(self) -> None:
        meta = self.owner.agent_metas[self.gallery_index]
        data = self.owner._load_agent_data(meta)
        self.preview_image.set_from_pixbuf(data.get_preview_pixbuf())
        self.preview_bubble.set_text(self._gallery_quote(meta.agent_id))
        self.preview_name.set_text(f'Name:    {meta.name}')
        self.preview_desc.set_text(meta.description)

    def _gallery_quote(self, agent_id: str) -> str:
        return {
            'clippy': "How's life? All work and no play?",
            'dog': 'Want me to sniff around your files?',
            'merlin': 'A little wizardry can brighten any workflow.',
            'genius': 'Let us approach this with a truly brilliant plan.',
            'f1': 'Ready to race through your work?',
            'links': 'I can keep an eye on your files for you.',
            'rocky': 'Rock solid support, coming right up.',
        }.get(agent_id, 'Choose the assistant you would like to use.')

    def _step_gallery(self, delta: int) -> None:
        total = len(self.owner.agent_metas)
        self.gallery_index = (self.gallery_index + delta) % total
        self._refresh_gallery_preview()

    def _selected_signal_name(self) -> str | None:
        row = self.signal_list.get_selected_row()
        if row is None:
            return None
        return getattr(row, 'signal_name', None)

    def _reload_behavior_profile_name(self) -> None:
        self.profile_name_entry.set_text(self.owner.profile.name)

    def _reload_behavior_signal_list(self) -> None:
        for child in list(self.signal_list.get_children()):
            self.signal_list.remove(child)
        for signal_name in self.owner.profile.signal_names():
            row = Gtk.ListBoxRow()
            row.signal_name = signal_name
            label = Gtk.Label(label=signal_name, xalign=0.0)
            label.set_margin_start(6)
            label.set_margin_end(6)
            label.set_margin_top(4)
            label.set_margin_bottom(4)
            row.add(label)
            self.signal_list.add(row)
        self.signal_list.show_all()
        if self.signal_list.get_row_at_index(0) is not None:
            self.signal_list.select_row(self.signal_list.get_row_at_index(0))

    def _populate_signal_editor(self, signal_name: str | None) -> None:
        if not signal_name:
            return
        signal = self.owner.profile.get_signal(signal_name)
        self.signal_enabled_check.set_active(bool(signal.get('enabled', True)))
        self.cooldown_spin.set_value(float(signal.get('cooldown_seconds', 0)))
        filters = signal.get('filters', {}) or {}
        self.debounce_spin.set_value(float(filters.get('debounce_seconds', 0)))

        speech_buffer = self.speech_view.get_buffer()
        speech_buffer.set_text('\n'.join(signal.get('speech', []) or []))

        animation_lines = []
        for item in signal.get('animations', []) or []:
            name = item.get('name', '')
            weight = item.get('weight', 1)
            animation_lines.append(f'{name}={weight}')
        anim_buffer = self.animations_view.get_buffer()
        anim_buffer.set_text('\n'.join(animation_lines))

    def _extract_text_view_lines(self, text_view: Gtk.TextView) -> list[str]:
        buffer = text_view.get_buffer()
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        text = buffer.get_text(start, end, True)
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _parse_animation_lines(self, lines: list[str]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for line in lines:
            if '=' in line:
                name, weight = line.split('=', 1)
                try:
                    parsed.append({'name': name.strip(), 'weight': max(int(weight.strip()), 1)})
                except ValueError:
                    parsed.append({'name': name.strip(), 'weight': 1})
            else:
                parsed.append({'name': line.strip(), 'weight': 1})
        return [item for item in parsed if item.get('name')]

    def _on_signal_selected(self, _listbox, row: Gtk.ListBoxRow | None) -> None:
        self._populate_signal_editor(getattr(row, 'signal_name', None) if row is not None else None)

    def _on_apply_signal(self, *_args) -> None:
        signal_name = self._selected_signal_name()
        if not signal_name:
            return
        signal = self.owner.profile.get_signal(signal_name)
        signal['enabled'] = self.signal_enabled_check.get_active()
        signal['cooldown_seconds'] = int(self.cooldown_spin.get_value())
        speech_lines = self._extract_text_view_lines(self.speech_view)
        signal['speech'] = speech_lines
        signal['animations'] = self._parse_animation_lines(self._extract_text_view_lines(self.animations_view))
        if self.debounce_spin.get_value() > 0:
            filters = signal.setdefault('filters', {})
            filters['debounce_seconds'] = int(self.debounce_spin.get_value())
        self.owner.profile.update_signal(signal_name, signal)
        self.owner.sync_profile_settings()

    def _on_save_profile(self, *_args) -> None:
        new_name = self.profile_name_entry.get_text().strip() or self.owner.profile.name
        self.owner.profile.data['name'] = new_name
        self.owner.profile.save()
        self._reload_behavior_profile_name()

    def _on_help(self, *_args) -> None:
        self.owner.set_speech('Double-click me to open this window again. The Behavior tab edits the JSON profile used by reactions.')

    def _on_play_animation(self, *_args) -> None:
        text = self.anim_combo.get_active_text()
        if text:
            self.owner.play_named_animation(text)

    def _on_fire_signal(self, *_args) -> None:
        signal_name = self.signal_combo.get_active_text()
        if signal_name:
            self.owner.trigger_signal(signal_name, {'source': 'manual'})

    def _on_ok(self, *_args) -> None:
        self.owner.set_agent_by_index(self.gallery_index)
        self._reload_animation_combo()
        self.destroy()


class TrayIndicator:
    def __init__(self, owner: 'AssistantWindow') -> None:
        self.owner = owner
        self.indicator = None
        if AppIndicator3 is None:
            return
        try:
            icon_dir = str(ASSETS_DIR.resolve())
            icon_name = 'clippy_tray_icon-symbolic' if (ASSETS_DIR / 'clippy_tray_icon-symbolic.png').exists() else 'clippy_tray_icon'
            indicator = AppIndicator3.Indicator.new('clippy-assistant', icon_name, AppIndicator3.IndicatorCategory.APPLICATION_STATUS)
            indicator.set_icon_theme_path(icon_dir)
            indicator.set_icon_full(icon_name, 'Clippy')
            indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
            indicator.set_title(APP_NAME)
            indicator.set_menu(self.build_menu())
            self.indicator = indicator
        except Exception as exc:
            print(f'[Tray] failed to init indicator: {exc}')

    def build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()
        show_item = Gtk.MenuItem(label='Show / Hide Assistant')
        show_item.connect('activate', self._on_toggle_visibility)
        menu.append(show_item)
        office_item = Gtk.MenuItem(label='Open Linux Assistant')
        office_item.connect('activate', self._on_open_actions)
        menu.append(office_item)
        rest_item = Gtk.MenuItem(label='RestPose')
        rest_item.connect('activate', self._on_restpose)
        menu.append(rest_item)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label='Quit')
        quit_item.connect('activate', self._on_quit)
        menu.append(quit_item)
        menu.show_all()
        return menu

    def _on_toggle_visibility(self, *_args) -> None:
        self.owner.toggle_visibility()

    def _on_open_actions(self, *_args) -> None:
        self.owner.open_actions_window()

    def _on_restpose(self, *_args) -> None:
        self.owner.play_named_animation('RestPose')

    def _on_quit(self, *_args) -> None:
        self.owner.force_quit()


class AssistantWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title=APP_NAME)
        self.profile_store = ProfileStore(PROFILES_DIR)
        self.profile = self.profile_store.load()

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
        self.connect('delete-event', self._on_delete_event)
        self.connect('button-press-event', self._on_button_press)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)

        self.agent_metas = [AgentMeta(item['id'], item['folder'], item['name'], item['description']) for item in AGENT_LIBRARY]
        self.current_agent_index = 0
        self.agent_data = self._load_agent_data(self.agent_metas[self.current_agent_index])
        self.sound_player = SoundPlayer()
        self.sound_player.set_agent_dir(self.agent_metas[self.current_agent_index].path / 'sounds')
        self.actions_window: OfficeActionsWindow | None = None
        self.queue: deque[tuple[str, str, dict[str, Any]]] = deque()
        self.is_busy = False
        self.last_idle = time.monotonic()
        self.last_recent_uri: str | None = None
        self.last_recent_seen = 0.0
        self.last_global_event = 0.0
        self.observer: Observer | None = None
        self.is_hidden = False
        self._quit_requested = False
        self.tray = TrayIndicator(self)

        root = Gtk.Overlay()
        self.add(root)
        drag_box = Gtk.EventBox()
        drag_box.set_visible_window(False)
        drag_box.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        drag_box.connect('button-press-event', self._on_button_press)
        root.add(drag_box)

        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        layout.set_margin_top(8)
        layout.set_margin_bottom(8)
        layout.set_margin_start(8)
        layout.set_margin_end(8)
        drag_box.add(layout)

        self.top_bubble = OfficeBubble()
        self.top_bubble.set_halign(Gtk.Align.CENTER)
        layout.pack_start(self.top_bubble, False, False, 0)

        self.image = Gtk.Image()
        self.image.set_halign(Gtk.Align.CENTER)
        self.image.set_valign(Gtk.Align.CENTER)
        layout.pack_start(self.image, True, True, 0)

        self.animator = SpriteAnimator(self.image, self._on_animation_finished, self.sound_player.play_sound_id)
        self.animator.set_agent(self.agent_data)

        self.show_all()
        self._set_intro_speech()
        self.sync_profile_settings()
        self._start_watchdog()
        self._start_recent_monitor()
        GLib.timeout_add_seconds(2, self._idle_tick)
        GLib.idle_add(lambda: self.trigger_signal('ProgramStart', {'source': 'app'}))

    def open_actions_window(self) -> None:
        if self.actions_window is None:
            self.actions_window = OfficeActionsWindow(self)
            self.actions_window.connect('destroy', self._on_actions_window_destroy)
        if self.is_hidden or not self.get_visible():
            self.show_all()
            self.present()
            self.is_hidden = False
        self.actions_window.present_for(self)

    def _on_actions_window_destroy(self, *_args) -> None:
        self.actions_window = None

    def sync_profile_settings(self) -> None:
        self.idle_every_seconds = float(self.profile.settings.get('idle_every_seconds', 600))
        self.global_min_gap_seconds = float(self.profile.settings.get('global_min_gap_seconds', 12))
        self.profile.save()

    def _load_agent_data(self, meta: AgentMeta) -> AgentData:
        return AgentData(meta.path / 'agent.json', meta.path / 'map.png')

    def set_agent_by_index(self, index: int) -> None:
        self.current_agent_index = index
        meta = self.agent_metas[index]
        self.agent_data = self._load_agent_data(meta)
        self.sound_player.set_agent_dir(meta.path / 'sounds')
        self.animator.set_agent(self.agent_data)
        if self.actions_window is not None:
            self.actions_window._reload_animation_combo()
        self.set_speech(f'{meta.name} is now ready to help you.')

    def set_speech(self, text: str) -> None:
        self.top_bubble.set_text(clamp_text(text, 160))

    def _set_intro_speech(self) -> None:
        self.set_speech(f'{self.agent_metas[self.current_agent_index].name} is watching your files.')

    def manual_event(self, event_type: str) -> None:
        legacy_map = {
            'created_dir': 'FolderCreated',
            'created_file': 'FileSaved',
            'modified': 'FileSaved',
            'deleted': 'TrashEmptied',
            'moved': 'SystemSearch',
            'opened': 'FileOpened',
            'idle': 'IdlePulse',
        }
        self.trigger_signal(legacy_map.get(event_type, event_type), {'source': 'manual'})

    def play_named_animation(self, name: str) -> None:
        self.queue.clear()
        self.is_busy = True
        self.last_idle = time.monotonic()
        self.set_speech(f'Playing animation: {name}')
        self.animator.set_animation(name)

    def trigger_signal(self, signal_name: str, context: dict[str, Any] | None = None) -> bool:
        context = context or {}
        animation, speech = self.profile.choose_reaction(signal_name, context)
        if animation is None and speech is None:
            return False
        payload = str(context.get('path') or context.get('display_name') or context.get('source', signal_name))
        self.queue.append((signal_name, payload, {'animation': animation, 'speech': speech, **context}))
        self._try_start_next()
        return False

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
            self.open_actions_window()
            return True
        if event.type == Gdk.EventType.BUTTON_PRESS:
            try:
                self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            except Exception:
                pass
        return False

    def toggle_visibility(self) -> None:
        if self.is_hidden or not self.get_visible():
            self.show_all()
            self.present()
            self.is_hidden = False
        else:
            if self.actions_window is not None:
                self.actions_window.hide()
            self.hide()
            self.is_hidden = True

    def force_quit(self) -> None:
        self._quit_requested = True
        self.trigger_signal('ProgramExit', {'source': 'quit'})
        GLib.timeout_add(250, self._finish_quit)

    def _finish_quit(self) -> bool:
        if self.actions_window is not None:
            try:
                self.actions_window.destroy()
            except Exception:
                pass
            self.actions_window = None
        self.destroy()
        return False

    def _on_delete_event(self, *_args):
        if self._quit_requested:
            return False
        if self.actions_window is not None:
            self.actions_window.hide()
        self.hide()
        self.is_hidden = True
        return True

    def _on_destroy(self, *_args) -> None:
        if self.observer is not None:
            self.observer.stop()
            self.observer.join(timeout=2)
        if self.actions_window is not None:
            try:
                self.actions_window.destroy()
            except Exception:
                pass
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
        observer.daemon = True
        observer.start()
        self.observer = observer

    def _watchdog_callback(self, event_type: str, path: str) -> None:
        GLib.idle_add(self._handle_fs_event, event_type, path)

    def _handle_fs_event(self, event_type: str, raw_path: str) -> bool:
        path = Path(raw_path)
        if event_type == 'created_dir':
            self.trigger_signal('FolderCreated', {'path': str(path), 'display_name': path.name, 'dedupe_key': f'folder:{path}'})
            return False

        if event_type in {'created_file', 'modified', 'moved'}:
            signal_name, context = self._classify_file_event(event_type, path)
            if signal_name:
                self.trigger_signal(signal_name, context)
            return False

        if event_type == 'deleted':
            if '.local/share/Trash/files' in str(path):
                self.trigger_signal('TrashEmptied', {'path': str(path), 'display_name': path.name, 'dedupe_key': 'trash'})
            return False
        return False

    def _classify_file_event(self, event_type: str, path: Path) -> tuple[str | None, dict[str, Any]]:
        display_name = path.name
        dedupe_key = f'{event_type}:{path}'
        if self._is_archive_path(path):
            return 'ArchiveCreated', {'path': str(path), 'display_name': display_name, 'dedupe_key': f'archive:{path.stem}'}

        file_saved_signal = self.profile.get_signal('FileSaved')
        filters = file_saved_signal.get('filters', {}) or {}
        if self._should_ignore_saved_file(path, filters):
            return None, {}
        return 'FileSaved', {'path': str(path), 'display_name': display_name, 'dedupe_key': dedupe_key}

    def _is_archive_path(self, path: Path) -> bool:
        lower = path.name.lower()
        return any(lower.endswith(ext) for ext in SUPPORTED_ARCHIVE_EXTENSIONS)

    def _should_ignore_saved_file(self, path: Path, filters: dict[str, Any]) -> bool:
        name = path.name
        lower = name.lower()
        if filters.get('ignore_hidden_files', True) and name.startswith('.'):
            return True
        if filters.get('ignore_temp_files', True):
            patterns = list(filters.get('ignore_patterns', []))
            if any(fnmatch.fnmatch(lower, pattern.lower()) for pattern in patterns):
                return True
        return False

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
        self.last_recent_uri = uri
        self.last_recent_seen = now
        self.trigger_signal('FileOpened', {'display_name': item.get_display_name() or uri, 'path': uri, 'dedupe_key': f'open:{uri}'})

    def _try_start_next(self) -> None:
        if self.is_busy or not self.queue:
            return
        if self.global_min_gap_seconds > 0 and (time.monotonic() - self.last_global_event) < self.global_min_gap_seconds:
            GLib.timeout_add(int(self.global_min_gap_seconds * 1000), self._continue_queue)
            return
        signal_name, payload, context = self.queue.popleft()
        animation = context.get('animation') or 'RestPose'
        speech = context.get('speech')
        if speech is None:
            speech = signal_name
        name = Path(payload).name if payload else payload
        final_speech = speech
        if name and name not in final_speech:
            final_speech = f'{speech}\n{name}'
        self.set_speech(final_speech)
        self.is_busy = True
        self.last_idle = time.monotonic()
        self.last_global_event = time.monotonic()
        self.animator.set_animation(animation)

    def _on_animation_finished(self, _animation_name: str) -> None:
        self.is_busy = False
        if self.agent_data.has_animation('RestPose'):
            self.animator.set_animation('RestPose')
        if self._quit_requested and not self.queue:
            GLib.timeout_add(80, self._finish_quit)
            return
        GLib.timeout_add(50, self._continue_queue)

    def _continue_queue(self) -> bool:
        self._try_start_next()
        return False

    def _idle_tick(self) -> bool:
        if not self.is_busy and not self.queue and (time.monotonic() - self.last_idle) >= self.idle_every_seconds:
            self.trigger_signal('IdlePulse', {'source': 'idle'})
        return True


def clamp_text(text: str, limit: int = 120) -> str:
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + '…'


def install_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(RETRO_CSS)
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
