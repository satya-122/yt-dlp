import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import quote, urlparse

from flask import Flask, Response, abort, jsonify, request, send_from_directory
from flask_cors import CORS 


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_URL_LENGTH = 2048
STANDARD_QUALITIES = [
    ("144p", 144),
    ("240p", 240),
    ("360p", 360),
    ("480p", 480),
    ("720p", 720),
    ("1080p", 1080),
    ("1440p", 1440),
    ("4K", 2160),
]
QUALITY_HEIGHTS = {label.lower(): height for label, height in STANDARD_QUALITIES}
VIDEO_FORMATS = {"mp4", "webm"}
AUDIO_FORMATS = {"mp3", "m4a", "wav"}
SUPPORTED_FORMATS = VIDEO_FORMATS | AUDIO_FORMATS

PROGRESS_PERCENT_RE = re.compile(r"\[download\]\s+(?P<percent>\d+(?:\.\d+)?)%", re.IGNORECASE)
PROGRESS_SPEED_RE = re.compile(r"\bat\s+(?P<speed>.*?)(?:\s+ETA\b|\s+\(frag\b|$)", re.IGNORECASE)
PROGRESS_ETA_RE = re.compile(r"\bETA\s+(?P<eta>[0-9:]+|Unknown)", re.IGNORECASE)
DESTINATION_RE = re.compile(r"\[download\]\s+Destination:\s+(?P<path>.+)")
MERGE_RE = re.compile(r"\[(Merger|ExtractAudio|VideoConvertor|ThumbnailsConvertor|ffmpeg)\]", re.IGNORECASE)

TASKS = {}
ACTIVE_DOWNLOADS = {}
COMPLETED_DOWNLOADS = {}
TASK_LOCK = threading.Lock()

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
CORS(app)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def json_error(message, status=400, **details):
    payload = {"error": message}
    payload.update(details)
    return jsonify(payload), status


def get_binary(name):
    configured = os.getenv(f"{name.upper().replace('-', '_')}_BIN")
    if configured:
        return configured
    return shutil.which(name)


def validate_url(raw_url):
    # Keep yt-dlp away from file, localhost, and private-network targets.
    if not isinstance(raw_url, str):
        raise ValueError("URL is required.")

    url = raw_url.strip()
    if not url:
        raise ValueError("URL is required.")
    if len(url) > MAX_URL_LENGTH:
        raise ValueError("URL is too long.")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a valid http or https video URL.")

    host = (parsed.hostname or "").lower().strip(".")
    if host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
        raise ValueError("Local network URLs are not allowed.")

    try:
        address = ip_address(host)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise ValueError("Private network URLs are not allowed.")
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise

    return url


def bytes_to_display(size):
    if not size:
        return None
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024
    return None


def duration_to_display(seconds):
    if seconds is None:
        return "Unknown"
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "Unknown"

    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def representative_info(info):
    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
        first_video = next((entry for entry in entries if isinstance(entry, dict) and entry.get("formats")), None)
        return first_video or info
    return info


def best_thumbnail(info):
    thumbnail = info.get("thumbnail")
    if thumbnail:
        return thumbnail

    thumbnails = info.get("thumbnails") or []
    if thumbnails:
        return thumbnails[-1].get("url")
    return None


def format_size_for_height(formats, height):
    matching_sizes = []
    for item in formats:
        item_height = item.get("height")
        if not item_height or item_height > height:
            continue
        size = item.get("filesize") or item.get("filesize_approx")
        if size:
            matching_sizes.append(size)
    return max(matching_sizes) if matching_sizes else None


