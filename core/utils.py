import os
import sys
import json
import re
import shutil
import logging
import tempfile
import threading
from logging.handlers import RotatingFileHandler
from mutagen.mp3 import MP3

from core.version import __app_name__

_ffmpeg_install_lock = threading.Lock()

DEFAULT_CONFIG = {
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "output_directory": "",
    "preferred_quality": "320",
    "max_workers": 3,
    "overwrite_mode": "upgrade",  # "upgrade" | "replace" | "skip"
    "theme": "Dark",
    "auto_check_updates": True,
    "last_update_check": 0.0,
    "update_etag": "",
    "skipped_version": "",
}


def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def get_app_data_dir():
    """Return a persistent per-user application data directory."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    app_dir = os.path.join(base, __app_name__)
    os.makedirs(app_dir, exist_ok=True)
    return app_dir


def get_config_path():
    """Get path to persistent config.json in user AppData directory."""
    return os.path.join(get_app_data_dir(), "config.json")


def _get_legacy_temp_config_path():
    return os.path.join(tempfile.gettempdir(), "DjaelYTPlaylistDWNLD_config.json")


def setup_logging():
    """Configure rotating file logging inside the user AppData directory."""
    logger = logging.getLogger(__app_name__)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    try:
        log_dir = os.path.join(get_app_data_dir(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(log_dir, "app.log"),
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except Exception:
        logger.addHandler(logging.NullHandler())
    return logger


def load_config():
    """
    Load settings from persistent config.json, migrating from legacy %TEMP% if needed,
    and merging with DEFAULT_CONFIG so newly added keys always exist.
    """
    config = dict(DEFAULT_CONFIG)
    path = get_config_path()

    # One-time migration from legacy %TEMP% location
    if not os.path.exists(path):
        legacy_path = _get_legacy_temp_config_path()
        if os.path.exists(legacy_path):
            try:
                with open(legacy_path, "r", encoding="utf-8") as f:
                    legacy_data = json.load(f)
                if isinstance(legacy_data, dict):
                    config.update(legacy_data)
                    save_config(config)
                    return config
            except Exception:
                pass

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                config.update(loaded)
        except Exception:
            pass

    return config


def save_config(config):
    """Atomically save settings to persistent config.json."""
    path = get_config_path()
    tmp_path = f"{path}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False


def find_ffmpeg():
    """
    Locate FFmpeg binary directory or executable.
    Checks:
      1. Bundled PyInstaller resource (_MEIPASS) or project root
      2. Next to the frozen executable
      3. Persistent AppData bin directory (%APPDATA%\\DjaelYTPlaylistDWNLD\\bin)
      4. System PATH
    Returns (ffmpeg_exe_path, ffmpeg_dir) or (None, None) if not found.
    """
    binary_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    local_exe = get_resource_path(binary_name)
    if os.path.isfile(local_exe):
        return local_exe, os.path.dirname(local_exe)

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        sibling_exe = os.path.join(exe_dir, binary_name)
        if os.path.isfile(sibling_exe):
            return sibling_exe, exe_dir

    appdata_bin = os.path.join(get_app_data_dir(), "bin")
    appdata_exe = os.path.join(appdata_bin, binary_name)
    if os.path.isfile(appdata_exe):
        return appdata_exe, appdata_bin

    path_exe = shutil.which("ffmpeg")
    if path_exe and os.path.isfile(path_exe):
        return path_exe, os.path.dirname(path_exe)

    return None, None


def ensure_ffmpeg(log_cb=None, progress_cb=None):
    """
    Ensure FFmpeg is available (thread-safe). If not found locally/bundled/PATH,
    automatically downloads and extracts ffmpeg.exe & ffprobe.exe into
    %APPDATA%\\DjaelYTPlaylistDWNLD\\bin once so the user never sees an error.
    Returns (ffmpeg_exe_path, ffmpeg_dir).
    """
    ffmpeg_exe, ffmpeg_dir = find_ffmpeg()
    if ffmpeg_exe:
        return ffmpeg_exe, ffmpeg_dir

    with _ffmpeg_install_lock:
        ffmpeg_exe, ffmpeg_dir = find_ffmpeg()
        if ffmpeg_exe:
            return ffmpeg_exe, ffmpeg_dir

        if sys.platform != "win32":
            raise FileNotFoundError(
                "FFmpeg is not installed. Please install it via your package manager (e.g. apt/brew install ffmpeg)."
            )

        import zipfile
        import requests

        dest_dir = os.path.join(get_app_data_dir(), "bin")
        os.makedirs(dest_dir, exist_ok=True)
        zip_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        zip_path = os.path.join(dest_dir, "ffmpeg-release-essentials.zip")
        targets = {"ffmpeg.exe", "ffprobe.exe"}
        extracted = set()

        if log_cb:
            log_cb("[FFMPEG] Installation automatique de FFmpeg (une seule fois)...")
        if progress_cb:
            progress_cb(0.02, "Installation automatique de FFmpeg...")

        try:
            with requests.get(zip_url, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                with open(zip_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb and total > 0:
                                pct = min(0.95, downloaded / total)
                                progress_cb(pct, f"Installation de FFmpeg ({int(pct * 100)}%)...")

            if log_cb:
                log_cb("[FFMPEG] Extraction de ffmpeg.exe et ffprobe.exe...")
            if progress_cb:
                progress_cb(0.98, "Extraction de FFmpeg...")

            with zipfile.ZipFile(zip_path, "r") as zf:
                for info in zf.infolist():
                    basename = os.path.basename(info.filename).lower()
                    if basename in targets and not info.is_dir():
                        out_path = os.path.join(dest_dir, basename)
                        with zf.open(info) as src, open(out_path, "wb") as dst:
                            while True:
                                buf = src.read(65536)
                                if not buf:
                                    break
                                dst.write(buf)
                        extracted.add(basename)
                        if extracted == targets:
                            break
        finally:
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except OSError:
                    pass

        ffmpeg_exe, ffmpeg_dir = find_ffmpeg()
        if not ffmpeg_exe:
            raise FileNotFoundError("Impossible d'installer FFmpeg automatiquement.")

        if log_cb:
            log_cb("[FFMPEG] FFmpeg prêt !")
        return ffmpeg_exe, ffmpeg_dir


def levenshtein_distance(s1, s2):
    """Compute the Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def get_similarity_ratio(s1, s2):
    """
    Compute similarity ratio between two strings using Levenshtein distance.
    Returns a float between 0.0 and 1.0.
    """
    if not s1 or not s2:
        return 0.0
    s1 = s1.strip().lower()
    s2 = s2.strip().lower()
    if not s1 or not s2:
        return 0.0
    dist = levenshtein_distance(s1, s2)
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    return 1.0 - (dist / max_len)


