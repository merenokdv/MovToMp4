#!/usr/bin/env python3
"""MovToMp4 — local web UI for converting MOV to MP4 via ffmpeg."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
OUTPUT_DIR = ROOT / "output"
HOST = "127.0.0.1"
PORT = 8765

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError(
            "ffmpeg не найден. Установите: brew install ffmpeg"
        )
    return path


def safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^\w\-а-яА-ЯёЁ ]+", "", stem, flags=re.UNICODE).strip()
    return stem or "video"


def parse_progress(line: str, duration: float) -> float | None:
    if duration <= 0:
        return None
    match = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", line)
    if not match:
        return None
    h, m, s = match.groups()
    current = int(h) * 3600 + int(m) * 60 + float(s)
    return max(0.0, min(99.0, (current / duration) * 100))


def probe_duration(_ffmpeg: str, path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return float(result.stdout.strip() or 0)
    except (ValueError, FileNotFoundError, OSError):
        return 0.0


def build_ffmpeg_cmd(
    ffmpeg: str,
    src: Path,
    dst: Path,
    mode: str,
) -> list[str]:
    base = [ffmpeg, "-y", "-hide_banner", "-i", str(src)]

    if mode == "copy":
        return base + ["-c", "copy", "-movflags", "+faststart", str(dst)]

    if mode == "fast":
        return base + [
            "-c:v",
            "h264_videotoolbox",
            "-b:v",
            "8M",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(dst),
        ]

    # quality — software x264
    return base + [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(dst),
    ]


def run_ffmpeg(cmd: list[str], job_id: str, duration: float) -> tuple[int, str]:
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stderr is not None
    err_lines: list[str] = []
    for line in process.stderr:
        err_lines.append(line)
        progress = parse_progress(line, duration)
        if progress is not None:
            with jobs_lock:
                if jobs[job_id]["status"] == "running":
                    jobs[job_id]["progress"] = round(progress, 1)
    code = process.wait()
    return code, "".join(err_lines[-40:])


def run_conversion(job_id: str, src: Path, original_name: str, mode: str) -> None:
    ffmpeg = require_ffmpeg()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    out_name = f"{safe_stem(original_name)}.mp4"
    dst = OUTPUT_DIR / out_name
    if dst.exists():
        dst = OUTPUT_DIR / f"{safe_stem(original_name)}_{job_id[:8]}.mp4"

    duration = probe_duration(ffmpeg, src)
    modes_to_try = [mode]
    if mode == "copy":
        modes_to_try.append("fast")

    with jobs_lock:
        jobs[job_id].update(
            {
                "status": "running",
                "progress": 1,
                "output": str(dst),
                "filename": dst.name,
                "message": "Конвертация…",
            }
        )

    try:
        last_err = ""
        for attempt, current_mode in enumerate(modes_to_try):
            if attempt > 0:
                with jobs_lock:
                    jobs[job_id].update(
                        {
                            "progress": 5,
                            "message": "Перекодирование…",
                            "mode": current_mode,
                        }
                    )
                if dst.exists():
                    dst.unlink()

            cmd = build_ffmpeg_cmd(ffmpeg, src, dst, current_mode)
            code, last_err = run_ffmpeg(cmd, job_id, duration)
            if code == 0:
                with jobs_lock:
                    jobs[job_id].update(
                        {
                            "status": "done",
                            "progress": 100,
                            "message": "Готово",
                            "mode": current_mode,
                        }
                    )
                return

        raise RuntimeError(last_err.strip() or "ffmpeg завершился с ошибкой")
    except Exception as exc:  # noqa: BLE001
        with jobs_lock:
            jobs[job_id].update(
                {
                    "status": "error",
                    "progress": 0,
                    "message": str(exc),
                }
            )
    finally:
        try:
            src.unlink(missing_ok=True)
            src.parent.rmdir()
        except OSError:
            pass


class Handler(BaseHTTPRequestHandler):
    server_version = "MovToMp4/1.0"

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, data, "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        if path == "/api/health":
            ok = shutil.which("ffmpeg") is not None
            return self._json(
                200,
                {
                    "ok": ok,
                    "ffmpeg": ok,
                    "output": str(OUTPUT_DIR),
                },
            )

        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            with jobs_lock:
                job = jobs.get(job_id)
            if not job:
                return self._json(404, {"error": "Задача не найдена"})
            return self._json(200, job)

        if path.startswith("/api/download/"):
            job_id = path.rsplit("/", 1)[-1]
            with jobs_lock:
                job = jobs.get(job_id)
            if not job or job.get("status") != "done":
                return self._json(404, {"error": "Файл не готов"})
            file_path = Path(job["output"])
            if not file_path.is_file():
                return self._json(404, {"error": "Файл отсутствует"})
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{file_path.name}"',
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/open-output":
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["open", str(OUTPUT_DIR)])
            return self._json(200, {"ok": True, "path": str(OUTPUT_DIR)})

        # static files
        rel = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        rel = unquote(rel)
        target = (STATIC / rel).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
            return self._send(404, b"Not found", "text/plain")

        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(target.suffix.lower(), "application/octet-stream")
        return self._send(200, target.read_bytes(), content_type)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/convert":
            return self._json(404, {"error": "Not found"})

        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return self._json(400, {"error": "Пустой запрос"})

        content_type = self.headers.get("Content-Type", "")
        body = self.rfile.read(length)

        try:
            filename, mode, file_bytes = parse_multipart(content_type, body)
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})

        if not filename.lower().endswith(".mov"):
            return self._json(400, {"error": "Нужен файл .mov"})

        if mode not in {"copy", "fast", "quality"}:
            mode = "copy"

        try:
            require_ffmpeg()
        except RuntimeError as exc:
            return self._json(500, {"error": str(exc)})

        job_id = uuid.uuid4().hex
        tmp_dir = Path(tempfile.mkdtemp(prefix="movtomp4_"))
        src = tmp_dir / filename
        src.write_bytes(file_bytes)

        with jobs_lock:
            jobs[job_id] = {
                "id": job_id,
                "status": "queued",
                "progress": 0,
                "filename": None,
                "output": None,
                "message": "В очереди",
                "source": filename,
                "mode": mode,
            }

        thread = threading.Thread(
            target=run_conversion,
            args=(job_id, src, filename, mode),
            daemon=True,
        )
        thread.start()
        return self._json(200, {"id": job_id})


def parse_multipart(
    content_type: str, body: bytes
) -> tuple[str, str, bytes]:
    if "multipart/form-data" not in content_type:
        raise ValueError("Ожидается multipart/form-data")

    match = re.search(r"boundary=(?P<b>[^;]+)", content_type, re.I)
    if not match:
        raise ValueError("Нет boundary")
    boundary = match.group("b").strip().strip('"').encode()
    parts = body.split(b"--" + boundary)

    filename = ""
    mode = "copy"
    file_bytes: bytes | None = None

    for part in parts:
        if not part or part in (b"--\r\n", b"--"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]

        header_blob, _, content = part.partition(b"\r\n\r\n")
        headers = header_blob.decode("utf-8", errors="replace")
        name_match = re.search(r'name="([^"]+)"', headers)
        if not name_match:
            continue
        name = name_match.group(1)

        if name == "mode":
            mode = content.decode("utf-8", errors="replace").strip()
        elif name == "file":
            fn_match = re.search(r'filename="([^"]*)"', headers)
            filename = fn_match.group(1) if fn_match else "video.mov"
            # strip trailing CRLF that may remain
            if content.endswith(b"\r\n"):
                content = content[:-2]
            file_bytes = content

    if file_bytes is None:
        raise ValueError("Файл не передан")
    return filename, mode, file_bytes


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not STATIC.exists():
        raise SystemExit(f"Папка static не найдена: {STATIC}")

    try:
        require_ffmpeg()
        print("✓ ffmpeg найден")
    except RuntimeError as exc:
        print(f"⚠ {exc}")

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"MovToMp4 → {url}")
    print(f"Готовые файлы: {OUTPUT_DIR}")
    print("Ctrl+C — выход")

    def open_browser() -> None:
        webbrowser.open(url)

    threading.Timer(0.6, open_browser).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка…")
        server.shutdown()


if __name__ == "__main__":
    main()
