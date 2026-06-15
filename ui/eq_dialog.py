from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSlider, QLabel,
    QComboBox, QPushButton, QWidget
)
from PySide6.QtCore import Qt, Signal
from player.equalizer import PRESETS, DEFAULT_BANDS


class BandWidget(QWidget):
    changed = Signal()

    def __init__(self, freq, gain=0.0, q=1.0):
        super().__init__()
        self.freq = freq
        self.q = q
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignHCenter)

        self.slider = QSlider(Qt.Vertical)
        self.slider.setRange(-120, 120)   # ±12 dB in 0.1 steps
        self.slider.setValue(int(gain * 10))
        self.slider.setFixedHeight(140)
        self.slider.setFixedWidth(30)

        self.gain_lbl = QLabel(f"{gain:+.1f}")
        self.gain_lbl.setAlignment(Qt.AlignCenter)
        self.gain_lbl.setStyleSheet("font-size: 10px; color: rgba(255,255,255,0.6);")

        freq_lbl = QLabel(f"{freq if freq < 1000 else f'{freq//1000}k'} Hz" if freq < 1000 else f"{freq//1000}kHz")
        freq_lbl.setAlignment(Qt.AlignCenter)
        freq_lbl.setStyleSheet("font-size: 10px; color: rgba(255,255,255,0.4);")

        layout.addWidget(self.gain_lbl, alignment=Qt.AlignHCenter)
        layout.addWidget(self.slider, alignment=Qt.AlignHCenter)
        layout.addWidget(freq_lbl, alignment=Qt.AlignHCenter)

        self.slider.valueChanged.connect(self._on_change)

    def _on_change(self, v):
        g = v / 10.0
        self.gain_lbl.setText(f"{g:+.1f}")
        self.changed.emit()

    def gain(self):
        return self.slider.value() / 10.0

    def set_gain(self, g):
        self.slider.blockSignals(True)
        self.slider.setValue(int(g * 10))
        self.gain_lbl.setText(f"{g:+.1f}")
        self.slider.blockSignals(False)


class EQDialog(QDialog):
    eq_changed = Signal(list)   # emits list of (freq, gain, q)

    def __init__(self, bands=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Parametric Equalizer")
        self.setMinimumWidth(420)
        self.setStyleSheet("""
            QDialog { background: #111; color: white; }
            QLabel  { color: white; }
            QPushButton {
                background: rgba(255,255,255,12); border: 1px solid rgba(255,255,255,15);
                border-radius: 8px; padding: 5px 12px; color: rgba(255,255,255,0.85);
            }
            QPushButton:hover { background: rgba(255,255,255,22); }
            QComboBox {
                background: rgba(255,255,255,10); border: 1px solid rgba(255,255,255,15);
                border-radius: 6px; padding: 4px 8px; color: white;
            }
            QComboBox QAbstractItemView { background: #1a1a1a; color: white; }
            QSlider::groove:vertical { width: 4px; background: rgba(255,255,255,15); border-radius: 2px; }
            QSlider::handle:vertical { background: white; width: 12px; height: 12px; margin: 0 -4px; border-radius: 6px; }
            QSlider::sub-page:vertical { background: rgba(255,255,255,15); border-radius: 2px; }
            QSlider::add-page:vertical { background: rgba(255,255,255,40); border-radius: 2px; }
        """)

        if bands is None:
            bands = DEFAULT_BANDS

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Preset row
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self.preset_box = QComboBox()
        self.preset_box.addItems(PRESETS.keys())
        self.preset_box.currentTextChanged.connect(self._load_preset)
        preset_row.addWidget(self.preset_box, 1)
        reset_btn = QPushButton("Reset")
        reset_btn.clicked.connect(self._reset)
        preset_row.addWidget(reset_btn)
        layout.addLayout(preset_row)

        # Band sliders
        self._bands_meta = [(f, q) for f, _, q in bands]
        bands_row = QHBoxLayout()
        bands_row.setSpacing(8)
        self._band_widgets = []
        for freq, gain, q in bands:
            bw = BandWidget(freq, gain, q)
            bw.changed.connect(self._emit)
            self._band_widgets.append(bw)
            bands_row.addWidget(bw)
        layout.addLayout(bands_row)

        # 0 dB reference line label
        ref_lbl = QLabel("0 dB ──────────────────────────────────────")
        ref_lbl.setStyleSheet("font-size: 9px; color: rgba(255,255,255,0.2);")
        layout.addWidget(ref_lbl, alignment=Qt.AlignHCenter)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _emit(self):
        self.eq_changed.emit(self._current_bands())

    def _current_bands(self):
        return [(f, bw.gain(), q) for (f, q), bw in zip(self._bands_meta, self._band_widgets)]

    def _load_preset(self, name):
        preset = PRESETS.get(name, [])
        for i, bw in enumerate(self._band_widgets):
            gain = preset[i][1] if i < len(preset) else 0.0
            bw.set_gain(gain)
        self._emit()

    def _reset(self):
        self.preset_box.setCurrentText("Flat")
        for bw in self._band_widgets:
            bw.set_gain(0.0)
        self._emit()

    def current_bands(self):
        return self._current_bands()
