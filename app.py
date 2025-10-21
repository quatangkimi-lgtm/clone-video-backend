from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import tempfile, subprocess, os, base64, json, uuid

app = FastAPI(title="Clone Video – Analyzer")

# Cho phép AI Studio hoặc web khác gọi trực tiếp
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AnalyzeOut(BaseModel):
    meta: dict
    transcript: List[dict]
    thumbs: List[dict]
    notes: Optional[str] = None

def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/analyze_upload", response_model=AnalyzeOut)
async def analyze_upload(
    video: UploadFile = File(...),
    subtitle: UploadFile = File(None),
    extract_frames_every_sec: float = Form(10.0)
):
    work = tempfile.mkdtemp()
    vid_path = os.path.join(work, f"v_{uuid.uuid4()}.mp4")
    with open(vid_path, "wb") as f:
        f.write(await video.read())

    # 1️⃣ Lấy thông tin video
    meta_json = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", vid_path
    ]).decode("utf-8")
    meta = json.loads(meta_json)
    duration = float(meta["format"].get("duration", 0))
    meta = {"title": video.filename, "duration_sec": duration}

    # 2️⃣ Đọc phụ đề nếu có
    transcript = []
    if subtitle:
        raw = (await subtitle.read()).decode("utf-8", errors="ignore")
        import re
        blocks = re.split(r"\n\s*\n", raw.strip())
        for b in blocks:
            lines = [l.strip() for l in b.splitlines() if l.strip()]
            if len(lines) >= 2 and "-->" in lines[1]:
                t = lines[1]
                t0, t1 = t.split("-->")
                transcript.append({"t_start": t0.strip(), "t_end": t1.strip(), "text": " ".join(lines[2:])})
        notes = "subs=provided"
    else:
        notes = "subs=none"

    # 3️⃣ Trích ảnh keyframe
    thumbs = []
    out_dir = os.path.join(work, "frames")
    os.makedirs(out_dir, exist_ok=True)
    run(["ffmpeg", "-y", "-i", vid_path, "-vf", f"fps=1/{extract_frames_every_sec}", os.path.join(out_dir, "f_%04d.jpg")])
    names = sorted(os.listdir(out_dir))[:6]
    for i, name in enumerate(names):
        p = os.path.join(out_dir, name)
        thumbs.append({"time": i * extract_frames_every_sec, "b64": b64(p)})

    return AnalyzeOut(meta=meta, transcript=transcript, thumbs=thumbs, notes=notes)
from fastapi import HTTPException
from typing import Optional
import yt_dlp, base64, subprocess, tempfile, os, json

@app.post("/analyze_auto", response_model=AnalyzeOut)
async def analyze_auto(
    url: Optional[str] = Form(None),
    video: Optional[UploadFile] = File(None),
    subtitle: Optional[UploadFile] = File(None),
    extract_frames_every_sec: float = Form(10.0),
    limit_frames: int = Form(6)
):
    """
    1-click auto:
    - Nếu có `url` (YouTube) -> tải tạm và phân tích (giống /analyze_url)
    - Ngược lại nếu có `video` -> phân tích upload (giống /analyze_upload)
    - Trả JSON: {meta, transcript, thumbs, notes}
    """
    if url:
        # ---- NHÁNH YOUTUBE (tương tự /analyze_url) ----
        work = tempfile.mkdtemp()
        video_path = os.path.join(work, "video.mp4")
        try:
            ydl_opts = {"outtmpl": video_path, "format": "bestvideo+bestaudio/best", "quiet": True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # meta
            meta_json = subprocess.check_output(
                ["ffprobe","-v","error","-show_entries","format=duration","-of","json",video_path]
            ).decode("utf-8")
            meta_probe = json.loads(meta_json or "{}")
            duration = float(meta_probe.get("format", {}).get("duration", 0.0))
            meta = {"source_url": url, "duration_sec": duration}

            # thumbs
            out_dir = os.path.join(work, "frames")
            os.makedirs(out_dir, exist_ok=True)
            subprocess.run([
                "ffmpeg","-y","-i",video_path,"-vf",f"fps=1/{extract_frames_every_sec}",
                os.path.join(out_dir,"f_%04d.jpg")
            ], check=True)
            thumbs = []
            names = sorted(os.listdir(out_dir))[:max(1, int(limit_frames))]
            for i, name in enumerate(names):
                p = os.path.join(out_dir, name)
                with open(p,"rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                thumbs.append({"time": i*extract_frames_every_sec, "b64": b64, "mime":"image/jpeg"})
            return AnalyzeOut(meta=meta, transcript=[], thumbs=thumbs, notes="auto: youtube")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"YouTube error: {e}")
        finally:
            try:
                os.remove(video_path)
            except Exception:
                pass

    # ---- NHÁNH FILE UPLOAD (tương tự /analyze_upload) ----
    if not video:
        raise HTTPException(status_code=400, detail="Hãy nhập link YouTube hoặc upload video.")

    work = tempfile.mkdtemp()
    vid_path = os.path.join(work, "v_tmp.mp4")
    with open(vid_path, "wb") as f:
        f.write(await video.read())

    # meta (an toàn nếu thiếu ffprobe)
    try:
        meta_json = subprocess.check_output(
            ["ffprobe","-v","error","-show_entries","format=duration","-of","json",vid_path]
        ).decode("utf-8")
        meta_probe = json.loads(meta_json or "{}")
        duration = float(meta_probe.get("format", {}).get("duration", 0.0))
    except Exception:
        duration = 0.0
    meta = {"title": video.filename, "duration_sec": duration}

    # transcript từ phụ đề nếu có
    transcript = []
    if subtitle is not None:
        raw = (await subtitle.read()).decode("utf-8", errors="ignore")
        import re
        blocks = re.split(r"\\n\\s*\\n", raw.strip())
        for b in blocks:
            lines = [l.strip() for l in b.splitlines() if l.strip()]
            if len(lines) >= 2 and "-->" in lines[1]:
                t0, t1 = lines[1].split("-->")
                transcript.append({"t_start": t0.strip(), "t_end": t1.strip(), "text": " ".join(lines[2:])})

    # trích frame làm thumbs
    thumbs = []
    out_dir = os.path.join(work, "frames")
    os.makedirs(out_dir, exist_ok=True)
    subprocess.run([
        "ffmpeg","-y","-i",vid_path,"-vf",f"fps=1/{extract_frames_every_sec}",
        os.path.join(out_dir,"f_%04d.jpg")
    ], check=True)
    names = sorted(os.listdir(out_dir))[:max(1, int(limit_frames))]
    for i, name in enumerate(names):
        p = os.path.join(out_dir, name)
        with open(p,"rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        thumbs.append({"time": i*extract_frames_every_sec, "b64": b64, "mime":"image/jpeg"})

    return AnalyzeOut(meta=meta, transcript=transcript, thumbs=thumbs, notes="auto: upload")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080)
