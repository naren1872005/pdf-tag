import sys
import os
import subprocess
import webbrowser
import threading
import time
import uvicorn

# Ensure the root directory is in sys.path
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
    pocr_path = os.path.join(base_path, 'paddleocr')
    if os.path.exists(pocr_path) and pocr_path not in sys.path:
        sys.path.insert(0, pocr_path)
else:
    base_path = os.path.dirname(os.path.abspath(__file__))

if base_path not in sys.path:
    sys.path.insert(0, base_path)


def free_port(port: int = 8000):
    """Frees port if an orphaned Python process is holding it on Windows."""
    if os.name != 'nt':
        return
    try:
        out = subprocess.check_output(f"netstat -ano | findstr :{port}", shell=True, text=True, stderr=subprocess.DEVNULL)
        current_pid = os.getpid()
        for line in out.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 5 and "LISTENING" in parts:
                pid = int(parts[-1])
                if pid != current_pid and pid > 0:
                    print(f"Freeing port {port} from stale process (PID {pid})...")
                    subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(0.5)
    except Exception:
        pass


def open_browser():
    time.sleep(1.2)
    webbrowser.open("http://127.0.0.1:8000")


def main():
    free_port(8000)

    print("=" * 60)
    print(" TagPilot Pro - PDF Accessibility Tagging Engine (Web Edition)")
    print(" 100% Offline with Local PaddleOCR & Deterministic PDF/UA-1")
    print("=" * 60)
    print("Server running at: http://127.0.0.1:8000")
    print("Opening browser...")

    threading.Thread(target=open_browser, daemon=True).start()
    try:
        if getattr(sys, 'frozen', False):
            from web.server import app
            uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
        else:
            uvicorn.run("web.server:app", host="127.0.0.1", port=8000, reload=True, log_level="info")
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
