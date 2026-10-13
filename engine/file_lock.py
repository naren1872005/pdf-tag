# GUI_V1/engine/file_lock.py
"""
Reliable "is the output PDF open in another program?" handling.

The message "... is open in another program" is shown ONLY when that is really
the case (Windows reports a sharing / lock violation for the file). Other
problems get their own, true message:
  * a short-lived lock (antivirus, Windows Search indexer, OneDrive sync) is
    waited out - the write is retried for a moment before anything is reported
  * a read-only file  -> "is read-only"
  * a missing / non-writable folder, full disk, ... -> the real error text
"""
import os
import stat
import time

# Windows: ERROR_SHARING_VIOLATION = 32, ERROR_LOCK_VIOLATION = 33
_WIN_LOCK_ERRORS = (32, 33)


class FileLockedError(PermissionError):
    """The file is open (locked) in another program."""

    def __init__(self, path, action="try again"):
        self.path = path
        name = os.path.basename(path)
        super().__init__(
            f'"{name}" is open in another program (for example Adobe Acrobat).\n'
            f"Close it there and {action}.")


def _is_lock_error(e):
    # Only Windows locks files for other programs; there the error carries
    # winerror 32/33. Any other error is something else (not "open elsewhere").
    return getattr(e, "winerror", None) in _WIN_LOCK_ERRORS


def is_read_only(path):
    try:
        return os.path.exists(path) and not (os.stat(path).st_mode & stat.S_IWRITE)
    except OSError:
        return False


def is_locked(path, retries=3, delay=0.15):
    """True only when another program holds the file open so it cannot be written.
    A missing file, a read-only file or any other error is NOT reported as locked.
    A lock that disappears within ~0.5 s (indexer / antivirus scan) is ignored."""
    if not path or not os.path.isfile(path):
        return False
    for attempt in range(max(1, retries)):
        try:
            with open(path, "r+b"):
                return False
        except OSError as e:
            if not _is_lock_error(e):
                return False
            if attempt < retries - 1:
                time.sleep(delay)
    return True


def check_writable(path, action="try again"):
    """Raise FileLockedError / PermissionError before starting work on `path`."""
    if is_locked(path):
        raise FileLockedError(path, action)
    if is_read_only(path):
        raise PermissionError(f'"{os.path.basename(path)}" is read-only. Remove the read-only '
                              f"attribute (file Properties) and {action}.")


def explain_write_error(path, err, action="try again"):
    """Turn an OSError raised while writing `path` into the correct exception."""
    if isinstance(err, FileLockedError):
        return err
    if _is_lock_error(err) or is_locked(path):
        return FileLockedError(path, action)
    if is_read_only(path):
        return PermissionError(f'"{os.path.basename(path)}" is read-only. Remove the read-only '
                               f"attribute (file Properties) and {action}.")
    folder = os.path.dirname(os.path.abspath(path))
    if isinstance(err, PermissionError):
        return PermissionError(f'TagPilot has no permission to write "{os.path.basename(path)}" '
                               f"in {folder}.\n({err.strerror or err})")
    return OSError(f'Could not write "{os.path.basename(path)}": {getattr(err, "strerror", None) or err}')


def safe_replace(src, dst, retries=8, delay=0.25, action="try again"):
    """os.replace(src, dst) that waits out short-lived locks and reports a real
    lock only when the destination is really open in another program.
    `src` is removed when the replace finally fails."""
    last = None
    for attempt in range(max(1, retries)):
        try:
            os.replace(src, dst)
            return
        except OSError as e:
            last = e
            # retry only errors a short-lived lock can cause
            if not (isinstance(e, PermissionError) or _is_lock_error(e)):
                break
            if attempt < retries - 1:
                time.sleep(delay)
    try:
        if os.path.exists(src):
            os.remove(src)
    except OSError:
        pass
    raise explain_write_error(dst, last, action)
