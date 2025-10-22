from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import tempfile, subprocess, os, base64, json, uuid, shutil

# 3rd party
import yt_dlp

app = FastAPI(title="Clone Video Analyzer v5 (cookies + dual client + format fallback)")

# CORS cho AI Studio / web
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Models ----------
class AnalyzeOut(BaseModel):
    meta: dict
    transcript: List[dict]
    thumbs: List[dict]
    notes: Optional[str] = None


# ---------- Utils ----------
def run(cmd: list):
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def to_netscape_cookies(text: str) -> str:
    """
    Cho phép dán cookies dạng 'Cookie: a=1; b=2' → chuyển tạm sang Netscape.
    Nếu đã là Netscape thì giữ nguyên.
    """
    t = (text or "").strip()
    if not t:
        return ""
    if "\t" in t or "HTTP Cookie File" in t:
        return t  # Netscape rồi
    if t.lower().startswith("cookie:"):
        t = t.split(":", 1)[1].strip()
    pairs = [p.strip() for p in t.split(";") if "=" in p]
    if not pairs:
        return text
    header = "# Netscape HTTP Cookie File\n"
    lines = []
    for p in pairs:
        name, value = p.split("=", 1)
        lines.append(f".youtube.com\tTRUE\t/\tTRUE\t2147483647\t{name.strip()}\t{value.strip()}")
    return header + "\n".join(lines) + "\n"

def netscape_to_cookie_header(netscape_txt: str) -> str:
    cookies = []
    for line in (netscape_txt or "").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            name, value = parts[5], parts[6]
            cookies.append(f"{name}={value}")
    return "; ".join(cookies)


# ---------- yt-dlp download with dual client + fallback ----------
def ytdlp_download(url: str, output_path: str, cookies_path: Optional[str] = None, cookie_header: Optional[str] = None):
    common_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 11; SM-G991B Build/RP1A.200720.012) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/127.0.0.0 Mobile Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
    }
    if cookie_header:
        common_headers["Cookie"] = cookie_header

    base_opts = {
        "outtmpl": output_path,
        "format": "(bestvideo+bestaudio/best)[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 5,
        "http_headers": common_headers,
        "extractor_args": {
            "youtube": {
                # dùng 2 client – thường vượt qua bot-check tốt hơn
                "player_client": ["android", "tv_embedded"],
                "skip": ["webpage"],
            }
        },
    }
    if cookies_path:
        base_opts["cookiefile"] = cookies_path

    try:
        with yt_dlp.YoutubeDL(base_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        # Fallback cuối: tải "best" bất kể ext
        print(f"[WARN] Primary format failed: {e} -> fallback to 'best'")
        fallback = dict(base_opts)
        fallback["format"] = "best"
        with yt_dlp.YoutubeDL(fallback) as ydl:
            ydl.download([url])


# ---------- Endpoints ----------
@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/analyze_auto", response_model=AnalyzeOut)
async def analyze_auto(
    url: Optional[str] = Form(None),
    video: Optional[UploadFile] = File(None),
    subtitle: Optional[UploadFile] = File(None),
    # cookies có thể là file .txt và/hoặc text vùng nhập (optional)
    cookies_file: Optional[UploadFile] = File(None),
    cookies_txt: Optional[str] = Form(None),
    extract_frames_every_sec: float = Form(10.0),
    limit_frames: int = Form(6),
):
    """
    1-click:
    - Có url -> tải bằng yt-dlp (+cookies nếu có)
    - Không url -> xử lý file upload
    - cookies: file .txt hoặc text (Netscape / 'Cookie: a=1; ...')
    """
    work = tempfile.mkdtemp()
    cookiefile_path = None
    cookie_header = None

    try:
        # ---- Chuẩn bị cookies (nếu có) ----
        if cookies_file:
            cookiefile_path = os.path.join(work, "cookies.txt")
            with open(cookiefile_path, "wb") as f:
                f.write(await cookies_file.read())
            with open(cookiefile_path, "r", encoding="utf-8", errors="ignore") as f:
                cookie_header = netscape_to_cookie_header(f.read())

        if cookies_txt:
            netscape = to_netscape_cookies(cookies_txt)
            if not cookiefile_path:
                cookiefile_path = os.path.join(work, "cookies_inline.txt")
            with open(cookiefile_path, "w", encoding="utf-8") as f:
                f.write(netscape)
            cookie_header = netscape_to_cookie_header(netscape)

        # ---- NHÁNH YOUTUBE ----
        if url:
            try:
                video_path = os.path.join(work, "video.mp4")
                ytdlp_download(url, video_path, cookiefile_path, cookie_header)

                # meta
                meta_json = subprocess.check_output(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", video_path]
                ).decode("utf-8")
                meta_data = json.loads(meta_json or "{}")
                duration = float(meta_data.get("format", {}).get("duration", 0.0))
                meta = {"source_url": url, "duration_sec": duration}

                # thumbnails
                out_dir = os.path.join(work, "frames")
                os.makedirs(out_dir, exist_ok=True)
                run(["ffmpeg", "-y", "-i", video_path, "-vf", f"fps=1/{extract_frames_every_sec}",
                     os.path.join(out_dir, "f_%04d.jpg")])
                thumbs = []
                names = sorted(os.listdir(out_dir))[:max(1, int(limit_frames))]
                for i, name in enumerate(names):
                    p = os.path.join(out_dir, name)
                    thumbs.append({"time": i*extract_frames_every_sec, "b64": b64(p), "mime": "image/jpeg"})

                return AnalyzeOut(meta=meta, transcript=[], thumbs=thumbs, notes="auto: youtube")

            except Exception as e:
                raise HTTPException(status_code=500, detail=f"YouTube error: {e}")

        # ---- NHÁNH FILE UPLOAD ----
        if not video:
            raise HTTPException(status_code=400, detail="Hãy nhập link YouTube hoặc upload video.")

        vid_path = os.path.join(work, f"v_{uuid.uuid4().hex}.mp4")
        with open(vid_path, "wb") as f:
            f.write(await video.read())

        # meta
        try:
            meta_json = subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", vid_path]
            ).decode("utf-8")
            meta_probe = json.loads(meta_json or "{}")
            duration = float(meta_probe.get("format", {}).get("duration", 0.0))
        except Exception:
            duration = 0.0
        meta = {"title": video.filename, "duration_sec": duration}

        # phụ đề
        transcript = []
        if subtitle:
            raw = (await subtitle.read()).decode("utf-8", errors="ignore")
            import re
            blocks = re.split(r"\n\s*\n", raw.strip())
            for b in blocks:
                lines = [l.strip() for l in b.splitlines() if l.strip()]
                if len(lines) >= 2 and "-->" in lines[1]:
                    t0, t1 = lines[1].split("-->")
                    transcript.append({"t_start": t0.strip(), "t_end": t1.strip(), "text": " ".join(lines[2:])})

        # thumbs
        out_dir = os.path.join(work, "frames")
        os.makedirs(out_dir, exist_ok=True)
        run(["ffmpeg", "-y", "-i", vid_path, "-vf", f"fps=1/{extract_frames_every_sec}",
             os.path.join(out_dir, "f_%04d.jpg")])
        thumbs = []
        names = sorted(os.listdir(out_dir))[:max(1, int(limit_frames))]
        for i, name in enumerate(names):
            p = os.path.join(out_dir, name)
            thumbs.append({"time": i*extract_frames_every_sec, "b64": b64(p), "mime": "image/jpeg"})

        return AnalyzeOut(meta=meta, transcript=transcript, thumbs=thumbs, notes="auto: upload")

    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080)