def summarize_metadata(info):
    details = representative_info(info)
    formats = details.get("formats") or []
    heights = sorted({int(item["height"]) for item in formats if item.get("height")})
    max_height = max(heights) if heights else None
    has_video = any(item.get("vcodec") and item.get("vcodec") != "none" for item in formats)
    has_audio = any(item.get("acodec") and item.get("acodec") != "none" for item in formats)

    qualities = []
    for label, height in STANDARD_QUALITIES:
        available = bool(max_height and height <= max_height)
        if available:
            size = format_size_for_height(formats, height)
            qualities.append(
                {
                    "label": label,
                    "height": height,
                    "available": True,
                    "size": size,
                    "sizeDisplay": bytes_to_display(size),
                }
            )

    available_formats = []
    if has_video:
        available_formats.extend(
            [
                {"value": "mp4", "label": "MP4", "kind": "video"},
                {"value": "webm", "label": "WEBM", "kind": "video"},
            ]
        )
    if has_audio:
        available_formats.extend(
            [
                {"value": "mp3", "label": "MP3", "kind": "audio"},
                {"value": "m4a", "label": "M4A", "kind": "audio"},
                {"value": "wav", "label": "WAV", "kind": "audio"},
            ]
        )

    playlist_count = len(info.get("entries") or []) if info.get("_type") == "playlist" else None
    return {
        "title": details.get("title") or info.get("title") or "Untitled video",
        "duration": details.get("duration"),
        "durationText": duration_to_display(details.get("duration")),
        "thumbnail": best_thumbnail(details) or best_thumbnail(info),
        "uploader": details.get("uploader") or details.get("channel") or info.get("uploader"),
        "platform": details.get("extractor_key") or info.get("extractor_key") or details.get("extractor") or "yt-dlp",
        "webpageUrl": details.get("webpage_url") or info.get("webpage_url"),
        "qualities": qualities,
        "formats": available_formats,
        "playlist": {
            "isPlaylist": info.get("_type") == "playlist",
            "count": playlist_count,
        },
    }


def update_task(task_id, **changes):
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        task.update(changes)
        task["updatedAt"] = utc_now()


def public_task(task):
    safe_task = dict(task)
    safe_task.pop("key", None)
    safe_task.pop("process", None)
    return safe_task


def find_primary_artifact(task_id, expected_format, playlist):
    if playlist:
        task_dir = DOWNLOAD_DIR / task_id
        if not task_dir.exists():
            return None, []
        files = sorted([path for path in task_dir.rglob("*") if path.is_file()], key=lambda item: item.stat().st_mtime, reverse=True)
        return task_dir, files

    candidates = sorted(DOWNLOAD_DIR.glob(f"{task_id}-*"), key=lambda item: item.stat().st_mtime, reverse=True)
    preferred = [path for path in candidates if path.suffix.lower().lstrip(".") == expected_format]
    primary = preferred[0] if preferred else (candidates[0] if candidates else None)
    return primary, candidates


def build_format_selector(output_format, quality_label):
    if output_format in AUDIO_FORMATS:
        return "bestaudio/best"

    height = QUALITY_HEIGHTS.get(quality_label.lower())
    if not height:
        height = 2160

    return (
        f"bestvideo[height<={height}][ext={output_format}]+bestaudio/"
        f"bestvideo[height<={height}]+bestaudio/"
        f"best[height<={height}]/best"
    )


def build_download_command(task_id, url, quality, output_format, playlist, subtitles, thumbnail):
    yt_dlp = get_binary("yt-dlp")
    if not yt_dlp:
        raise RuntimeError("yt-dlp is not installed or is not available on PATH.")

    # Build an argument list instead of a shell string so user input is not shell-expanded.
    output_template = (
        f"{task_id}/%(playlist_index)03d-%(title).180B-%(id)s.%(ext)s"
        if playlist
        else f"{task_id}-%(title).180B-%(id)s.%(ext)s"
    )

    command = [
        yt_dlp,
        "--extractor-args", "youtube:player_client=android",
        "--cookies",
        "cookies.txt",
        "--newline",
        "--no-color",
        "--restrict-filenames",
        "--trim-filenames",
        "180",
        "--paths",
        str(DOWNLOAD_DIR),
        "-o",
        output_template,
        "-f",
        build_format_selector(output_format, quality),
    ]

    ffmpeg_location = os.getenv("FFMPEG_LOCATION")
    if ffmpeg_location:
        command.extend(["--ffmpeg-location", ffmpeg_location])

    if output_format in AUDIO_FORMATS:
        command.extend(["--extract-audio", "--audio-format", output_format, "--audio-quality", "0"])
    else:
        command.extend(["--merge-output-format", output_format])

    if playlist:
        command.extend(["--yes-playlist", "--ignore-errors"])
    else:
        command.append("--no-playlist")

    if subtitles:
        command.extend(["--write-subs", "--write-auto-subs", "--sub-langs", "all,-live_chat", "--convert-subs", "srt"])

    if thumbnail:
        command.extend(["--write-thumbnail", "--convert-thumbnails", "jpg"])

    command.append(url)
    return command


