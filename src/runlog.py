"""Shared run logging: mirror log lines to logs/run.log and dump diagnostics."""
import datetime
import os

_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "run.log")


def _ensure_dir():
    os.makedirs(_LOG_DIR, exist_ok=True)


def log(msg):
    """Timestamp a line and append it to logs/run.log. Returns the raw msg."""
    _ensure_dir()
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write("{} {}\n".format(stamp, msg))
    except OSError:
        pass
    return msg


def dump(name, content):
    """Write a diagnostic artifact (e.g. HTML) to logs/ and return its path."""
    _ensure_dir()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(_LOG_DIR, "{}_{}".format(stamp, name))
    try:
        mode = "wb" if isinstance(content, (bytes, bytearray)) else "w"
        with open(path, mode, encoding=None if "b" in mode else "utf-8") as f:
            f.write(content)
    except OSError:
        return None
    return path


def log_dir():
    return _LOG_DIR
