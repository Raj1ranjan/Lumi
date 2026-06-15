from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QPushButton,
    QFileDialog,
    QLabel,
    QFrame,
    QHBoxLayout,
    QSlider,
    QGraphicsDropShadowEffect,
    QGraphicsBlurEffect,
    QGraphicsOpacityEffect,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QLineEdit,
    QSystemTrayIcon,
    QMenu,
    QApplication,
    QTabWidget,
)
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QSettings, QThread, Signal, QMimeData
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtGui import QPixmap, QShortcut, QKeySequence, QColor, QPainter, QRadialGradient, QLinearGradient, QIcon, QAction
from colorthief import ColorThief
import os
import tempfile
from mutagen import File

from player.mpv_player import MPVPlayer
from player.queue_manager import QueueManager
from player.youtube import YoutubeSearchWorker, StreamUrlWorker
from player.mpris import MprisController
from player.equalizer import DEFAULT_BANDS
from ui.eq_dialog import EQDialog
from library.scanner import MusicLibrary


def _track_duration(file_path):
    """Return duration string mm:ss or empty string."""
    try:
        audio = File(file_path)
        if audio and audio.info:
            secs = int(audio.info.length)
            return f"{secs // 60}:{secs % 60:02}"
    except Exception:
        pass
    return ""


