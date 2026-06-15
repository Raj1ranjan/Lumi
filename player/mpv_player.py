import locale
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtCore import QObject, Signal
import mpv


class MPVPlayer(QObject):
    end_of_track = Signal()
    pause_changed = Signal(bool)  # True = paused, False = playing

    def __init__(self):
        super().__init__()
        self._volume = 0.8  # track volume without allocating Qt multimedia objects
        self.audio_output = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(self._volume)

        self._mpv = None
        self._using_mpv = True  # always use mpv; QMediaPlayer kept only for fallback signals

    def _get_mpv(self):
        if self._mpv is None:
            locale.setlocale(locale.LC_NUMERIC, "C")
            self._mpv = mpv.MPV(
                video=False,
                gapless_audio="yes",
                replaygain="track",
                replaygain_clip="yes",
            )

            @self._mpv.event_callback("end-file")
            def _on_end(event):
                reason = getattr(event, "reason", None) or ""
                if reason == "eof":
                    self.end_of_track.emit()

            @self._mpv.property_observer("pause")
            def _on_pause(name, value):
                if value is not None:
                    self.pause_changed.emit(bool(value))

        return self._mpv

    def apply_eq(self, bands: list):
        """Apply parametric EQ bands. bands = [(freq, gain_db, q), ...]"""
        from player.equalizer import build_af_string
        af = build_af_string(bands)
        m = self._get_mpv()
        if af:
            m.af = af
        else:
            m.af = ""

    def load(self, path):
        """Load a local file via mpv for full format support (FLAC, ALAC, AIFF, 24-bit, hi-res)."""
        self._using_mpv = True
        m = self._get_mpv()
        m.play(path)
        m.volume = int(self._volume * 100)

    def load_url(self, url):
        """Stream a URL via mpv (handles YouTube audio streams)."""
        self._using_mpv = True
        self._get_mpv().play(url)
        self._get_mpv().volume = int(self._volume * 100)

    def pause(self):
        if self._using_mpv:
            self._get_mpv().pause = not self._get_mpv().pause
        elif self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def get_position_ms(self):
        if self._using_mpv and self._mpv:
            pos = self._mpv.time_pos
            return int(pos * 1000) if pos is not None else 0
        return self.player.position()

    def get_duration_ms(self):
        if self._using_mpv and self._mpv:
            dur = self._mpv.duration
            return int(dur * 1000) if dur is not None else 0
        return self.player.duration()

    def seek_ms(self, ms):
        if self._using_mpv and self._mpv:
            self._mpv.seek(ms / 1000.0, reference="absolute")
        else:
            self.player.setPosition(ms)

    def stop(self):
        if self._using_mpv and self._mpv:
            self._mpv.stop()
        else:
            self.player.stop()

    def cleanup(self):
        """Call on app exit to terminate mpv."""
        if self._mpv:
            self._mpv.terminate()
            self._mpv = None
