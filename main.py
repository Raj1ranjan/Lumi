#!/usr/bin/env python3
import locale
locale.setlocale(locale.LC_NUMERIC, "C")

import sys

from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow


app = QApplication(sys.argv)

window = MainWindow()
window.show()

sys.exit(app.exec())