import os
import sys
import zipfile
import requests

# Modern FFmpeg release essentials (gyan.dev latest stable 7.x/8.x Windows 64-bit)
FFMPEG_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def download_and_extract_ffmpeg(dest_dir):
    """
    Downloads the latest stable FFmpeg essentials zip and extracts
    ffmpeg.exe and ffprobe.exe directly into dest_dir.
    """
    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, "ffmpeg-release-essentials.zip")
    targets = {"ffmpeg.exe", "ffprobe.exe"}
    extracted = set()

    print(f"Downloading FFmpeg from {FFMPEG_ZIP_URL}...")
    try:
        with requests.get(FFMPEG_ZIP_URL, stream=True, timeout=60) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(zip_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = int(downloaded * 100 / total)
                            print(f"\rDownloading... {pct}% ({downloaded // (1024 * 1024)} MB)", end="", flush=True)
        print("\nExtracting ffmpeg.exe and ffprobe.exe...")

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            for file_info in zip_ref.infolist():
                basename = os.path.basename(file_info.filename).lower()
                if basename in targets and not file_info.is_dir():
                    out_path = os.path.join(dest_dir, basename)
                    with zip_ref.open(file_info) as src, open(out_path, "wb") as dst:
                        while True:
                            buf = src.read(65536)
                            if not buf:
                                break
                            dst.write(buf)
                    extracted.add(basename)
                    print(f"Extracted {out_path}")
                    if extracted == targets:
                        break

        if extracted != targets:
            missing = targets - extracted
            raise RuntimeError(f"Missing expected binaries in archive: {', '.join(sorted(missing))}")
    finally:
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except OSError:
                pass


def main():
    if sys.platform != "win32":
        print("This helper downloads Windows binaries. On Linux/macOS, install ffmpeg via your package manager (apt/brew).")
        return

    dest_dir = os.path.dirname(os.path.abspath(__file__))
    ffmpeg_path = os.path.join(dest_dir, "ffmpeg.exe")
    ffprobe_path = os.path.join(dest_dir, "ffprobe.exe")

    if os.path.exists(ffmpeg_path) and os.path.exists(ffprobe_path):
        print("ffmpeg.exe and ffprobe.exe are already present in the project folder.")
        return

    try:
        download_and_extract_ffmpeg(dest_dir)
        print("FFmpeg setup completed successfully!")
    except Exception as e:
        print(f"Error while downloading FFmpeg: {e}")
        print("Please download ffmpeg.exe and ffprobe.exe manually and place them in the project root.")
        sys.exit(1)


if __name__ == "__main__":
    main()
