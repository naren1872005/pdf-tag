# GUI_V1/app/logging_setup.py
"""
Session log files for TagPilot (both the built exe and `python app/main.py`).

  * one file per start: Logs/app_YYYYMMDD_HHMMSS.log
      - built exe:  <folder of tagpilot.exe>/Logs
      - from source: <project folder>/Logs
      - if that folder is not writable (e.g. Program Files):
        %LOCALAPPDATA%/TagPilot/Logs
  * everything printed (stdout + stderr), uncaught errors and thread errors
  * every line is scrubbed: the AI provider, its SDK, endpoints and model
    names never appear (app.branding.scrub)
  * the newest 30 log files are kept
"""
import datetime
import glob
import os
import sys
import threading

from app.branding import scrub

_installed = None
KEEP_FILES = 30


def _candidate_dirs():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [
        os.path.join(base, "Logs"),
        os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "TagPilot", "Logs"),
    ]


def _writable_dir():
    for d in _candidate_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write_test")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return d
        except Exception:
            continue
    return None


def get_log_dir():
    return _installed["dir"] if _installed else _writable_dir()


def get_log_file():
    return _installed["file"] if _installed else None


class _Tee:
    """stdout/stderr replacement: scrubbed text to the console (if any) and the file."""

    def __init__(self, stream, fh, lock):
        self.stream = stream
        self.fh = fh
        self.lock = lock
        self.encoding = "utf-8"
        self.errors = "replace"

    def write(self, message):
        if not message:
            return 0
        try:
            text = scrub(str(message))
        except Exception:
            text = str(message)
        with self.lock:
            if self.stream is not None:
                try:
                    self.stream.write(text)
                except Exception:
                    pass
            if self.fh is not None:
                try:
                    self.fh.write(text)
                    self.fh.flush()
                except Exception:
                    pass
        return len(message)

    def flush(self):
        with self.lock:
            for s in (self.stream, self.fh):
                if s is not None:
                    try:
                        s.flush()
                    except Exception:
                        pass

    def isatty(self):
        return False

    def fileno(self):
        if self.stream is not None and hasattr(self.stream, "fileno"):
            return self.stream.fileno()
        raise OSError("no file descriptor")


def _prune(log_dir):
    try:
        files = sorted(glob.glob(os.path.join(log_dir, "app_*.log")), key=os.path.getmtime)
        for f in files[:-KEEP_FILES]:
            try:
                os.remove(f)
            except Exception:
                pass
    except Exception:
        pass


def install_logging():
    """Idempotent. Returns the log file path (or None when no folder is writable)."""
    global _installed
    if _installed:
        return _installed["file"]
    log_dir = _writable_dir()
    fh, log_file = None, None
    if log_dir:
        _prune(log_dir)
        log_file = os.path.join(log_dir, f"app_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
        try:
            fh = open(log_file, "a", encoding="utf-8", errors="replace")
        except Exception:
            fh, log_file = None, None
    lock = threading.RLock()
    sys.stdout = _Tee(sys.__stdout__ if sys.stdout is None else sys.stdout, fh, lock)
    sys.stderr = _Tee(sys.__stderr__ if sys.stderr is None else sys.stderr, fh, lock)

    def _excepthook(exc_type, exc, tb):
        import traceback
        sys.stderr.write("[error] Uncaught exception:\n" + "".join(traceback.format_exception(exc_type, exc, tb)))

    def _thread_excepthook(args):
        import traceback
        sys.stderr.write(f"[error] Uncaught exception in thread {getattr(args.thread, 'name', '?')}:\n"
                         + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    _installed = {"dir": log_dir, "file": log_file}
    print(f"=== TagPilot started {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ===")
    if log_file:
        print(f"[info] Log file: {log_file}")
    return log_file
