FROM python:3.11-slim

# Install FFmpeg + font support for drawtext
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-liberation \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD gunicorn --bind 0.0.0.0:$PORT --timeout 300 --workers 2 main:app
