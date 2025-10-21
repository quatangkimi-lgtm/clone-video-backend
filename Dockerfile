# Dockerfile - chạy ổn trên Render Free
FROM python:3.11-slim

# Cài ffmpeg + libgomp (cho faster-whisper/ctranslate2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgomp1 ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cài thư viện Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy mã nguồn
COPY app.py .

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
# ---------- Base ----------
FROM python:3.11-slim

# Không ghi .pyc và log ra stdout ngay
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ---------- OS deps ----------
# ffmpeg là bắt buộc cho trích frame
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# ---------- Python deps ----------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------- App code ----------
COPY . .

# Render set biến PORT tự động, ta đọc ra
ENV PORT=8080

# ---------- Start ----------
# Dùng $PORT để khớp healthcheck của Render
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT}
