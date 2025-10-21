from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import tempfile, subprocess, os, base64, json, uuid, shutil
import yt_dlp

app = FastAPI(title="Clone Video – Analyzer")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------- Utils ----------
def run(cmd: list):
    subprocess.run(cmd, check=True)

def b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def ffprobe_duration(path: str) -> float:
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", path
        ]).decode("utf-8")
        j = json.loads(out or "{}")
        return float(j.get("format", {}).get("duration", 0.0))
    except Exception:
        return 0.0

def parse_subs(raw: str) -> List[dict]:
    import re
    items = []
    blocks = re.split(r"\n\s*\n", raw.strip())
    for b in blocks:
        lines = [l.strip() for l in b.splitlines() if l.strip()]
        if len(lines) >= 2 and "-->" in lines[1]:
            t0, t1 = [s.strip() for s in lines[1].split("-->")]
            items.append({"t_start": t0, "t_end": t1, "text": " ".join(lines[2:])})
    return items

# --------- Schemas ----------
class AnalyzeOut(BaseModel):
    meta: dict
    transcript: List[dict]
    thumbs: List[dict]
    notes: Optional[str] = None

# --------- Health ----------
@app.get("/health")
async def health():
    return {"status": "ok"}

# --------- /analyze_upload ----------
@app.post("/analyze_upload", response_model=AnalyzeOut)
async def analyze_upload(
    video: UploadFile = File(...),
    subtitle: Optional[UploadFile] = File(None),
    extract_frames_every_sec: float = Form(10.0),
    limit_frames: int = Form(6)
):
    work = tempfile.mkdtemp()
    vid_path = os.path.join(work, f"v_{uuid.uuid4()}.mp4")
    try:
        with open(vid_path, "wb") as f:
            f.write(await video.read())

        meta = {"title": video.filename, "duration_sec": ffprobe_duration(vid_path)}

        transcript: List[dict] = []
        notes = "subs=none"
        if subtitle is not None:
            raw = (await subtitle.read()).decode("utf-8", errors="ignore")
            transcript = parse_subs(raw)
            notes = "subs=provided"

        out_dir = os.path.join(work, "frames")
        os.makedirs(out_dir, exist_ok=True)
        run(["ffmpeg", "-y", "-i", vid_path,
             "-vf", f"fps=1/{extract_frames_every_sec}",
             os.path.join(out_dir, "f_%04d.jpg")])

        thumbs = []
        names = sorted(os.listdir(out_dir))[:max(1, int(limit_frames))]
        for i, name in enumerate(names):
            p = os.path.join(out_dir, name)
            thumbs.append({"time": i * extract_frames_every_sec, "b64": b64(p), "mime": "image/jpeg"})

        return AnalyzeOut(meta=meta, transcript=transcript, thumbs=thumbs, notes=notes)
    finally:
        shutil.rmtree(work, ignore_errors=True)

# --------- helper to download youtube with optional cookies ----------
def ytdlp_download(url: str, output_path: str, cookies_txt: Optional[str] = None):
    tmp = None
    try:
        ydl_opts = {
            "outtmpl": output_path,
            "format": "bv*+ba/b",
            "quiet": True,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            },
            "extractor_args": {"youtube": {"player_client": ["android"]}},
        }
        if cookies_txt:
            tmp = tempfile.mkdtemp()
            cookiefile = os.path.join(tmp, "cookies.txt")
            with open(cookiefile, "w", encoding="utf-8") as f:
                f.write(cookies_txt)
            ydl_opts["cookiefile"] = cookiefile

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

# --------- /analyze_url ----------
@app.post("/analyze_url", response_model=AnalyzeOut)
async def analyze_url(
    url: str = Form(...),
    extract_frames_every_sec: float = Form(10.0),
    limit_frames: int = Form(6),
    cookies_txt: Optional[str] = Form(None)
):
    work = tempfile.mkdtemp()
    vid_path = os.path.join(work, "video.mp4")
    try:
        ytdlp_download(url, vid_path, cookies_txt)

        meta = {"source_url": url, "duration_sec": ffprobe_duration(vid_path)}

        out_dir = os.path.join(work, "frames")
        os.makedirs(out_dir, exist_ok=True)
        run(["ffmpeg", "-y", "-i", vid_path,
             "-vf", f"fps=1/{extract_frames_every_sec}",
             os.path.join(out_dir, "f_%04d.jpg")])

        thumbs = []
        names = sorted(os.listdir(out_dir))[:max(1, int(limit_frames))]
        for i, name in enumerate(names):
            p = os.path.join(out_dir, name)
            thumbs.append({"time": i * extract_frames_every_sec, "b64": b64(p), "mime": "image/jpeg"})

        note = "youtube+cookies" if cookies_txt else "youtube"
        return AnalyzeOut(meta=meta, transcript=[], thumbs=thumbs, notes=note)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"YouTube error: {e}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

# --------- /analyze_auto (1-click) ----------
@app.post("/analyze_auto", response_model=AnalyzeOut)
async def analyze_auto(
    url: Optional[str] = Form(None),
    video: Optional[UploadFile] = File(None),
    subtitle: Optional[UploadFile] = File(None),
    extract_frames_every_sec: float = Form(10.0),
    limit_frames: int = Form(6),
    cookies_txt: Optional[str] = Form(None)
):
    if url:
        return await analyze_url(
            url=url,
            extract_frames_every_sec=extract_frames_every_sec,
            limit_frames=limit_frames,
            cookies_txt=cookies_txt
        )

    if not video:
        raise HTTPException(status_code=400, detail="Hãy nhập link YouTube hoặc upload video.")

    return await analyze_upload(
        video=video,
        subtitle=subtitle,
        extract_frames_every_sec=extract_frames_every_sec,
        limit_frames=limit_frames
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080)
