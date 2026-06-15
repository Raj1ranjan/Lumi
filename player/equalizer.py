"""
Parametric Equalizer for Lumi using mpv's lavfi equalizer filter.
Each band: { freq, gain, q }
"""

# ── Headphone presets ────────────────────────────────────────────────────────
# Format: list of (freq_hz, gain_db, q)
PRESETS = {
    "Flat": [],
    "Sennheiser HD 560S": [
        (60,   2.0, 0.7),
        (200, -1.5, 1.0),
        (1000, 0.5, 1.0),
        (3000,-2.5, 1.5),
        (8000, 3.0, 1.0),
    ],
    "Beyerdynamic DT770 Pro": [
        (60,   3.0, 0.7),
        (300, -1.0, 1.0),
        (1000, 0.0, 1.0),
        (6000,-3.5, 2.0),
        (10000,2.0, 1.0),
    ],
    "Moondrop Aria": [
        (80,   1.5, 0.8),
        (250, -0.5, 1.0),
        (1000, 0.0, 1.0),
        (3500,-1.5, 1.5),
        (10000,1.0, 1.0),
    ],
    "Bass Shelf": [
        (60,   4.0, 0.7),
        (120,  2.0, 1.0),
        (1000, 0.0, 1.0),
        (5000, 0.0, 1.0),
        (12000,0.0, 1.0),
    ],
    "Treble Shelf": [
        (60,   0.0, 0.7),
        (1000, 0.0, 1.0),
        (5000, 1.5, 1.0),
        (8000, 2.5, 1.5),
        (12000,3.0, 1.0),
    ],
}

DEFAULT_BANDS = [
        (60,   0.0, 0.7),
        (250,  0.0, 1.0),
        (1000, 0.0, 1.0),
        (4000, 0.0, 1.5),
        (12000,0.0, 1.0),
]


def build_af_string(bands: list) -> str:
    """Build an mpv --af lavfi=[anequalizer=...] filter string from band list."""
    if not bands or all(g == 0 for _, g, _ in bands):
        return ""
    parts = []
    for i, (freq, gain, q) in enumerate(bands):
        # anequalizer format: c{ch}f{freq}w{bandwidth}g{gain}t{type}
        # t=0 = peaking EQ, bandwidth = freq/q
        bw = freq / q
        # Apply to both channels (c0 and c1)
        for ch in (0, 1):
            parts.append(f"c{ch} f={freq} w={bw:.1f} g={gain:.2f} t=0")
    return "lavfi=[anequalizer=" + "|".join(parts) + "]"
