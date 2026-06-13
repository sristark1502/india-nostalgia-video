import os
import uuid
import subprocess
import requests
from flask import Flask, request, send_file, jsonify

app = Flask(__name__)

# ── Health check ────────────────────────────────────────────────────────────
@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'service': 'India Nostalgia Video Generator'})

# ── Main endpoint ────────────────────────────────────────────────────────────
@app.route('/create-video', methods=['POST'])
def create_video():
    """
    POST body (JSON):
    {
        "image1_url": "...",   <- 2026 bad nature image URL
        "image2_url": "...",   <- 2017 good nature image URL
        "music_url":  "...",   <- background music MP3/MP4 URL
        "text1": "2026",       <- text overlay on image 1
        "text2": "2017"        <- text overlay on image 2
    }
    Returns: MP4 video binary (720x1280 vertical, ~12 seconds)
    """
    data = request.get_json(force=True)

    image1_url = data.get('image1_url')
    image2_url = data.get('image2_url')
    music_url  = data.get('music_url')
    text1      = data.get('text1', '2026')
    text2      = data.get('text2', '2017')

    if not all([image1_url, image2_url, music_url]):
        return jsonify({'error': 'image1_url, image2_url, and music_url are all required'}), 400

    tmp    = f'/tmp/{uuid.uuid4()}'
    img1   = f'{tmp}/img1.jpg'
    img2   = f'{tmp}/img2.jpg'
    music  = f'{tmp}/music.mp3'
    output = f'{tmp}/nostalgia.mp4'
    os.makedirs(tmp, exist_ok=True)

    # ── Download all assets ──────────────────────────────────────────────────
    def download(url, dest):
        resp = requests.get(url, timeout=60, stream=True,
                            headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
        with open(dest, 'wb') as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)

    image1_b64 = data.get('image1_b64')
    image2_b64 = data.get('image2_b64')

    try:
        # Support both base64 and URL for images
        if image1_b64:
            import base64 as b64lib
            with open(img1, 'wb') as f:
                f.write(b64lib.b64decode(image1_b64))
        else:
            download(image1_url, img1)

        if image2_b64:
            import base64 as b64lib
            with open(img2, 'wb') as f:
                f.write(b64lib.b64decode(image2_b64))
        else:
            download(image2_url, img2)

        download(music_url, music)
    except Exception as e:
        return jsonify({'error': f'Download/decode failed: {str(e)}'}), 500

    # ── Build FFmpeg filter ──────────────────────────────────────────────────
    # Escape quotes for drawtext
    t1 = text1.replace("'", "\\'").replace(':', '\\:')
    t2 = text2.replace("'", "\\'").replace(':', '\\:')

    filter_complex = (
        # --- Clip 1: 2026 (bad nature) ---
        f"[0:v]scale=720:1280:force_original_aspect_ratio=increase,"
        f"crop=720:1280,setsar=1,"
        f"drawtext=text='{t1}':"
        f"fontsize=130:fontcolor=white@0.95:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"shadowcolor=black@0.75:shadowx=5:shadowy=5,"
        f"fade=t=in:st=0:d=1,"
        f"fade=t=out:st=5:d=1[v1];"

        # --- Clip 2: 2017 (beautiful nature) ---
        f"[1:v]scale=720:1280:force_original_aspect_ratio=increase,"
        f"crop=720:1280,setsar=1,"
        f"drawtext=text='{t2}':"
        f"fontsize=130:fontcolor=white@0.95:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"shadowcolor=black@0.75:shadowx=5:shadowy=5,"
        f"fade=t=in:st=0:d=1,"
        f"fade=t=out:st=5:d=1[v2];"

        # --- Concat ---
        f"[v1][v2]concat=n=2:v=1:a=0[vout]"
    )

    cmd = [
        'ffmpeg', '-y',
        '-loop', '1', '-t', '6', '-i', img1,
        '-loop', '1', '-t', '6', '-i', img2,
        '-i', music,
        '-filter_complex', filter_complex,
        '-map', '[vout]',
        '-map', '2:a',
        '-shortest',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
        '-c:a', 'aac', '-b:a', '128k',
        '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
        output
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Video processing timed out (>3 min)'}), 504

    if result.returncode != 0:
        return jsonify({
            'error': 'FFmpeg failed',
            'details': result.stderr[-1200:]
        }), 500

    return send_file(
        output,
        mimetype='video/mp4',
        as_attachment=True,
        download_name='india_nostalgia.mp4'
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

