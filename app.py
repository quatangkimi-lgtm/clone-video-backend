app.py
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080)
