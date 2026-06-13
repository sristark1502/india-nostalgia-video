import os
import re
import uuid
import shutil
import subprocess
import requests
from flask import Flask, request, send_file, jsonify

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB max request

BUNDLED_MUSIC = '/app/music.mp3'   # copied into Docker image


# ── Health check ─────────────────────────────────────────────────────────────
@app.route('/health')
def health():
    return jsonify({
        'status': 'ok',
        'service': 'India Nostalgia Video Generator',
        'bundled_music': os.path.exists(BUNDLED_MUSIC)
    })


# ── Download helper (handles Google Drive confirmation redirects) ──────────────
def download_file(url, dest):
    session = requests.Session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    resp = session.get(url, headers=headers, timeout=60, stream=True, allow_redirects=True)
    resp.raise_for_status()

    content_type = resp.headers.get('Content-Type', '')

    if 'text/html' in content_type:
        html = b''.join(resp.iter_content(65536)).decode('utf-8', errors='ignore')
        confirm = None
        for pattern in [r'confirm=([0-9A-Za-z_\-]+)', r'"confirm":"([^"]+)"', r'&amp;confirm=([^&"]+)']:
            m = re.search(pattern, html)
            if m:
                confirm = m.group(1)
                break
        new_url = url + f'&confirm={confirm}' if confirm else url + '&confirm=t'
        resp = session.get(new_url, headers=headers, timeout=60, stream=True, allow_redirects=True)
        resp.raise_for_status()
        if 'text/html' in resp.headers.get('Content-Type', ''):
            raise ValueError(f'URL returned HTML instead of a file — make sure it is publicly accessible: {url}')

    with open(dest, 'wb') as f:
        for chunk in resp.iter_content(8192):
            if chunk:
                f.write(chunk)

    size = os.path.getsize(dest)
    if size < 100:
        raise ValueError(f'Downloaded file is only {size} bytes — likely an error page, not the actual file.')
    return size


# ── Main endpoint ─────────────────────────────────────────────────────────────
@app.route('/create-video', methods=['POST'])
def create_video():
    data = request.get_json(force=True)

    image1_url = data.get('image1_url')
    image2_url = data.get('image2_url')
    music_url  = data.get('music_url', '').strip()
    text1      = data.get('text1', '2026')
    text2      = data.get('text2', '2017')
    image1_b64 = data.get('image1_b64')
    image2_b64 = data.get('image2_b64')

    if not ((image1_url or image1_b64) and (image2_url or image2_b64)):
        return jsonify({'error': 'Provide image1_b64 or image1_url, and image2_b64 or image2_url'}), 400

    # Clean up stale /tmp dirs
    try:
        for entry in os.listdir('/tmp'):
            full = os.path.join('/tmp', entry)
            if os.path.isdir(full) and len(entry) == 36:
                shutil.rmtree(full, ignore_errors=True)
    except Exception:
        pass

    tmp    = f'/tmp/{uuid.uuid4()}'
    img1   = f'{tmp}/img1.png'
    img2   = f'{tmp}/img2.png'
    music  = f'{tmp}/music.mp3'
    output = f'{tmp}/nostalgia.mp4'
    os.makedirs(tmp, exist_ok=True)

    # ── Images ────────────────────────────────────────────────────────────────
    try:
        import base64 as b64lib
        if image1_b64:
            with open(img1, 'wb') as f:
                f.write(b64lib.b64decode(image1_b64))
        else:
            download_file(image1_url, img1)

        if image2_b64:
            with open(img2, 'wb') as f:
                f.write(b64lib.b64decode(image2_b64))
        else:
            download_file(image2_url, img2)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return jsonify({'error': f'Image download/decode failed: {str(e)}'}), 500

    # ── Music: use URL if provided, otherwise use bundled file ───────────────
    try:
        if music_url:
            download_file(music_url, music)
        elif os.path.exists(BUNDLED_MUSIC):
            shutil.copy(BUNDLED_MUSIC, music)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
            return jsonify({'error': 'No music_url provided and no bundled music.mp3 found in image'}), 400
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return jsonify({'error': f'Music setup failed: {str(e)}'}), 500

    # ── FFmpeg ────────────────────────────────────────────────────────────────
    t1 = text1.replace("'", "\\'").replace(':', '\\:')
    t2 = text2.replace("'", "\\'").replace(':', '\\:')

    filter_complex = (
        f"[0:v]scale=720:1280:force_original_aspect_ratio=increase,"
        f"crop=720:1280,setsar=1,"
        f"drawtext=text='{t1}':"
        f"fontsize=130:fontcolor=white@0.95:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"shadowcolor=black@0.75:shadowx=5:shadowy=5,"
        f"fade=t=in:st=0:d=1,fade=t=out:st=5:d=1[v1];"
        f"[1:v]scale=720:1280:force_original_aspect_ratio=increase,"
        f"crop=720:1280,setsar=1,"
        f"drawtext=text='{t2}':"
        f"fontsize=130:fontcolor=white@0.95:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"shadowcolor=black@0.75:shadowx=5:shadowy=5,"
        f"fade=t=in:st=0:d=1,fade=t=out:st=5:d=1[v2];"
        f"[v1][v2]concat=n=2:v=1:a=0[vout]"
    )

    cmd = [
        'ffmpeg', '-y',
        '-loop', '1', '-t', '6', '-i', img1,
        '-loop', '1', '-t', '6', '-i', img2,
        '-stream_loop', '-1', '-i', music,   # loop music so short clips still work
        '-filter_complex', filter_complex,
        '-map', '[vout]',
        '-map', '2:a',
        '-t', '12',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
        '-c:a', 'aac', '-b:a', '128k',
        '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
        output
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp, ignore_errors=True)
        return jsonify({'error': 'FFmpeg timed out after 5 min'}), 504

    if result.returncode != 0:
        stderr = result.stderr
        detail = stderr[:500] + '\n...\n' + stderr[-2000:] if len(stderr) > 2500 else stderr
        shutil.rmtree(tmp, ignore_errors=True)
        return jsonify({
            'error': 'FFmpeg failed',
            'returncode': result.returncode,
            'details': detail
        }), 500

    if not os.path.exists(output) or os.path.getsize(output) == 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return jsonify({'error': 'Output file is empty after FFmpeg'}), 500

    return send_file(
        output,
        mimetype='video/mp4',
        as_attachment=True,
        download_name='india_nostalgia.mp4'
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