def clean_text(text):
    """
    Cleans track titles/artists by converting to lowercase, removing common suffixes
    using word boundaries (e.g., remastered, radio edit, feat), normalizing underscores,
    and stripping punctuation.
    """
    if not text:
        return ""
    text = str(text).lower()
    # Remove contents inside parentheses/brackets containing common suffix keywords (with word boundaries)
    text = re.sub(
        r"\s*[\(\[][^\]\)]*\b(remaster(?:ed)?|edit|version|remix|mix|live|mono|stereo|single|feat\.?|ft\.?|lyrics?|official|audio|video|hd|hq)\b[^\]\)]*[\)\]]",
        "",
        text,
    )
    # Remove common standalone suffix patterns after hyphen
    text = re.sub(
        r"\s*-\s*\b(remaster(?:ed)?|radio edit|official video|official audio|video|single version|live|remix|mix)\b.*",
        "",
        text,
    )
    # Replace underscores with spaces before stripping non-word characters
    text = text.replace("_", " ")
    # Remove non-alphanumeric / non-space characters
    text = re.sub(r"[^\w\s]", "", text)
    # Collapse repeated whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def scan_local_mp3s(directory):
    """
    Scans the directory for all .mp3 files and extracts their title, artist, and bitrate.
    Returns a list of dictionaries with:
    {'filepath': str, 'title': str, 'artist': str, 'bitrate': int}
    """
    local_tracks = []
    if not directory or not os.path.isdir(directory):
        return local_tracks

    try:
        entries = os.listdir(directory)
    except OSError:
        return local_tracks

    for filename in entries:
        if not filename.lower().endswith(".mp3"):
            continue
        if filename.startswith("djaelyt_temp_"):
            continue

        filepath = os.path.join(directory, filename)
        if not os.path.isfile(filepath):
            continue

        title = ""
        artist = ""
        bitrate = 0

        try:
            audio = MP3(filepath)
            if audio.info and audio.info.bitrate:
                bitrate = int(round(audio.info.bitrate / 1000))

            if audio.tags:
                if "TIT2" in audio.tags:
                    title = str(audio.tags["TIT2"])
                if "TPE1" in audio.tags:
                    artist = str(audio.tags["TPE1"])
        except Exception:
            pass

        if not title or not artist:
            name_without_ext = os.path.splitext(filename)[0]
            parts = name_without_ext.split(" - ", 1)
            if len(parts) == 2:
                if not artist:
                    artist = parts[0].strip()
                if not title:
                    title = parts[1].strip()
            else:
                if not title:
                    title = name_without_ext.strip()

        local_tracks.append({
            "filepath": filepath,
            "title": title,
            "artist": artist,
            "bitrate": bitrate,
        })

    return local_tracks