def parse_progress_line(task_id, line):
    clean = line.strip()
    if not clean:
        return

    progress = PROGRESS_PERCENT_RE.search(clean)
    if progress:
        speed_match = PROGRESS_SPEED_RE.search(clean)
        eta_match = PROGRESS_ETA_RE.search(clean)
        speed = " ".join(speed_match.group("speed").split()) if speed_match else None
        eta = eta_match.group("eta") if eta_match else None
        update_task(
            task_id,
            status="running",
            progress=min(float(progress.group("percent")), 99.5),
            speed=speed,
            eta=eta,
            message="Downloading",
        )
        return

    destination = DESTINATION_RE.search(clean)
    if destination:
        update_task(task_id, message="Preparing file", destination=destination.group("path"))
        return

    if MERGE_RE.search(clean):
        update_task(task_id, message="Processing with FFmpeg")
        return

    if "has already been downloaded" in clean:
        update_task(task_id, message="Already downloaded")
        return

    if clean.startswith("[download]") or clean.startswith("[info]"):
        update_task(task_id, message=clean[:180])


def run_download(task_id):
    with TASK_LOCK:
        task = TASKS[task_id]
        url = task["url"]
        quality = task["quality"]
        output_format = task["format"]
        playlist = task["playlist"]
        subtitles = task["subtitles"]
        thumbnail = task["thumbnail"]
        key = task["key"]

    last_lines = []
    process = None
    try:
        command = build_download_command(task_id, url, quality, output_format, playlist, subtitles, thumbnail)
        update_task(task_id, status="running", progress=0, message="Starting download", commandPreview="yt-dlp")

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(BASE_DIR),
        )
        update_task(task_id, process=process)

        for line in process.stdout or []:
            last_lines.append(line.strip())
            last_lines = last_lines[-10:]
            parse_progress_line(task_id, line)

        return_code = process.wait()
        if return_code != 0:
            message = next((line for line in reversed(last_lines) if line), "Download failed.")
            raise RuntimeError(message)

        primary, artifacts = find_primary_artifact(task_id, output_format, playlist)
        artifact_payload = []
        for artifact in artifacts[:50]:
            if artifact.is_file():
                rel = artifact.relative_to(DOWNLOAD_DIR).as_posix()
                artifact_payload.append(
                    {
                        "name": artifact.name,
                        "path": rel,
                        "size": artifact.stat().st_size,
                        "sizeDisplay": bytes_to_display(artifact.stat().st_size),
                        "url": f"/api/file/{quote(rel)}",
                    }
                )

        file_url = None
        output_path = None
        if primary:
            output_path = primary.relative_to(DOWNLOAD_DIR).as_posix()
            if primary.is_file():
                file_url = f"/api/file/{quote(output_path)}"

        update_task(
            task_id,
            status="completed",
            progress=100,
            eta=None,
            speed=None,
            message="Download ready",
            fileUrl=file_url,
            outputPath=output_path,
            artifacts=artifact_payload,
            finishedAt=utc_now(),
        )

        with TASK_LOCK:
            COMPLETED_DOWNLOADS[key] = task_id
    except Exception as exc:
        update_task(
            task_id,
            status="failed",
            progress=0,
            error=str(exc),
            message=str(exc),
            finishedAt=utc_now(),
        )
    finally:
        with TASK_LOCK:
            ACTIVE_DOWNLOADS.pop(key, None)
            task = TASKS.get(task_id)
            if task:
                task.pop("process", None)


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
    "default-src * 'unsafe-inline' 'unsafe-eval' data: blob:;"
)
    return response


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "ytDlpAvailable": bool(get_binary("yt-dlp")),
            "ffmpegAvailable": bool(get_binary("ffmpeg")),
        }
    )


