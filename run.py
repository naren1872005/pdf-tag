import sys
import os

# Ensure the root directory (where run.py lives) is in sys.path
if getattr(sys, 'frozen', False):
    # Running in a PyInstaller bundle
    base_path = sys._MEIPASS
else:
    # Running in normal Python environment
    base_path = os.path.dirname(os.path.abspath(__file__))

if base_path not in sys.path:
    sys.path.insert(0, base_path)

# Session log file first, so start-up messages are captured too
from app.logging_setup import install_logging
install_logging()

from app.application import TaggingApplication

import datetime


def main():
    # Hide the blank console window on Windows
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
        except Exception:
            pass
            
    app = TaggingApplication(sys.argv)
    sys.exit(app.run())

if __name__ == "__main__":
    main()
