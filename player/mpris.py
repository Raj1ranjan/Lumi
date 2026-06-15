"""
MPRIS2 D-Bus service for Lumi.
Exposes org.mpris.MediaPlayer2 and org.mpris.MediaPlayer2.Player.
"""
import threading

try:
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib
    _DBUS_AVAILABLE = True
except ImportError:
    _DBUS_AVAILABLE = False

    # Stub so class-level decorators don't raise NameError
    class _dbus_stub:
        class service:
            @staticmethod
            def method(*a, **kw):
                return lambda f: f
            @staticmethod
            def signal(*a, **kw):
                return lambda f: f

    dbus = _dbus_stub()

MPRIS_BUS_NAME  = "org.mpris.MediaPlayer2.lumi"
OBJECT_PATH     = "/org/mpris/MediaPlayer2"
ROOT_IFACE      = "org.mpris.MediaPlayer2"
PLAYER_IFACE    = "org.mpris.MediaPlayer2.Player"
PROPS_IFACE     = "org.freedesktop.DBus.Properties"


def _make_metadata(title="", artist="", album=""):
    if not _DBUS_AVAILABLE:
        return {}
    return dbus.Dictionary({
        "mpris:trackid": dbus.ObjectPath("/org/lumi/track/1"),
        "xesam:title":   dbus.String(title),
        "xesam:artist":  dbus.Array([dbus.String(artist)], signature="s"),
        "xesam:album":   dbus.String(album),
    }, signature="sv")


_ServiceBase = dbus.service.Object if _DBUS_AVAILABLE else object