@app.post("/api/metadata")
def metadata():
    payload = request.get_json(silent=True) or {}
    try:
        url = validate_url(payload.get("url"))
        playlist = bool(payload.get("playlist", False))
    except ValueError as exc:
        return json_error(str(exc))

    yt_dlp = get_binary("yt-dlp")
    if not yt_dlp:
        return json_error("yt-dlp is not installed or is not available on PATH.", 503)

    command = [
        yt_dlp,
        "--extractor-args", "youtube:player_client=android",
        "--cookies",
        "cookies.txt",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        "--socket-timeout",
        "20",
    ]
    if playlist:
        command.extend(["--playlist-items", "1"])
    else:
        command.append("--no-playlist")
    command.append(url)

    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        return json_error("Timed out while fetching metadata.", 504)

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "Could not fetch metadata.").strip().splitlines()[-1]
        return json_error(message, 422)

    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return json_error("yt-dlp returned malformed metadata.", 502)

    response = summarize_metadata(info)
    response["rawFormatCount"] = len((representative_info(info).get("formats") or []))
    return jsonify(response)


@app.post("/api/download")
def download():
    payload = request.get_json(silent=True) or {}
    try:
        url = validate_url(payload.get("url"))
    except ValueError as exc:
        return json_error(str(exc))

    quality = str(payload.get("quality") or "720p")
    output_format = str(payload.get("format") or "mp4").lower()
    playlist = bool(payload.get("playlist", False))
    subtitles = bool(payload.get("subtitles", False))
    thumbnail = bool(payload.get("thumbnail", False))

    if output_format not in SUPPORTED_FORMATS:
        return json_error("Unsupported output format.")
    if quality.lower() not in QUALITY_HEIGHTS:
        return json_error("Unsupported quality option.")
    if not get_binary("yt-dlp"):
        return json_error("yt-dlp is not installed or is not available on PATH.", 503)
    if not get_binary("ffmpeg"):
        return json_error("FFmpeg is required for merging, conversion, and audio extraction.", 503)

    duplicate_key = json.dumps(
        {
            "url": url,
            "quality": quality.lower(),
            "format": output_format,
            "playlist": playlist,
            "subtitles": subtitles,
            "thumbnail": thumbnail,
        },
        sort_keys=True,
    )

    with TASK_LOCK:
        existing_id = ACTIVE_DOWNLOADS.get(duplicate_key) or COMPLETED_DOWNLOADS.get(duplicate_key)
        if existing_id and existing_id in TASKS:
            existing = TASKS[existing_id]
            output_path = existing.get("outputPath")
            if existing["status"] != "completed" or not output_path or (DOWNLOAD_DIR / output_path).exists():
                return jsonify({"taskId": existing_id, "duplicate": True, "task": public_task(existing)}), 202

        task_id = uuid.uuid4().hex
        TASKS[task_id] = {
            "id": task_id,
            "key": duplicate_key,
            "url": url,
            "quality": quality,
            "format": output_format,
            "playlist": playlist,
            "subtitles": subtitles,
            "thumbnail": thumbnail,
            "status": "queued",
            "progress": 0,
            "message": "Queued",
            "speed": None,
            "eta": None,
            "fileUrl": None,
            "artifacts": [],
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        ACTIVE_DOWNLOADS[duplicate_key] = task_id

    worker = threading.Thread(target=run_download, args=(task_id,), daemon=True)
    worker.start()
    return jsonify({"taskId": task_id, "duplicate": False, "task": public_task(TASKS[task_id])}), 202


@app.get("/api/progress/<task_id>")
def progress(task_id):
    # Server-Sent Events keep the frontend progress bar current without polling.
    def event_stream():
        while True:
            with TASK_LOCK:
                task = TASKS.get(task_id)
                payload = public_task(task) if task else {"status": "missing", "error": "Task not found."}

            yield f"data: {json.dumps(payload)}\n\n"

            if payload.get("status") in {"completed", "failed", "missing"}:
                break
            time.sleep(0.75)

    return Response(event_stream(), mimetype="text/event-stream")


@app.get("/api/history")
def history():
    with TASK_LOCK:
        tasks = [public_task(task) for task in TASKS.values()]
    tasks.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
    return jsonify({"tasks": tasks[:25]})


@app.get("/api/file/<path:filename>")
def file_download(filename):
    requested = Path(filename)
    if requested.is_absolute() or ".." in requested.parts:
        abort(400)
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True, threaded=True)
