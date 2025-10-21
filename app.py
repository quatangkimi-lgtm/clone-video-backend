from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import tempfile, subprocess, os, base64, json, uuid, yt_dlp

app = FastAPI(title="Clone Video Analyzer v4 – cookies + android client")

# CORS cho phép AI Studio / web frontend gọi
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Model xuất kết quả ----
class AnalyzeOut(BaseModel):
    meta: dict
    transcript: List[dict]
    thumbs: List[dict]
    notes: Optional[str] = None


# ---- Hàm tiện ích ----
def run(cmd: list):
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def b64(path: str):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# ---- Hàm tải video bằng yt-dlp ----
def ytdlp_download(url: str, output_path: str, cookies_path: Optional[str] = None):
    ydl_opts = {
        "outtmpl": output_path,
        "format": "bv*+ba/b",
        "quiet": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        # né bot-check bằng Android client
        "extractor_args": {
            "youtube": {
                "player_client": ["android"],
                "skip": ["webpage"],
            }
        },
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 11; SM-G991B Build/RP1A.200720.012) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Mobile Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        },
    }
    if cookies_path:
        ydl_opts["cookiefile"] = cookies_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


# ---- Health ----
@app.get("/health")
async def health():
    return {"status": "ok"}


# ---- Endpoint chính: auto detect ----
@app.post("/analyze_auto", response_model=AnalyzeOut)
async def analyze_auto(
    url: Optional[str] = Form(None),
    video: Optional[UploadFile] = File(None),
    subtitle: Optional[UploadFile] = File(None),
    cookies: Optional[UploadFile] = File(None),
    extract_frames_every_sec: float = Form(10.0),
    limit_frames: int = Form(6),
):
    """
    1-click auto:
    - Nếu có `url` (YouTube) -> tải và phân tích
    - Nếu có `video` upload -> phân tích file
    - Có thể kèm `cookies.txt` (tùy chọn)
    """
    work = tempfile.mkdtemp()
    cookies_path = None

    # ---- Nếu có cookies.txt ----
    if cookies:
        cookies_path = os.path.join(work, "cookies.txt")
        with open(cookies_path, "wb") as f:
            f.write(await cookies.read())

    # ---- NHÁNH YOUTUBE ----
    if url:
        try:
            video_path = os.path.join(work, "video.mp4")
            ytdlp_download(url, video_path, cookies_path)

            # Lấy metadata video
            meta_json = subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", video_path]
            ).decode("utf-8")
            meta_data = json.loads(meta_json or "{}")
            duration = float(meta_data.get("format", {}).get("duration", 0))
            meta = {"source_url": url, "duration_sec": duration}

            # Trích khung hình
            out_dir = os.path.join(work, "frames")
            os.makedirs(out_dir, exist_ok=True)
            run([
                "ffmpeg", "-y", "-i", video_path,
                "-vf", f"fps=1/{extract_frames_every_sec}",
                os.path.join(out_dir, "f_%04d.jpg")
            ])
            thumbs = []
            names = sorted(os.listdir(out_dir))[:max(1, int(limit_frames))]
            for i, name in enumerate(names):
                p = os.path.join(out_dir, name)
                thumbs.append({
                    "time": i * extract_frames_every_sec,
                    "b64": b64(p),
                    "mime": "image/jpeg"
                })

            return AnalyzeOut(meta=meta, transcript=[], thumbs=thumbs, notes="auto: youtube")

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"YouTube error: {e}")

    # ---- NHÁNH FILE UPLOAD ----
    if not video:
        raise HTTPException(status_code=400, detail="Hãy nhập link YouTube hoặc upload video.")

    vid_path = os.path.join(work, "v_tmp.mp4")
    with open(vid_path, "wb") as f:
        f.write(await video.read())

    # Metadata
    try:
        meta_json = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", vid_path]
        ).decode("utf-8")
        meta_probe = json.loads(meta_json or "{}")
        duration = float(meta_probe.get("format", {}).get("duration", 0.0))
    except Exception:
        duration = 0.0
    meta = {"title": video.filename, "duration_sec": duration}

    # Đọc phụ đề (nếu có)
    transcript = []
    if subtitle:
        raw = (await subtitle.read()).decode("utf-8", errors="ignore")
        import re
        blocks = re.split(r"\n\s*\n", raw.strip())
        for b in blocks:
            lines = [l.strip() for l in b.splitlines() if l.strip()]
            if len(lines) >= 2 and "-->" in lines[1]:
                t0, t1 = lines[1].split("-->")
                transcript.append({
                    "t_start": t0.strip(),
                    "t_end": t1.strip(),
                    "text": " ".join(lines[2:])
                })

    # Trích ảnh
    thumbs = []
    out_dir = os.path.join(work, "frames")
    os.makedirs(out_dir, exist_ok=True)
    run([
        "ffmpeg", "-y", "-i", vid_path,
        "-vf", f"fps=1/{extract_frames_every_sec}",
        os.path.join(out_dir, "f_%04d.jpg")
    ])
    names = sorted(os.listdir(out_dir))[:max(1, int(limit_frames))]
    for i, name in enumerate(names):
        p = os.path.join(out_dir, name)
        thumbs.append({"time": i * extract_frames_every_sec, "b64": b64(p), "mime": "image/jpeg"})

    return AnalyzeOut(meta=meta, transcript=transcript, thumbs=thumbs, notes="auto: upload")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080)
