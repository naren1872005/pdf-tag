import sys
import os

# Add the project root to sys.path so we can import from gui, engine, etc.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Session log file first, so start-up messages are captured too
from app.logging_setup import install_logging
install_logging()

from app.application import TaggingApplication

def main():
    if sys.platform == "win32":
        import ctypes
        # Hide the console window that spawns when running via python.exe
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
        
    app = TaggingApplication(sys.argv)
    sys.exit(app.run())

if __name__ == "__main__":
    main()
