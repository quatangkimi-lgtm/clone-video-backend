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
