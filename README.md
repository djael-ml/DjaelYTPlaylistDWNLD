# DjaelYTPlaylistDWNLD

A sleek, portable, and zero-configuration playlist downloader (GUI & CLI) that extracts and saves tracks as high-quality MP3s with ID3v2.3 tags and cover art from **Spotify, SoundCloud, Apple Music, Deezer, YouTube Music, and YouTube**.

Developed by **djael-ml** (GitHub: [github.com/djael-ml](https://github.com/djael-ml)).

---

## Features

- **Clean Step-by-Step Wizard GUI**:
  1. **Source**: Minimalist centered input field with live platform detection, clipboard paste button, and large launch button.
  2. **Options**: Configure target MP3 bitrate (`128` / `192` / `256` / `320` kbps) and parallel worker count (`1`–`6` simultaneous downloads).
  3. **Destination**: Choose the output folder (auto-created if needed, with live MP3 count preview).
  4. **Duplicates Policy**: Choose whether to upgrade lower-bitrate tracks (`upgrade`), overwrite existing tracks (`replace`), or skip all existing tracks (`skip`).
  5. **Download & Summary**: Live progress bar, real-time KPI counters, console logs, immediate cancellation, and one-click retry for failed tracks.
- **Native Multi-Platform Support**:
  - **Spotify**: Works with URLs, URIs, short links (`spotify.link`), raw 22-char IDs, playlists/albums/tracks, optional custom Spotify API credentials, and automatic public embed fallback.
  - **Deezer**: Native public REST API support (playlists, albums, tracks).
  - **Apple Music**: Hydrated JSON / JSON-LD extraction (playlists, albums, songs).
  - **SoundCloud**: Fast `yt-dlp` flat extraction + batch v2 metadata resolution.
  - **YouTube & YouTube Music**: Direct `videoId` bypass (no redundant search).
- **Resilient YouTube Extraction**: Bundles `yt-dlp[default]` (`yt-dlp-ejs`), automatically detects or installs a lightweight QuickJS runtime (~1.8 MB) into `%APPDATA%\DjaelYTPlaylistDWNLD\bin`, and rotates YouTube player clients automatically if `googlevideo.com` returns `HTTP Error 403: Forbidden`.
- **Safe Atomic Upgrades**: Existing MP3s are never deleted before their replacement has been downloaded and tagged.
- **GitHub Auto-Update Checker**: Non-blocking startup check against GitHub Releases (with ETag caching, rate-limit fallback, release notes viewer, and self-updating `.exe` support).
- **Rich ID3v2.3 Metadata**: Embeds Title, Artist, Album Artist, Album, Track Number, Year, and converted JPEG/PNG front-cover artwork for full Windows Explorer & player compatibility.
- **Dual Mode (GUI & CLI)**: Full desktop app and scriptable headless CLI.

---

## Quick Start (Zero-Config Portable `.exe`)

1. Go to the [GitHub Releases](https://github.com/djael-ml/DjaelYTPlaylistDWNLD/releases) page.
2. Download `DjaelYTPlaylistDWNLD.exe` (~25 MB portable executable — FFmpeg is downloaded automatically once on first launch into `%APPDATA%\DjaelYTPlaylistDWNLD\bin`, zero setup required).
3. Double-click and paste any playlist URL!

---

## Development Setup (From Source)

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/djael-ml/DjaelYTPlaylistDWNLD.git
   cd DjaelYTPlaylistDWNLD
   ```

2. **Set up a Virtual Environment**:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Acquire FFmpeg Binaries** *(or ensure `ffmpeg` is installed in your system `PATH`)*:
   ```bash
   python download_ffmpeg.py
   ```

---

## Usage

### 1. GUI Mode (Default)
Double-click the compiled executable or run:
```bash
python main.py
```

*Persistent settings and rotating logs are stored in `%APPDATA%\DjaelYTPlaylistDWNLD\` (automatically migrated from legacy `%TEMP%` configs).*

### 2. CLI Mode (Headless)
```bash
python main.py --playlist "PLAYLIST_URL_OR_ID" --output "C:\Path\To\Music" --quality 320 --workers 3 --overwrite upgrade
```

#### CLI Parameters:
- `--playlist`, `-p`: Playlist/album/track URL or Spotify ID.
- `--output`, `-o`: Destination folder (created automatically if it does not exist).
- `--quality`, `-q`: Target quality in kbps (`320`, `256`, `192`, `128`; default `320`).
- `--workers`, `-w`: Number of parallel download workers (`1`–`8`; default `3`).
- `--overwrite`: Duplicate handling mode — `upgrade` (default), `replace`, or `skip`.
- `--check-update`: Check GitHub for a newer release immediately.
- `--no-update-check`: Skip the automatic startup update check.
- `--version`, `-v`: Show application version.
- `--cli`: Force headless CLI mode.

---

## Running Tests

```bash
python -m unittest discover -s tests -v
```

---

## Building Windows Installer & Portable Package

```bash
.venv\Scripts\pyinstaller.exe DjaelYTPlaylistDWNLD.spec --clean --noconfirm
```
This produces the clean `--onedir` build in `dist/DjaelYTPlaylistDWNLD/` (with Windows PE version metadata and no self-extracting `%TEMP%` dropper heuristics). You can then compile the Windows installer with Inno Setup (`installer.iss`) or let `.github/workflows/release.yml` build, sign (if configured), attest (SLSA), and publish automatically on any Git tag.

---

## License

MIT — see [LICENSE](LICENSE).
