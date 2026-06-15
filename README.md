# Lumi

A sleek, modern desktop music player for Linux built with Python and PySide6. Lumi plays local audio files and streams audio from YouTube, with a glassmorphic UI that dynamically adapts its color theme to your album art.

![Lumi](assets/lumi.png)



## Features

- **Local Library** — Scan a folder and browse albums; supports MP3, FLAC, WAV, M4A, AIFF, ALAC, OGG, Opus, WMA, APE
- **YouTube Streaming** — Search YouTube and stream audio directly via yt-dlp (no download required)
- **Hi-Res Audio** — Powered by mpv for full format support including 24-bit and hi-res files
- **Parametric Equalizer** — 5-band EQ with built-in headphone presets (Sennheiser HD 560S, Beyerdynamic DT770 Pro, Moondrop Aria, Bass Shelf, Treble Shelf)
- **Dynamic Theming** — Album art drives a blurred background and accent colors extracted via ColorThief
- **MPRIS2 Support** — Integrates with Linux desktop media controls, notification center, and media keys via D-Bus
- **Queue Management** — Shuffle and repeat modes with shuffle history for previous-track navigation
- **System Tray** — Minimize to tray with playback controls
- **Marquee Labels** — Song titles scroll smoothly when they overflow

## Tech Stack

| Layer | Library |
|---|---|
| UI Framework | PySide6 (Qt6) |
| Audio Playback | mpv (python-mpv) |
| YouTube | yt-dlp |
| Metadata / Art | mutagen |
| Color Extraction | colorthief |
| MPRIS / D-Bus | dbus-python, PyGObject |
| Packaging | PyInstaller |

## Project Structure

```
lumi/
├── main.py                  # Entry point
├── ui/
│   ├── main_window.py       # Main application window
│   └── eq_dialog.py         # Equalizer dialog
├── player/
│   ├── mpv_player.py        # mpv-backed audio player
│   ├── queue_manager.py     # Playback queue with shuffle/repeat
│   ├── mpris.py             # MPRIS2 D-Bus service
│   ├── equalizer.py         # EQ presets and lavfi filter builder
│   └── youtube.py           # YouTube search & stream workers (QThread)
├── library/
│   └── scanner.py           # Recursive music folder scanner
├── services/
│   └── youtube_search.py    # Synchronous YouTube search helper
└── assets/
    └── lumi.png             # App icon
```

## Installation

### Prerequisites

- Python 3.12+
- `mpv` installed on your system (`sudo apt install mpv` / `sudo pacman -S mpv`)
- For MPRIS support: `python3-dbus` and `python3-gi` system packages

### Setup

```bash
git clone <repo-url>
cd lumi

python -m venv venv
source venv/bin/activate

pip install PySide6 python-mpv yt-dlp mutagen colorthief
# Optional (MPRIS):
pip install dbus-python PyGObject
```

### Run

```bash
python main.py
```

## Build Standalone Binary

Uses PyInstaller:

```bash
pip install pyinstaller
pyinstaller Lumi.spec
# Output: dist/Lumi
```

## Usage

1. **Library tab** — Click "Open Folder" to scan your music directory. Albums appear in the sidebar; click an album to load its tracks.
2. **YouTube tab** — Type a search query and press Enter. Click a result to stream it.
3. **Equalizer** — Click the EQ button to open the 5-band parametric equalizer. Choose a headphone preset or dial in your own settings.
4. **Playback controls** — Previous, Play/Pause, Next, shuffle toggle, volume slider, and a seek bar are always visible.
5. **Media keys** — Hardware media keys and MPRIS-aware apps (e.g., KDE, GNOME shell) control playback automatically.

## Keyboard Shortcuts

| Key | Action |
|---|---|
| Space | Play / Pause |
| ← / → | Seek backward / forward |
| N | Next track |
| P | Previous track |

## License

MIT
