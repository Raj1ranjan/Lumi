from PySide6.QtCore import QThread, Signal
import yt_dlp


class YoutubeSearchWorker(QThread):
    results_ready = Signal(list)
    error = Signal(str)

    def __init__(self, query):
        super().__init__()
        self.query = query

    def run(self):
        try:
            ydl_opts = {"quiet": True, "extract_flat": True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch10:{self.query}", download=False)
                entries = info.get("entries", [])
                results = [
                    {"title": e.get("title", "Unknown"), "url": f"https://www.youtube.com/watch?v={e['id']}"}
                    for e in entries if e.get("id")
                ]
            self.results_ready.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class StreamUrlWorker(QThread):
    url_ready = Signal(str, dict)
    error = Signal(str)

    def __init__(self, video):
        super().__init__()
        self.video = video
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            ydl_opts = {"quiet": True, "format": "bestaudio/best"}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.video["url"], download=False)
                if self._cancelled:
                    return
                # Try requested_formats first (populated when format is selected),
                # then top-level url, then best format by abr/tbr
                url = None
                if info.get("requested_formats"):
                    url = info["requested_formats"][0].get("url")
                if not url:
                    url = info.get("url")
                if not url:
                    formats = info.get("formats", [])
                    audio_fmts = [f for f in formats if f.get("acodec") != "none" and f.get("vcodec") == "none"]
                    if audio_fmts:
                        url = max(audio_fmts, key=lambda f: f.get("abr") or f.get("tbr") or 0)["url"]
                    elif formats:
                        url = formats[-1]["url"]
                if not url:
                    raise ValueError("Could not extract stream URL")
                self.url_ready.emit(url, self.video)
        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e))