class _MprisService(_ServiceBase):
    def __init__(self, bus, ctrl):
        super().__init__(bus, OBJECT_PATH)
        self._ctrl = ctrl

    # ── org.freedesktop.DBus.Properties ──────────────────────────────────────

    @dbus.service.method(PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        return self._all_props(interface)[prop]

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self._all_props(interface)

    @dbus.service.method(PROPS_IFACE, in_signature="ssv")
    def Set(self, interface, prop, value):
        if interface == PLAYER_IFACE and prop == "Volume":
            self._ctrl.on_set_volume(float(value))

    @dbus.service.signal(PROPS_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    def _all_props(self, iface):
        c = self._ctrl
        if iface == ROOT_IFACE:
            return dbus.Dictionary({
                "CanQuit":             dbus.Boolean(True),
                "CanRaise":            dbus.Boolean(True),
                "HasTrackList":        dbus.Boolean(False),
                "Identity":            dbus.String("Lumi"),
                "SupportedUriSchemes": dbus.Array(["file", "https"], signature="s"),
                "SupportedMimeTypes":  dbus.Array(["audio/mpeg", "audio/flac", "audio/x-wav"], signature="s"),
            }, signature="sv")
        if iface == PLAYER_IFACE:
            return dbus.Dictionary({
                "PlaybackStatus": dbus.String(c.playback_status),
                "LoopStatus":     dbus.String("None"),
                "Shuffle":        dbus.Boolean(c.shuffle),
                "Volume":         dbus.Double(c.volume),
                "Position":       dbus.Int64(c.position_us),
                "Rate":           dbus.Double(1.0),
                "MinimumRate":    dbus.Double(1.0),
                "MaximumRate":    dbus.Double(1.0),
                "Metadata":       c.metadata,
                "CanGoNext":      dbus.Boolean(True),
                "CanGoPrevious":  dbus.Boolean(True),
                "CanPlay":        dbus.Boolean(True),
                "CanPause":       dbus.Boolean(True),
                "CanSeek":        dbus.Boolean(True),
                "CanControl":     dbus.Boolean(True),
            }, signature="sv")
        return dbus.Dictionary({}, signature="sv")

    # ── org.mpris.MediaPlayer2 ────────────────────────────────────────────────

    @dbus.service.method(ROOT_IFACE)
    def Raise(self):
        self._ctrl.on_raise()

    @dbus.service.method(ROOT_IFACE)
    def Quit(self):
        self._ctrl.on_quit()

    # ── org.mpris.MediaPlayer2.Player ─────────────────────────────────────────

    @dbus.service.method(PLAYER_IFACE)
    def PlayPause(self): self._ctrl.on_play_pause()

    @dbus.service.method(PLAYER_IFACE)
    def Play(self): self._ctrl.on_play()

    @dbus.service.method(PLAYER_IFACE)
    def Pause(self): self._ctrl.on_pause()

    @dbus.service.method(PLAYER_IFACE)
    def Stop(self): self._ctrl.on_stop()

    @dbus.service.method(PLAYER_IFACE)
    def Next(self): self._ctrl.on_next()

    @dbus.service.method(PLAYER_IFACE)
    def Previous(self): self._ctrl.on_previous()

    @dbus.service.method(PLAYER_IFACE, in_signature="x")
    def Seek(self, offset_us): self._ctrl.on_seek(int(offset_us))

    @dbus.service.method(PLAYER_IFACE, in_signature="ox")
    def SetPosition(self, track_id, position_us): self._ctrl.on_set_position(int(position_us))

    @dbus.service.signal(PLAYER_IFACE, signature="x")
    def Seeked(self, position_us):
        pass

    def emit_props(self, changed: dict):
        d = dbus.Dictionary({dbus.String(k): v for k, v in changed.items()}, signature="sv")
        self.PropertiesChanged(PLAYER_IFACE, d, dbus.Array([], signature="s"))


class MprisController:
    def __init__(self):
        self.playback_status = "Stopped"
        self.shuffle   = False
        self.volume    = 1.0
        self.position_us = 0
        self.metadata  = dbus.Dictionary({}, signature="sv") if _DBUS_AVAILABLE else {}
        self._service  = None

        # Callbacks set by MainWindow
        self.cb_play_pause   = None
        self.cb_play         = None
        self.cb_pause        = None
        self.cb_stop         = None
        self.cb_next         = None
        self.cb_previous     = None
        self.cb_seek         = None  # offset_us
        self.cb_set_position = None  # position_us
        self.cb_set_volume   = None  # 0.0–1.0
        self.cb_raise        = None
        self.cb_quit         = None

    def start(self):
        if not _DBUS_AVAILABLE:
            print("[MPRIS] dbus/gi not available — MPRIS disabled")
            return
        self.metadata = _make_metadata()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        bus.request_name(MPRIS_BUS_NAME)
        self._service = _MprisService(bus, self)
        GLib.MainLoop().run()

    # ── Qt → MPRIS state updates ─────────────────────────────────────────────

    def update_playback(self, status: str):
        self.playback_status = status
        if _DBUS_AVAILABLE:
            self._emit({"PlaybackStatus": dbus.String(status)})

    def update_metadata(self, title="", artist="", album=""):
        if not _DBUS_AVAILABLE:
            return
        self.metadata = _make_metadata(title, artist, album)
        self._emit({"Metadata": self.metadata})

    def update_volume(self, vol: float):
        self.volume = vol
        if _DBUS_AVAILABLE:
            self._emit({"Volume": dbus.Double(vol)})

    def update_position(self, ms: int):
        self.position_us = ms * 1000

    def update_shuffle(self, shuffle: bool):
        self.shuffle = shuffle
        if _DBUS_AVAILABLE:
            self._emit({"Shuffle": dbus.Boolean(shuffle)})

    def _emit(self, changed: dict):
        if self._service and _DBUS_AVAILABLE:
            GLib.idle_add(self._service.emit_props, changed)

    # ── D-Bus → Qt ───────────────────────────────────────────────────────────

    def on_play_pause(self):   self.cb_play_pause   and self.cb_play_pause()
    def on_play(self):         self.cb_play         and self.cb_play()
    def on_pause(self):        self.cb_pause        and self.cb_pause()
    def on_stop(self):         self.cb_stop         and self.cb_stop()
    def on_next(self):         self.cb_next         and self.cb_next()
    def on_previous(self):     self.cb_previous     and self.cb_previous()
    def on_seek(self, off_us): self.cb_seek         and self.cb_seek(off_us)
    def on_set_position(self, pos_us): self.cb_set_position and self.cb_set_position(pos_us)
    def on_set_volume(self, v):        self.cb_set_volume   and self.cb_set_volume(v)
    def on_raise(self):        self.cb_raise        and self.cb_raise()
    def on_quit(self):         self.cb_quit         and self.cb_quit()