class ArtLoader(QThread):
    """Load album art thumbnail in background."""
    done = Signal(str, QPixmap)  # album_name, pixmap

    def __init__(self, album_name, file_path):
        super().__init__()
        self.album_name = album_name
        self.file_path = file_path

    def run(self):
        try:
            audio = File(self.file_path)
            if audio is None:
                return
            artwork = None
            if hasattr(audio, "pictures") and audio.pictures:
                artwork = audio.pictures[0].data
            elif hasattr(audio, "tags") and audio.tags:
                apic = audio.tags.getall("APIC") if hasattr(audio.tags, "getall") else []
                if apic:
                    artwork = apic[0].data
                else:
                    covr = audio.tags.get("covr")
                    if covr:
                        artwork = bytes(covr[0])
            if artwork:
                px = QPixmap()
                px.loadFromData(artwork)
                self.done.emit(self.album_name, px.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:
            pass


class ThumbnailLoader(QThread):
    """Fetch a YouTube thumbnail URL and emit a QPixmap."""
    done = Signal(QPixmap, bytes)  # pixmap, raw_bytes

    def __init__(self, video_id):
        super().__init__()
        self.video_id = video_id

    def run(self):
        import urllib.request
        for quality in ("maxresdefault", "hqdefault", "mqdefault"):
            url = f"https://img.youtube.com/vi/{self.video_id}/{quality}.jpg"
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    data = r.read()
                px = QPixmap()
                if px.loadFromData(data):
                    self.done.emit(px, data)
                    return
            except Exception:
                continue


class MarqueeLabel(QLabel):
    """Scrolls text horizontally when it overflows."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._offset = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._scroll)
        self._full_text = ""

    def setText(self, text):
        self._full_text = text
        self._offset = 0
        super().setText(text)
        self._timer.stop()
        # Start scrolling only if text overflows
        QTimer.singleShot(0, self._check_overflow)

    def _check_overflow(self):
        fm = self.fontMetrics()
        if fm.horizontalAdvance(self._full_text) > self.width():
            self._timer.start(30)
        else:
            super().setText(self._full_text)

    def _scroll(self):
        fm = self.fontMetrics()
        text_width = fm.horizontalAdvance(self._full_text)
        if text_width <= self.width():
            self._timer.stop()
            super().setText(self._full_text)
            return
        gap = "   "
        gap_width = fm.horizontalAdvance(gap)
        cycle = text_width + gap_width
        self._offset = (self._offset + 2) % cycle
        # Find how many chars to skip to match pixel offset
        clipped = self._full_text
        while clipped and fm.horizontalAdvance(self._full_text) - fm.horizontalAdvance(clipped) < self._offset:
            clipped = clipped[1:]
        super().setText(clipped + gap + self._full_text)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()

        # Background blur layer
        self.bg_label = QLabel(self)
        self.bg_label.setScaledContents(True)
        self.bg_blur = QGraphicsBlurEffect()
        self.bg_blur.setBlurRadius(80)
        self.bg_label.setGraphicsEffect(self.bg_blur)
        self.bg_label.lower()

        # Vignette overlay (Fix 7)
        self.vignette = QLabel(self)
        self.vignette.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.vignette.raise_()

        self.player = MPVPlayer()
        self.queue = QueueManager()
        self.library = MusicLibrary()
        self.current_album = None
        self._base_stylesheet = None
        self._youtube_results = []
        self._online_queue = []
        self._online_queue_index = -1
        self._art_loaders = []
        self._art_loader_queue = []
        self._art_loaders_running = 0
        self._bg_pixmap = None
        self._eq_bands = list(DEFAULT_BANDS)  # current EQ state

        # MPRIS
        self.mpris = MprisController()
        self.mpris.cb_play_pause = self._toggle_play
        self.mpris.cb_play = lambda: self.player._using_mpv and not self.player._mpv.pause or self.player.player.play()
        self.mpris.cb_pause = lambda: self.player.pause() if self.play_btn.text() == "⏸" else None
        self.mpris.cb_stop = self.player.stop
        self.mpris.cb_next = self.play_next
        self.mpris.cb_previous = self.play_previous
        self.mpris.cb_seek = lambda off_us: self.player.seek_ms(self.player.get_position_ms() + off_us // 1000)
        self.mpris.cb_set_position = lambda pos_us: self.player.seek_ms(pos_us // 1000)
        self.mpris.cb_set_volume = lambda v: self.volume_slider.setValue(int(v * 100))
        self.mpris.cb_raise = lambda: (self.showNormal(), self.activateWindow())
        self.mpris.cb_quit = QApplication.quit
        self.mpris.start()

        self.setWindowTitle("Lumi")
        self.resize(1400, 900)

        self.setStyleSheet("""
            QWidget {
                background-color: #0a0a0a;
                color: white;
                font-family: Inter;
            }

            MainWindow {
                background-color: #0a0a0a;
            }

            #card {
                background-color: rgba(255, 255, 255, 10);
                border: 1px solid rgba(255, 255, 255, 20);
                border-top: 1px solid rgba(255, 255, 255, 40);
                border-radius: 32px;
            }

            #albumArt {
                background-color: rgba(255, 255, 255, 6);
                border-radius: 20px;
                font-size: 20px;
                color: #555555;
            }

            #songTitle {
                font-size: 36px;
                font-weight: 700;
                color: white;
            }

            #artistName {
                font-size: 16px;
                letter-spacing: 1px;
                color: rgba(255, 255, 255, 0.55);
            }

            #sideLabel {
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 2px;
                color: rgba(255, 255, 255, 0.3);
                padding: 8px 12px 4px 12px;
            }

            QPushButton {
                background-color: rgba(255, 255, 255, 12);
                border: 1px solid rgba(255, 255, 255, 15);
                border-radius: 18px;
                padding: 10px 16px;
                font-size: 15px;
                color: rgba(255, 255, 255, 0.85);
            }

            QPushButton:hover {
                background-color: rgba(255, 255, 255, 24);
                border: 1px solid rgba(255, 255, 255, 30);
                color: white;
            }

            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 35);
            }

            #playButton {
                background-color: white;
                color: black;
                font-size: 22px;
                font-weight: bold;
                border: none;
                border-radius: 32px;
            }

            #playButton:hover {
                background-color: #e8e8e8;
            }

            #playButton:pressed {
                background-color: #cccccc;
            }

            #iconBtn {
                background-color: transparent;
                border: none;
                font-size: 20px;
                padding: 8px;
                border-radius: 16px;
                color: rgba(255, 255, 255, 0.7);
            }

            #iconBtn:hover {
                background-color: rgba(255, 255, 255, 14);
                color: white;
            }

            QSlider::groove:horizontal {
                height: 5px;
                background: rgba(255, 255, 255, 15);
                border-radius: 3px;
            }

            QSlider::handle:horizontal {
                background: white;
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }

            QSlider::sub-page:horizontal {
                background: white;
                border-radius: 3px;
            }

            #volumeSlider::groove:horizontal {
                height: 3px;
                background: rgba(255, 255, 255, 15);
                border-radius: 2px;
            }

            #volumeSlider::handle:horizontal {
                background: rgba(255, 255, 255, 0.7);
                width: 10px;
                height: 10px;
                margin: -4px 0;
                border-radius: 5px;
            }

            #volumeSlider::sub-page:horizontal {
                background: rgba(255, 255, 255, 0.5);
                border-radius: 2px;
            }

            #albumList {
                background-color: rgba(12, 12, 12, 180);
                border: 1px solid rgba(255, 255, 255, 12);
                border-top: 1px solid rgba(255, 255, 255, 22);
                border-radius: 24px;
                padding: 8px;
                font-size: 14px;
            }

            #albumList::item {
                padding: 10px 14px;
                border-radius: 10px;
                color: rgba(255, 255, 255, 0.75);
            }

            #albumList::item:selected {
                background-color: rgba(255, 255, 255, 16);
                color: white;
            }

            #albumList::item:hover {
                background-color: rgba(255, 255, 255, 8);
                color: white;
            }

            #trackList {
                background-color: rgba(255, 255, 255, 7);
                border: 1px solid rgba(255, 255, 255, 12);
                border-top: 1px solid rgba(255, 255, 255, 22);
                border-radius: 24px;
                padding: 8px;
                font-size: 13px;
            }

            #trackList::item {
                padding: 9px 14px;
                border-radius: 10px;
                color: rgba(255, 255, 255, 0.7);
            }

            #trackList::item:selected {
                background-color: rgba(255, 255, 255, 16);
                color: white;
            }

            #trackList::item:hover {
                background-color: rgba(255, 255, 255, 8);
                color: white;
            }

            #searchBox {
                background-color: rgba(255, 255, 255, 8);
                border: 1px solid rgba(255, 255, 255, 12);
                border-radius: 10px;
                padding: 6px 10px;
                font-size: 12px;
                color: rgba(255, 255, 255, 0.7);
                margin: 0 4px;
            }

            #searchBox:focus {
                border: 1px solid rgba(255, 255, 255, 30);
                color: white;
            }

            #leftTabs::pane {
                border: none;
                background: transparent;
            }

            #leftTabs QTabBar::tab {
                background: rgba(255, 255, 255, 8);
                border: 1px solid rgba(255, 255, 255, 12);
                border-radius: 8px;
                padding: 6px 18px;
                margin-right: 4px;
                font-size: 12px;
                font-weight: 600;
                letter-spacing: 1px;
                color: rgba(255, 255, 255, 0.5);
            }

            #leftTabs QTabBar::tab:selected {
                background: rgba(255, 255, 255, 18);
                border: 1px solid rgba(255, 255, 255, 28);
                color: white;
            }

            #leftTabs QTabBar::tab:hover:!selected {
                background: rgba(255, 255, 255, 12);
                color: rgba(255, 255, 255, 0.75);
            }
        """)

        self._base_stylesheet = self.styleSheet()

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # --- Left panel: tabbed Library / Online ---
        left_tabs = QTabWidget()
        left_tabs.setFixedWidth(430)
        left_tabs.setObjectName("leftTabs")

        # Library tab: albums + tracks side by side
        library_widget = QWidget()
        library_layout = QHBoxLayout(library_widget)
        library_layout.setContentsMargins(0, 4, 0, 0)
        library_layout.setSpacing(8)

        album_sidebar = QVBoxLayout()
        album_sidebar.setContentsMargins(0, 0, 0, 0)
        album_sidebar.setSpacing(4)
        album_label = QLabel("ALBUMS")
        album_label.setObjectName("sideLabel")
        self.album_search = QLineEdit()
        self.album_search.setPlaceholderText("Search albums…")
        self.album_search.setObjectName("searchBox")
        self.album_list = QListWidget()
        self.album_list.setObjectName("albumList")
        self.album_list.setIconSize(__import__('PySide6.QtCore', fromlist=['QSize']).QSize(40, 40))
        album_sidebar.addWidget(album_label)
        album_sidebar.addWidget(self.album_search)
        album_sidebar.addWidget(self.album_list)
        album_sidebar_widget = QWidget()
        album_sidebar_widget.setLayout(album_sidebar)

        track_sidebar = QVBoxLayout()
        track_sidebar.setContentsMargins(0, 0, 0, 0)
        track_sidebar.setSpacing(4)
        track_label = QLabel("TRACKS")
        track_label.setObjectName("sideLabel")
        self.track_search = QLineEdit()
        self.track_search.setPlaceholderText("Search tracks…")
        self.track_search.setObjectName("searchBox")
        self.track_list = QListWidget()
        self.track_list.setObjectName("trackList")
        self.track_list.setDragDropMode(QListWidget.InternalMove)
        track_sidebar.addWidget(track_label)
        track_sidebar.addWidget(self.track_search)
        track_sidebar.addWidget(self.track_list)
        track_sidebar_widget = QWidget()
        track_sidebar_widget.setLayout(track_sidebar)

        library_layout.addWidget(album_sidebar_widget)
        library_layout.addWidget(track_sidebar_widget)

        # Online tab
        online_widget = QWidget()
        online_layout = QHBoxLayout(online_widget)
        online_layout.setContentsMargins(0, 4, 0, 0)
        online_layout.setSpacing(8)

        # --- Search column ---
        search_col = QVBoxLayout()
        search_col.setSpacing(4)
        search_col.setContentsMargins(0, 0, 0, 0)
        search_label = QLabel("SEARCH")
        search_label.setObjectName("sideLabel")
        search_row = QHBoxLayout()
        self.online_search = QLineEdit()
        self.online_search.setPlaceholderText("Search YouTube…")
        self.online_search.setObjectName("searchBox")
        self.search_btn = QPushButton("Go")
        self.search_btn.setFixedWidth(36)
        search_row.addWidget(self.online_search)
        search_row.addWidget(self.search_btn)
        self.online_results = QListWidget()
        self.online_results.setObjectName("trackList")
        self.online_results.setContextMenuPolicy(Qt.CustomContextMenu)
        self.online_results.customContextMenuRequested.connect(self._online_results_ctx)
        self.online_status = QLabel("")
        self.online_status.setStyleSheet("font-size: 11px; color: rgba(255,255,255,0.4);")
        search_col.addWidget(search_label)
        search_col.addLayout(search_row)
        search_col.addWidget(self.online_results)
        search_col.addWidget(self.online_status)
        search_col_widget = QWidget()
        search_col_widget.setLayout(search_col)

        # --- Queue column ---
        queue_col = QVBoxLayout()
        queue_col.setSpacing(4)
        queue_col.setContentsMargins(0, 0, 0, 0)
        queue_label = QLabel("UP NEXT")
        queue_label.setObjectName("sideLabel")
        self.online_queue_list = QListWidget()
        self.online_queue_list.setObjectName("trackList")
        self.online_queue_list.setDragDropMode(QListWidget.InternalMove)
        self.online_queue_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.online_queue_list.customContextMenuRequested.connect(self._online_queue_ctx)
        self.online_queue_list.itemDoubleClicked.connect(self._online_queue_play_item)
        queue_clear_btn = QPushButton("Clear")
        queue_clear_btn.setFixedHeight(28)
        queue_clear_btn.clicked.connect(self._online_queue_clear)
        queue_col.addWidget(queue_label)
        queue_col.addWidget(self.online_queue_list)
        queue_col.addWidget(queue_clear_btn)
        queue_col_widget = QWidget()
        queue_col_widget.setLayout(queue_col)

        online_layout.addWidget(search_col_widget)
        online_layout.addWidget(queue_col_widget)

        left_tabs.addTab(library_widget, "Library")
        left_tabs.addTab(online_widget, "Online")
        self.left_tabs = left_tabs

        # Main Card
        card = QFrame()
        card.setObjectName("card")

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 32, 32, 32)
        card_layout.setSpacing(0)
        card_layout.setAlignment(Qt.AlignCenter)

        # Content container — max 700px wide (Fix 2)
        content_container = QWidget()
        content_container.setMaximumWidth(700)
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)
        content_layout.setAlignment(Qt.AlignCenter)

        # Album Art — centered square 420×420 (Fix 1)
        self.album_art = QLabel()
        self.album_art.setObjectName("albumArt")
        self.album_art.setFixedSize(420, 420)
        self.album_art.setAlignment(Qt.AlignCenter)
        self.album_art.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.glow = QGraphicsDropShadowEffect()
        self.glow.setBlurRadius(140)
        self.glow.setOffset(0, 0)
        self.glow.setColor(Qt.white)
        self.album_art.setGraphicsEffect(self.glow)

        self.glow_anim = QPropertyAnimation(self.glow, b"blurRadius")
        self.glow_anim.setStartValue(80)
        self.glow_anim.setEndValue(140)
        self.glow_anim.setDuration(1800)
        self.glow_anim.setLoopCount(-1)
        self.glow_anim.setEasingCurve(QEasingCurve.InOutSine)

        self.art_opacity = QGraphicsOpacityEffect()
        self.bg_anim = QPropertyAnimation(self.bg_label, b"pos")
        self.bg_anim.setDuration(12000)
        self.bg_anim.setStartValue(QPoint(-20, -20))
        self.bg_anim.setEndValue(QPoint(20, 20))
        self.bg_anim.setLoopCount(-1)
        self.bg_anim.setEasingCurve(QEasingCurve.InOutSine)

        # Song Title
        self.song_title = MarqueeLabel("No song playing")
        self.song_title.setObjectName("songTitle")

        # Artist
        self.artist_name = QLabel("Unknown Artist")
        self.artist_name.setObjectName("artistName")

        # Audio info strip
        self.audio_info_lbl = QLabel("")
        self.audio_info_lbl.setAlignment(Qt.AlignCenter)
        self.audio_info_lbl.setStyleSheet(
            "font-size: 10px; letter-spacing: 1px; color: rgba(255,255,255,0.28); font-family: monospace;"
        )

        # Seekbar
        self.seekbar = QSlider(Qt.Horizontal)

        self.current_time = QLabel("0:00")
        self.total_time = QLabel("0:00")
        for lbl in (self.current_time, self.total_time):
            lbl.setStyleSheet("font-size: 11px; color: rgba(255,255,255,0.4);")
        time_layout = QHBoxLayout()
        time_layout.addWidget(self.current_time)
        time_layout.addStretch()
        time_layout.addWidget(self.total_time)

        # Volume row (icon + compact slider)
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setObjectName("volumeSlider")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(100)
        vol_icon = QLabel("🔊")
        vol_icon.setStyleSheet("font-size: 13px; color: rgba(255,255,255,0.4);")

        # Controls
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(10)
        controls_layout.setAlignment(Qt.AlignCenter)

        self.prev_btn = QPushButton("⏮")
        self.play_btn = QPushButton("▶")
        self.next_btn = QPushButton("⏭")
        self.open_btn = QPushButton("Open")
        self.immersive_btn = QPushButton("⛶")
        self.next_album_btn = QPushButton("Next Album")
        self.prev_album_btn = QPushButton("Prev Album")
        self.shuffle_btn = QPushButton("⇀")
        self.repeat_btn = QPushButton("↻")
        self.eq_btn = QPushButton("EQ")

        for btn in (self.prev_btn, self.next_btn, self.immersive_btn, self.shuffle_btn, self.repeat_btn):
            btn.setObjectName("iconBtn")
            btn.setFixedSize(44, 44)

        self.play_btn.setObjectName("playButton")
        self.play_btn.setFixedSize(64, 64)

        controls_layout.addWidget(self.shuffle_btn)
        controls_layout.addWidget(self.prev_btn)
        controls_layout.addWidget(self.play_btn)
        controls_layout.addWidget(self.next_btn)
        controls_layout.addWidget(self.repeat_btn)

        # Secondary row: open, album nav, volume, immersive
        secondary_layout = QHBoxLayout()
        secondary_layout.setSpacing(8)
        secondary_layout.setAlignment(Qt.AlignCenter)
        secondary_layout.addWidget(self.prev_album_btn)
        secondary_layout.addWidget(self.open_btn)
        secondary_layout.addWidget(self.next_album_btn)
        secondary_layout.addSpacing(16)
        secondary_layout.addWidget(vol_icon)
        secondary_layout.addWidget(self.volume_slider)
        secondary_layout.addSpacing(8)
        secondary_layout.addWidget(self.eq_btn)
        secondary_layout.addWidget(self.immersive_btn)

        # Assemble content into centered container (Fix 2)
        art_wrapper = QHBoxLayout()
        art_wrapper.setAlignment(Qt.AlignCenter)
        art_wrapper.addWidget(self.album_art)

        content_layout.addLayout(art_wrapper)
        content_layout.addSpacing(24)
        content_layout.addWidget(self.song_title, alignment=Qt.AlignCenter)
        content_layout.addWidget(self.artist_name, alignment=Qt.AlignCenter)
        content_layout.addWidget(self.audio_info_lbl, alignment=Qt.AlignCenter)
        content_layout.addSpacing(20)
        content_layout.addWidget(self.seekbar)
        content_layout.addLayout(time_layout)
        content_layout.addSpacing(16)
        content_layout.addLayout(controls_layout)
        content_layout.addSpacing(8)
        content_layout.addLayout(secondary_layout)

        card_layout.addWidget(content_container, alignment=Qt.AlignCenter)

        main_layout.addWidget(left_tabs)
        main_layout.addWidget(card, 1)

        # Timer for Seekbar
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_player_ui)
        self.update_timer.start(500)

        self.seekbar.sliderMoved.connect(self.seek_to_position)
        self.open_btn.clicked.connect(self.open_music)
        self.play_btn.clicked.connect(self._toggle_play)
        self.next_btn.clicked.connect(self.play_next)
        self.prev_btn.clicked.connect(self.play_previous)
        self.volume_slider.valueChanged.connect(self.change_volume)
        self.next_album_btn.clicked.connect(self.next_album)
        self.prev_album_btn.clicked.connect(self.previous_album)
        self.album_list.itemClicked.connect(self.album_clicked)
        self.track_list.itemClicked.connect(self.track_clicked)
        self.track_list.model().rowsMoved.connect(self._on_track_reordered)
        self.player.player.mediaStatusChanged.connect(self.handle_media_status)
        self.player.player.playbackStateChanged.connect(self._update_play_btn)
        self.player.end_of_track.connect(self.play_next)  # mpv stream end-of-track
        self.player.pause_changed.connect(self._on_mpv_pause_changed)
        self.shuffle_btn.clicked.connect(self._toggle_shuffle)
        self.repeat_btn.clicked.connect(self._toggle_repeat)
        self.album_search.textChanged.connect(self._filter_albums)
        self.track_search.textChanged.connect(self._filter_tracks)
        self.search_btn.clicked.connect(self.search_online)
        self.online_search.returnPressed.connect(self.search_online)
        self.online_results.itemClicked.connect(self.play_online_song)
        self.online_queue_list.model().rowsMoved.connect(self._on_online_queue_reordered)
        self.eq_btn.clicked.connect(self._open_eq)

        # Keyboard Shortcuts
        self.space_shortcut = QShortcut(QKeySequence("Space"), self)
        self.space_shortcut.activated.connect(self._toggle_play)
        self.right_shortcut = QShortcut(QKeySequence("Right"), self)
        self.right_shortcut.activated.connect(self.seek_forward)
        self.left_shortcut = QShortcut(QKeySequence("Left"), self)
        self.left_shortcut.activated.connect(self.seek_backward)
        self.up_shortcut = QShortcut(QKeySequence("Up"), self)
        self.up_shortcut.activated.connect(self.volume_up)
        self.down_shortcut = QShortcut(QKeySequence("Down"), self)
        self.down_shortcut.activated.connect(self.volume_down)
        self.f_shortcut = QShortcut(QKeySequence("F"), self)
        self.f_shortcut.activated.connect(self.toggle_immersive_mode)
        self.open_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        self.open_shortcut.activated.connect(self.open_music)
        # Media keys
        QShortcut(QKeySequence("Media Play"), self).activated.connect(self._toggle_play)
        QShortcut(QKeySequence("Media Next"), self).activated.connect(self.play_next)
        QShortcut(QKeySequence("Media Previous"), self).activated.connect(self.play_previous)

        self.immersive_mode = False
        self.immersive_btn.clicked.connect(self.toggle_immersive_mode)

        # System tray
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(QIcon("assets/lumi.png") if os.path.exists("assets/lumi.png")
                           else self.style().standardIcon(__import__('PySide6.QtWidgets', fromlist=['QStyle']).QStyle.SP_MediaPlay))
        tray_menu = QMenu()
        self._tray_track_action = tray_menu.addAction("No song playing")
        self._tray_track_action.setEnabled(False)
        tray_menu.addSeparator()
        self._tray_playpause_action = tray_menu.addAction("▶  Play / Pause", self._toggle_play)
        tray_menu.addAction("⏭  Next", self.play_next)
        tray_menu.addAction("⏮  Previous", self.play_previous)
        tray_menu.addSeparator()
        tray_menu.addAction("Quit", QApplication.quit)
        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._tray_activated)
        self._tray.show()

        # Restore settings
        self._settings = QSettings("Lumi", "Lumi")
        saved_vol = int(self._settings.value("volume", 80))
        self.volume_slider.setValue(saved_vol)
        self.player.audio_output.setVolume(saved_vol / 100.0)

        last_folder = self._settings.value("last_folder")
        if last_folder and os.path.isdir(last_folder):
            self._load_folder(last_folder, autoplay=False)
            last_album = self._settings.value("last_album")
            last_index = int(self._settings.value("last_track_index", 0))
            if last_album and last_album in self.library.albums:
                self.load_album(last_album, autoplay=False)
                if 0 <= last_index < self.track_list.count():
                    self.queue.current_index = last_index
                    self.track_list.setCurrentRow(last_index)
                    song = self.queue.current_song()
                    if song:
                        self.load_metadata(song)
                        self.load_album_art(song)

    def resizeEvent(self, event):
        self.bg_label.resize(self.size())
        self.vignette.resize(self.size())
        self._paint_vignette()
        if self._bg_pixmap:
            self.bg_label.setPixmap(
                self._bg_pixmap.scaled(800, 800, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            )
        super().resizeEvent(event)

    def _notify(self, title: str, body: str):
        """Send a desktop notification for the current track."""
        try:
            import gi
            gi.require_version("Notify", "0.7")
            from gi.repository import Notify, GdkPixbuf
            if not Notify.is_initted():
                Notify.init("Lumi")
            n = Notify.Notification.new(title, body, None)
            # Attach album art if available
            px = self.album_art.pixmap()
            if px and not px.isNull():
                import tempfile, os
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                    tmp = f.name
                px.scaled(128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(tmp, "PNG")
                try:
                    pbuf = GdkPixbuf.Pixbuf.new_from_file(tmp)
                    n.set_image_from_pixbuf(pbuf)
                finally:
                    os.remove(tmp)
            n.set_hint("transient", GLib.Variant.new_boolean(True))
            n.set_timeout(3000)
            n.show()
        except Exception:
            pass  # notifications are best-effort

    def closeEvent(self, event):
        for loader in self._art_loaders:
            loader.quit()
            loader.wait()
        for attr in ("_search_worker", "_thumb_loader"):
            worker = getattr(self, attr, None)
            if worker and worker.isRunning():
                worker.quit()
                worker.wait()
        stream = getattr(self, "_stream_worker", None)
        if stream and stream.isRunning():
            stream.cancel()
            stream.wait()
        self.player.cleanup()
        super().closeEvent(event)

    def _paint_vignette(self):
        w, h = self.width(), self.height()
        pixmap = QPixmap(w, h)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        grad = QRadialGradient(w / 2, h / 2, max(w, h) * 0.7)
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0, QColor(0, 0, 0, 160))
        painter.fillRect(0, 0, w, h, grad)
        painter.end()
        self.vignette.setPixmap(pixmap)

    # Window Dragging Logic (removed — native window handles dragging)

    def update_ambient_colors(self, image_data):
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
                f.write(image_data)
                temp_path = f.name

            try:
                r, g, b = ColorThief(temp_path).get_color(quality=1)
            finally:
                os.remove(temp_path)

            # Fix 3: darker overlay so UI floats above background
            self.setStyleSheet(self._base_stylesheet + f"""
                MainWindow {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:1,
                        stop:0 rgba({r}, {g}, {b}, 30),
                        stop:1 rgba(6, 6, 6, 220)
                    );
                }}
                #card {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:1,
                        stop:0 rgba({r}, {g}, {b}, 45),
                        stop:1 rgba({r}, {g}, {b}, 10)
                    );
                    border: 1px solid rgba(255, 255, 255, 15);
                    border-top: 1px solid rgba({r}, {g}, {b}, 120);
                    border-radius: 32px;
                }}
            """)
        except Exception as e:
            print(e)

    def _dominant_color(self, pixmap):
        img = pixmap.scaled(1, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).toImage()
        color = QColor(img.pixel(0, 0))
        # Boost saturation so muted album art still produces a vivid glow
        h, s, v, a = color.getHsvF()
        return QColor.fromHsvF(h, min(s * 2, 1.0), min(v * 1.5, 1.0), a)

    def _make_placeholder_art(self):
        """Vinyl-style gradient placeholder (Fix 4)."""
        size = 420
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Outer gradient disc
        grad = QRadialGradient(size / 2, size / 2, size / 2)
        grad.setColorAt(0.0, QColor(60, 60, 70))
        grad.setColorAt(0.45, QColor(30, 30, 38))
        grad.setColorAt(0.46, QColor(20, 20, 26))
        grad.setColorAt(1.0, QColor(15, 15, 20))
        painter.setBrush(grad)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(10, 10, size - 20, size - 20)

        # Inner label circle
        inner_grad = QRadialGradient(size / 2, size / 2, size * 0.18)
        inner_grad.setColorAt(0.0, QColor(80, 70, 100))
        inner_grad.setColorAt(1.0, QColor(40, 35, 55))
        painter.setBrush(inner_grad)
        r = int(size * 0.18)
        cx, cy = size // 2, size // 2
        painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Center hole
        painter.setBrush(QColor(10, 10, 14))
        hole = 8
        painter.drawEllipse(cx - hole, cy - hole, hole * 2, hole * 2)

        # Music note
        painter.setPen(QColor(255, 255, 255, 60))
        painter.setFont(painter.font())
        from PySide6.QtGui import QFont
        f = QFont("Inter", 64)
        painter.setFont(f)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "♪")

        painter.end()
        return pixmap

    def _apply_glow(self):
        self.glow_anim.stop()
        self.album_art.setGraphicsEffect(None)  # release old effect and its offscreen buffer
        self.glow = QGraphicsDropShadowEffect()
        self.glow.setBlurRadius(80)
        self.glow.setOffset(0, 0)
        self.glow.setColor(self._dominant_color(self.album_art.pixmap()) if self.album_art.pixmap() else Qt.white)
        self.album_art.setGraphicsEffect(self.glow)
        self.glow_anim = QPropertyAnimation(self.glow, b"blurRadius")
        self.glow_anim.setStartValue(80)
        self.glow_anim.setEndValue(140)
        self.glow_anim.setDuration(1800)
        self.glow_anim.setLoopCount(-1)
        self.glow_anim.setEasingCurve(QEasingCurve.InOutSine)
        self.glow_anim.start()

    def load_album_art(self, file_path):
        """Attempts to extract and display album art for various audio formats."""
        self.album_art.setText("⏳")  # loading indicator
        try:
            audio = File(file_path)
            if audio is None:
                self.album_art.setPixmap(self._make_placeholder_art())
                self.album_art.setText("")
                return

            artwork = None
            if hasattr(audio, "pictures") and audio.pictures:
                artwork = audio.pictures[0].data
            elif hasattr(audio, "tags") and audio.tags:
                apic = audio.tags.getall("APIC") if hasattr(audio.tags, "getall") else []
                if apic:
                    artwork = apic[0].data
                else:
                    covr = audio.tags.get("covr")
                    if covr:
                        artwork = bytes(covr[0])

            if artwork:
                pixmap = QPixmap()
                pixmap.loadFromData(artwork)
                self.update_ambient_colors(artwork)
                self._bg_pixmap = pixmap
                self.bg_label.setPixmap(
                    pixmap.scaled(800, 800, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                )
                scaled = pixmap.scaled(420, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.album_art.setPixmap(scaled)
                self.album_art.setText("")
                self._apply_glow()
            else:
                self._bg_pixmap = None
                self.album_art.setPixmap(self._make_placeholder_art())
                self.album_art.setText("")
                self._apply_glow()

        except Exception as e:
            print("Album art error:", e)
            self._bg_pixmap = None
            self.album_art.setPixmap(self._make_placeholder_art())
            self.album_art.setText("")

    def update_player_ui(self):
        duration = self.player.get_duration_ms()
        position = self.player.get_position_ms()
        if duration > 0:
            self.seekbar.setMaximum(duration)
            if not self.seekbar.isSliderDown():
                self.seekbar.setValue(position)
            self.current_time.setText(self.format_time(position))
            self.total_time.setText(self.format_time(duration))
        self.mpris.update_position(position)

    def format_time(self, ms):
        seconds = ms // 1000
        return f"{seconds // 60}:{seconds % 60:02}"

    def seek_to_position(self, position):
        self.player.seek_ms(position)

    # Step 6: Update Open Music to support Queue
    def open_music(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Music Folder")
        if not folder:
            return
        self._settings.setValue("last_folder", folder)
        self._load_folder(folder)

    def _load_folder(self, folder, autoplay=True):
        # Stop any running art loader threads from previous folder
        for loader in getattr(self, '_art_loaders', []):
            loader.quit()
            loader.wait()
        self._art_loaders = []
        self._art_loader_queue = []  # pending (album, path) pairs
        self._art_loaders_running = 0
        self.library.scan_folder(folder)
        self.album_list.clear()
        albums = list(self.library.albums.items())
        for album, songs in albums:
            item = QListWidgetItem(album)
            self.album_list.addItem(item)
            if songs:
                self._art_loader_queue.append((album, songs[0]))
        self._pump_art_loaders()
        if albums:
            self.load_album(albums[0][0], autoplay=autoplay)

    _ART_LOADER_MAX = 4  # max concurrent art loader threads

    def _pump_art_loaders(self):
        while self._art_loaders_running < self._ART_LOADER_MAX and self._art_loader_queue:
            album, path = self._art_loader_queue.pop(0)
            loader = ArtLoader(album, path)
            loader.done.connect(self._set_album_icon)
            loader.done.connect(lambda *_: self._on_art_loader_done())
            loader.start()
            self._art_loaders.append(loader)
            self._art_loaders_running += 1

    def _on_art_loader_done(self):
        self._art_loaders_running -= 1
        self._pump_art_loaders()

    def album_clicked(self, item):
        self.load_album(item.text())

    def track_clicked(self, item):
        i = self.track_list.row(item)
        songs = self.library.albums.get(self.current_album)
        if songs and 0 <= i < len(songs):
            self.queue.current_index = i
            self.play_song(songs[i])

    def _track_display_name(self, file_path):
        try:
            audio = File(file_path, easy=True)
            if audio:
                title = audio.get("title", [None])[0]
                artist = audio.get("artist", [None])[0]
                if title and artist:
                    return f"{artist} — {title}"
                if title:
                    return title
        except Exception:
            pass
        return file_path.split("/")[-1]

    def load_album(self, album_name, autoplay=True):
        self.current_album = album_name
        self.queue.queue = []
        self.queue.current_index = -1
        songs = self.library.albums[album_name]
        self.track_list.clear()
        for song in songs:
            self.queue.add_song(song)
            name = self._track_display_name(song)
            dur = _track_duration(song)
            label = f"{name}  {dur}" if dur else name
            self.track_list.addItem(label)
        if songs and autoplay:
            self.queue.current_index = 0
            self.play_song(self.queue.current_song())
            self._settings.setValue("last_album", album_name)
            self._settings.setValue("last_track_index", 0)

    def next_album(self):
        albums = list(self.library.albums.keys())
        if self.current_album in albums:
            index = albums.index(self.current_album)
            if index < len(albums) - 1:
                self.load_album(albums[index + 1])

    def previous_album(self):
        albums = list(self.library.albums.keys())
        if self.current_album in albums:
            index = albums.index(self.current_album)
            if index > 0:
                self.load_album(albums[index - 1])

    def _open_eq(self):
        dlg = EQDialog(bands=self._eq_bands, parent=self)
        dlg.eq_changed.connect(self._apply_eq)
        dlg.exec()
        self._eq_bands = dlg.current_bands()

    def _apply_eq(self, bands):
        self._eq_bands = bands
        self.player.apply_eq(bands)

    def _set_audio_info(self, text: str):
        self.audio_info_lbl.setText(text)

    def _populate_audio_info(self, file_path):
        try:
            audio = File(file_path)
            if audio is None or not hasattr(audio, "info"):
                self.audio_info_lbl.setText("")
                return
            info = audio.info
            # Codec from class name e.g. mutagen.flac.FLAC → "FLAC"
            codec = type(audio).__name__.upper()
            sr = getattr(info, "sample_rate", None)
            bits = getattr(info, "bits_per_sample", None)
            bitrate = getattr(info, "bitrate", None)
            channels = getattr(info, "channels", None)

            parts = [f"  {codec}  "]
            if bitrate:
                parts.append(f"{bitrate} kbps")
            if sr:
                sr_str = f"{sr // 1000} kHz" if sr % 1000 == 0 else f"{sr / 1000:.1f} kHz"
                parts.append(sr_str)
            if bits:
                parts.append(f"{bits}-bit")
            if channels == 1:
                parts.append("Mono")
            elif channels == 2:
                parts.append("Stereo")
            self.audio_info_lbl.setText("  ·  ".join(p.strip() for p in parts if p.strip()))
        except Exception:
            self.audio_info_lbl.setText("")

    def load_metadata(self, file_path):
        try:
            audio = File(file_path, easy=True)
            if audio is None:
                return
            title = audio.get("title", ["Unknown Title"])[0]
            artist = audio.get("artist", ["Unknown Artist"])[0]
            album = audio.get("album", [""])[0]
        except Exception as e:
            print("Metadata error:", e)
            title = file_path.split("/")[-1]
            artist = "Unknown Artist"
            album = ""
        self.song_title.setText(title)
        self.artist_name.setText(artist)
        self.setWindowTitle(f"{title} — {artist}")
        self.mpris.update_metadata(title, artist, album)
        # Audio info
        self._populate_audio_info(file_path)

    # Step 7: Play Song Method
    def play_song(self, file_path):
        self.player.load(file_path)
        self.load_metadata(file_path)
        self.load_album_art(file_path)
        self.bg_anim.start()
        self.track_list.setCurrentRow(self.queue.current_index)
        self._settings.setValue("last_album", self.current_album)
        self._settings.setValue("last_track_index", self.queue.current_index)
        title = self.song_title._full_text
        artist = self.artist_name.text()
        self._tray.setToolTip(f"{title} — {artist}")
        self._tray_track_action.setText(f"{title} — {artist}")
        self._tray_playpause_action.setText("⏸  Pause")
        self._notify(title, artist)
        self.mpris.update_playback("Playing")

    def _toggle_play(self):
        self.player.pause()

    def _on_mpv_pause_changed(self, is_paused: bool):
        self.play_btn.setText("▶" if is_paused else "⏸")
        self._tray_playpause_action.setText("▶  Play" if is_paused else "⏸  Pause")
        self.mpris.update_playback("Paused" if is_paused else "Playing")

    def _update_play_btn(self, state):
        # kept for safety but no longer the primary update path
        playing = state == QMediaPlayer.PlayingState
        self.play_btn.setText("⏸" if playing else "▶")
        self._tray_playpause_action.setText("⏸  Pause" if playing else "▶  Play")
        self.mpris.update_playback("Playing" if playing else "Paused")

    def _toggle_shuffle(self):
        self.queue.shuffle = not self.queue.shuffle
        self.shuffle_btn.setStyleSheet(
            "color: white; background-color: rgba(255,255,255,25);" if self.queue.shuffle else ""
        )
        self.mpris.update_shuffle(self.queue.shuffle)

    def _toggle_repeat(self):
        self.queue.repeat = not self.queue.repeat
        self.repeat_btn.setStyleSheet(
            "color: white; background-color: rgba(255,255,255,25);" if self.queue.repeat else ""
        )

    def _set_album_icon(self, album_name, pixmap):
        self._art_loaders = [l for l in self._art_loaders if l.isRunning()]
        for i in range(self.album_list.count()):
            item = self.album_list.item(i)
            if item and item.text() == album_name:
                item.setIcon(QIcon(pixmap))
                break

    def _filter_albums(self, text):
        for i in range(self.album_list.count()):
            item = self.album_list.item(i)
            item.setHidden(text.lower() not in item.text().lower())

    def _filter_tracks(self, text):
        for i in range(self.track_list.count()):
            item = self.track_list.item(i)
            item.setHidden(text.lower() not in item.text().lower())

    def _on_track_reordered(self, parent, start, end, dest, row):
        # Qt rowsMoved: item removed from `start`, inserted at `row` in the post-removal model.
        # Convert to final index: if moving down, subtract 1 because removal shifted indices.
        dest_idx = row - 1 if row > start else row
        old_queue = list(self.queue.queue)
        moved = old_queue.pop(start)
        old_queue.insert(dest_idx, moved)
        self.queue.queue = old_queue
        if self.queue.current_index == start:
            self.queue.current_index = dest_idx
        elif start < self.queue.current_index <= dest_idx:
            self.queue.current_index -= 1
        elif dest_idx <= self.queue.current_index < start:
            self.queue.current_index += 1

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.showNormal() if self.isHidden() else self.hide()

    # Step 8: Next / Previous Logic
    def play_next(self):
        # If we're in the online queue, advance it
        if self.player._using_mpv and self._online_queue_index >= 0:
            self._advance_online_queue()
            return
        next_song = self.queue.next_song()
        if next_song:
            self.play_song(next_song)

    def play_previous(self):
        if self.player._using_mpv and self._online_queue_index > 0:
            self._online_queue_index -= 1
            self.online_queue_list.setCurrentRow(self._online_queue_index)
            self._stream_load(self._online_queue[self._online_queue_index], from_queue=True)
            return
        prev_song = self.queue.previous_song()
        if prev_song:
            self.play_song(prev_song)

    def change_volume(self, value):
        vol = value / 100.0
        self.player._volume = vol
        self.player.audio_output.setVolume(vol)
        if self.player._using_mpv and self.player._mpv:
            self.player._mpv.volume = value
        self._settings.setValue("volume", value)
        self.mpris.update_volume(vol)

    def handle_media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.play_next()

    def seek_forward(self):
        self.player.seek_ms(self.player.get_position_ms() + 5000)

    def seek_backward(self):
        self.player.seek_ms(self.player.get_position_ms() - 5000)

    def volume_up(self):
        volume = min(self.player._volume + 0.05, 1.0)
        self.player._volume = volume
        self.player.audio_output.setVolume(volume)
        if self.player._using_mpv and self.player._mpv:
            self.player._mpv.volume = int(volume * 100)
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(int(volume * 100))
        self.volume_slider.blockSignals(False)

    def volume_down(self):
        volume = max(self.player._volume - 0.05, 0.0)
        self.player._volume = volume
        self.player.audio_output.setVolume(volume)
        if self.player._using_mpv and self.player._mpv:
            self.player._mpv.volume = int(volume * 100)
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(int(volume * 100))
        self.volume_slider.blockSignals(False)

    def search_online(self):
        query = self.online_search.text().strip()
        if not query:
            return
        self.online_status.setText("Searching…")
        self.search_btn.setEnabled(False)
        self.online_results.clear()
        self._youtube_results = []
        self._search_worker = YoutubeSearchWorker(query)
        self._search_worker.results_ready.connect(self._on_search_results)
        self._search_worker.error.connect(lambda e: (
            self.online_status.setText(f"Error: {e}"),
            self.search_btn.setEnabled(True)
        ))
        self._search_worker.start()

    def _on_search_results(self, results):
        self.search_btn.setEnabled(True)
        self._youtube_results = results
        if not results:
            self.online_status.setText("No results.")
            return
        self.online_status.setText(f"{len(results)} results")
        for song in results:
            self.online_results.addItem(song["title"])

    def play_online_song(self, item):
        """Single-click on search result → play immediately."""
        index = self.online_results.row(item)
        if not (0 <= index < len(self._youtube_results)):
            return
        video = self._youtube_results[index]
        self._stream_load(video)

    def _online_results_ctx(self, pos):
        item = self.online_results.itemAt(pos)
        if not item:
            return
        index = self.online_results.row(item)
        if not (0 <= index < len(self._youtube_results)):
            return
        video = self._youtube_results[index]
        menu = QMenu(self)
        menu.addAction("▶  Play now", lambda: self._stream_load(video))
        menu.addAction("➕  Add to queue", lambda: self._online_queue_add(video))
        menu.exec(self.online_results.viewport().mapToGlobal(pos))

    def _online_queue_ctx(self, pos):
        item = self.online_queue_list.itemAt(pos)
        if not item:
            return
        index = self.online_queue_list.row(item)
        menu = QMenu(self)
        menu.addAction("▶  Play now", lambda: self._online_queue_play_index(index))
        menu.addAction("✕  Remove", lambda: self._online_queue_remove(index))
        menu.exec(self.online_queue_list.viewport().mapToGlobal(pos))

    def _online_queue_add(self, video):
        self._online_queue.append(video)
        self.online_queue_list.addItem(video["title"])

    def _online_queue_remove(self, index):
        if 0 <= index < len(self._online_queue):
            self._online_queue.pop(index)
            self.online_queue_list.takeItem(index)
            if self._online_queue_index >= index:
                self._online_queue_index -= 1

    def _online_queue_clear(self):
        self._online_queue.clear()
        self._online_queue_index = -1
        self.online_queue_list.clear()

    def _online_queue_play_item(self, item):
        self._online_queue_play_index(self.online_queue_list.row(item))

    def _online_queue_play_index(self, index):
        if 0 <= index < len(self._online_queue):
            self._online_queue_index = index
            self._stream_load(self._online_queue[index], from_queue=True)

    def _on_online_queue_reordered(self, parent, start, end, dest, row):
        dest_idx = row - 1 if row > start else row
        moved = self._online_queue.pop(start)
        self._online_queue.insert(dest_idx, moved)
        if self._online_queue_index == start:
            self._online_queue_index = dest_idx
        elif start < self._online_queue_index <= dest_idx:
            self._online_queue_index -= 1
        elif dest_idx <= self._online_queue_index < start:
            self._online_queue_index += 1

    def _stream_load(self, video, from_queue=False):
        """Start streaming a YouTube video, optionally marking it as queued."""
        if not from_queue:
            # Playing from search — not tracking queue position
            self._online_queue_index = -1
        self.online_status.setText("Loading stream…")
        if hasattr(self, '_stream_worker') and self._stream_worker.isRunning():
            self._stream_worker.cancel()
            self._stream_worker.url_ready.disconnect()
            self._stream_worker.error.disconnect()
        self._stream_worker = StreamUrlWorker(video)
        self._stream_worker.url_ready.connect(self._on_stream_ready)
        self._stream_worker.error.connect(lambda e: self.online_status.setText(f"Error: {e}"))
        self._stream_worker.start()

    def _advance_online_queue(self):
        """Auto-advance to the next item in the online queue."""
        next_idx = self._online_queue_index + 1
        if 0 <= next_idx < len(self._online_queue):
            self._online_queue_index = next_idx
            self.online_queue_list.setCurrentRow(next_idx)
            self._stream_load(self._online_queue[next_idx], from_queue=True)
        else:
            self._online_queue_index = -1

    def _on_stream_ready(self, audio_url, video):
        self.online_status.setText(f"▶ {video['title']}")
        self.player.load_url(audio_url)
        self.play_btn.setText("⏸")
        self._tray_track_action.setText(video["title"])
        self._tray_playpause_action.setText("⏸  Pause")
        self._tray.setToolTip(video["title"])
        self._notify(video["title"], "YouTube")
        self.mpris.update_metadata(video["title"], "YouTube")
        self.mpris.update_playback("Playing")
        self.song_title.setText(video["title"])
        self.artist_name.setText("YouTube")
        self.audio_info_lbl.setText("YouTube Stream")
        self.setWindowTitle(video["title"])
        self.album_art.setPixmap(self._make_placeholder_art())
        self._bg_pixmap = None
        self._apply_glow()
        # Highlight in queue list
        if self._online_queue_index >= 0:
            self.online_queue_list.setCurrentRow(self._online_queue_index)
        # Fetch YouTube thumbnail
        video_id = video["url"].split("v=")[-1].split("&")[0]
        self._thumb_loader = ThumbnailLoader(video_id)
        self._thumb_loader.done.connect(self._on_thumbnail_ready)
        self._thumb_loader.start()

    def _on_thumbnail_ready(self, pixmap, raw_bytes):
        scaled = pixmap.scaled(420, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.album_art.setPixmap(scaled)
        self.album_art.setText("")
        self._bg_pixmap = pixmap
        self.bg_label.setPixmap(
            pixmap.scaled(800, 800, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        )
        self.update_ambient_colors(raw_bytes)
        self._apply_glow()

    def toggle_immersive_mode(self):
        self.immersive_mode = not self.immersive_mode

        if self.immersive_mode:
            self.showFullScreen()
            self.left_tabs.hide()
            self.album_art.setFixedSize(520, 520)
            self.song_title.setStyleSheet("font-size: 48px; font-weight: 700; color: white;")
            self.artist_name.setStyleSheet("font-size: 20px; letter-spacing: 2px; color: rgba(255,255,255,0.55);")
            self.bg_blur.setBlurRadius(140)
            self.bg_anim.setDuration(20000)
            self.play_btn.setFixedSize(80, 80)
            self.play_btn.setStyleSheet(
                self.play_btn.styleSheet() + "border-radius: 40px; font-size: 28px;"
            )
        else:
            self.showNormal()
            self.left_tabs.show()
            self.album_art.setFixedSize(420, 420)
            self.song_title.setStyleSheet("")
            self.artist_name.setStyleSheet("")
            self.bg_blur.setBlurRadius(80)
            self.bg_anim.setDuration(12000)
            self.play_btn.setFixedSize(64, 64)
            self.play_btn.setStyleSheet("")