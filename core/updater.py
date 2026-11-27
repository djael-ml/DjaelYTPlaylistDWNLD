"""GitHub Releases auto-update checker and Windows self-updater."""

import os
import re
import sys
import time
import hashlib
import tempfile
from dataclasses import dataclass
from typing import Optional, Callable, Tuple
import requests

from core.version import __version__, GITHUB_OWNER, GITHUB_REPO, GITHUB_REPO_URL

API_LATEST_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
WEB_LATEST_URL = f"{GITHUB_REPO_URL}/releases/latest"
DEFAULT_COOLDOWN_SECONDS = 12 * 3600  # 12 hours


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    release_name: str
    release_notes: str
    release_url: str
    published_at: str
    download_url: Optional[str] = None
    asset_name: Optional[str] = None
    asset_size: int = 0
    asset_sha256: Optional[str] = None
    etag: Optional[str] = None


def parse_semver(version_str: str) -> Optional[Tuple[int, int, int]]:
    """Parse 'v3.0.0', '3.0.0', '2.3.0-beta.1' -> (3, 0, 0). Returns None if invalid."""
    if not version_str:
        return None
    cleaned = version_str.strip().lstrip("vV")
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", cleaned)
    if not match:
        return None
    parts = [int(p) if p is not None else 0 for p in match.groups()]
    return (parts[0], parts[1], parts[2])


def is_newer_version(latest: str, current: str = __version__) -> bool:
    """Compare two SemVer strings safely."""
    v_latest = parse_semver(latest)
    v_current = parse_semver(current)
    if v_latest is None or v_current is None:
        return False
    return v_latest > v_current


def _select_windows_asset(assets: list) -> Tuple[Optional[str], Optional[str], int, Optional[str]]:
    """
    Pick the best Windows binary asset (.exe preferred, .zip fallback).
    Matches dynamically by extension so repo renames (e.g. MusicDL.exe) keep working.
    """
    exe_asset = None
    zip_asset = None
    for asset in assets or []:
        name = asset.get("name", "")
        lower = name.lower()
        if lower.endswith(".exe") and exe_asset is None:
            exe_asset = asset
        elif lower.endswith(".zip") and zip_asset is None:
            zip_asset = asset

    chosen = exe_asset or zip_asset
    if not chosen:
        return None, None, 0, None

    digest = chosen.get("digest") or ""
    sha256 = digest.split(":", 1)[1] if isinstance(digest, str) and digest.startswith("sha256:") else None
    return (
        chosen.get("browser_download_url"),
        chosen.get("name"),
        int(chosen.get("size") or 0),
        sha256,
    )


def _fallback_check_via_redirect(timeout: int = 5) -> Optional[UpdateInfo]:
    """Rate-limit-free fallback using GitHub's /releases/latest HTTP redirect."""
    resp = requests.get(
        WEB_LATEST_URL,
        allow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": f"{GITHUB_REPO}/{__version__}"},
    )
    resp.raise_for_status()
    match = re.search(r"/releases/tag/([^/?#]+)", resp.url)
    if not match:
        return None
    tag = match.group(1)
    if not is_newer_version(tag, __version__):
        return None
    return UpdateInfo(
        current_version=__version__,
        latest_version=tag.lstrip("vV"),
        release_name=f"Release {tag}",
        release_notes="",
        release_url=resp.url,
        published_at="",
    )


def check_for_updates(
    config: dict,
    force: bool = False,
    timeout: int = 6,
) -> Optional[UpdateInfo]:
    """
    Check GitHub Releases for a newer version.
    Returns UpdateInfo if a newer version is available, otherwise None.
    Respects cooldown, ETag caching, and user's skipped_version unless force=True.
    """
    if not force and not config.get("auto_check_updates", True):
        return None

    now = time.time()
    last_check = float(config.get("last_update_check") or 0.0)
    if not force and (now - last_check) < DEFAULT_COOLDOWN_SECONDS:
        return None

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{GITHUB_REPO}/{__version__}",
    }
    if config.get("update_etag") and not force:
        headers["If-None-Match"] = config["update_etag"]

    try:
        resp = requests.get(API_LATEST_URL, headers=headers, timeout=timeout)
        config["last_update_check"] = now

        if resp.status_code == 304:
            return None

        if resp.status_code == 403:
            fallback = _fallback_check_via_redirect(timeout=timeout)
            if fallback and not force and config.get("skipped_version") == fallback.latest_version:
                return None
            return fallback

        resp.raise_for_status()
        data = resp.json()

        if data.get("draft") or data.get("prerelease"):
            return None

        tag_name = data.get("tag_name", "")
        config["update_etag"] = resp.headers.get("ETag", "")

        if not is_newer_version(tag_name, __version__):
            return None

        latest_clean = tag_name.lstrip("vV")
        if not force and config.get("skipped_version") == latest_clean:
            return None

        dl_url, asset_name, asset_size, sha256 = _select_windows_asset(data.get("assets", []))
        return UpdateInfo(
            current_version=__version__,
            latest_version=latest_clean,
            release_name=data.get("name") or tag_name,
            release_notes=(data.get("body") or "").strip(),
            release_url=data.get("html_url") or WEB_LATEST_URL,
            published_at=data.get("published_at") or "",
            download_url=dl_url,
            asset_name=asset_name,
            asset_size=asset_size,
            asset_sha256=sha256,
            etag=resp.headers.get("ETag"),
        )
    except Exception:
        return None


def can_self_update_exe(update: UpdateInfo) -> bool:
    """Return True if update provides a standard Windows Setup installer .exe."""
    name = (update.asset_name or "").lower()
    return (
        sys.platform == "win32"
        and getattr(sys, "frozen", False)
        and bool(update.download_url)
        and name.endswith(".exe")
        and "setup" in name
    )


def download_and_apply_exe_update(
    update: UpdateInfo,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> bool:
    """
    Download a standard Windows Setup installer, verify SHA-256 if provided,
    and launch it cleanly via os.startfile() (no hidden batch scripts or temp self-deletion).
    """
    if not can_self_update_exe(update):
        return False

    installer_path = os.path.join(tempfile.gettempdir(), os.path.basename(update.asset_name))
    hasher = hashlib.sha256()
    downloaded = 0
    try:
        with requests.get(update.download_url, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or update.asset_size or 0)
            with open(installer_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)
                        if progress_cb and total > 0:
                            progress_cb(min(1.0, downloaded / total))

        if update.asset_sha256 and hasher.hexdigest().lower() != update.asset_sha256.lower():
            raise ValueError("SHA-256 verification failed for downloaded installer.")
    except Exception:
        if os.path.exists(installer_path):
            try:
                os.remove(installer_path)
            except OSError:
                pass
        raise

    os.startfile(installer_path)
    return True
