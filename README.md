# yt-dlp
I used yt-dlp as a too make a webpage to download any video over internet (All the cradits goes to yt-dlp original developers "https://github.com/yt-dlp/yt-dlp")


[README.md](https://github.com/user-attachments/files/27972603/README.md)
# ClipForge yt-dlp Downloader

A responsive dark-mode video downloader frontend with a Flask backend powered by the official `yt-dlp` command-line tool and FFmpeg.

## Folder Structure

```text
.
├── backend/
│   ├── app.py
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── downloads/
│   └── .gitkeep
├── .gitignore
└── README.md
```

## Features

- Fetches metadata from any site supported by `yt-dlp`
- Shows thumbnail, title, duration, available quality choices, and available output formats
- Supports MP4, WEBM, MP3, M4A, and WAV
- Uses FFmpeg for merge, conversion, and audio extraction
- Streams real-time progress to the browser with Server-Sent Events
- Prevents duplicate active downloads for the same URL, quality, format, and options
- Handles invalid URLs and backend/tooling errors cleanly
- Optional playlist, subtitles, and thumbnail downloads

## Prerequisites

Install:

- Python 3.10 or newer
- FFmpeg on your system `PATH`
- `yt-dlp` on your system `PATH` or from `requirements.txt`

On Windows, these are common options:

```powershell
winget install yt-dlp.yt-dlp
winget install Gyan.FFmpeg
```

You can also install `yt-dlp` into the Python virtual environment with the requirements file below.

## Setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Configuration

Optional environment variables:

```text
PORT=5000
FLASK_DEBUG=1
CORS_ORIGINS=http://127.0.0.1:5000,http://localhost:5000
YTDLP_BIN=C:\path\to\yt-dlp.exe
FFMPEG_LOCATION=C:\path\to\ffmpeg\bin
```

## API Endpoints

- `GET /health` checks whether `yt-dlp` and FFmpeg are available
- `POST /api/metadata` fetches video metadata and available qualities
- `POST /api/download` starts an async download
- `GET /api/progress/<task_id>` streams progress events
- `GET /api/history` returns recent in-memory download tasks
- `GET /api/file/<filename>` downloads completed files

## Notes

The backend rejects non-http URLs, local/private network URLs, unsupported formats, and unsupported quality values. It never passes user input through a shell; all `yt-dlp` calls use argument arrays.
