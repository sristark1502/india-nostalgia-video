import os
import re
import uuid
import shutil
import subprocess
import requests
from flask import Flask, request, send_file, jsonify

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB max request


# ── Health check ─────────────────────────────────────────────────────────────
@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'service': 'India Nostalgia Video Generator'})


# ── Download helper (handles Google Drive + redirects) ────────────────────────
def download_file(url, dest):
    """Download a file, handling Google Drive share/confirmation redirects."""
    session = requests.Session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    resp = session.get(url, headers=headers, timeout=60, stream=True, allow_redirects=True)
    resp.raise_for_status()

    content_type = resp.headers.get('Content-Type', '')

    # Google Drive returns HTML when it needs a download confirmation
    if 'text/html' in content_type:
        # Read the HTML to find the confirm token
        html = b''.join(resp.iter_content(65536)).decode('utf-8', errors='ignore')

        # Try multiple confirm patterns used by Google Drive
        confirm = None
        for pattern in [r'confirm=([0-9A-Za-z_\-]+)', r'"confirm":"([^"]+)"', r'&amp;confirm=([^&"]+)']:
            m = re.search(pattern, html)
            if m:
                confirm = m.group(1)
                break

        if confirm:
            new_url = url + f'&confirm={confirm}'
        else:
            # Generic confirm fallback used for smaller files
            new_url = url + '&confirm=t'

        resp = session.get(new_url, headers=headers, timeout=60, stream=True, allow_redirects=True)
        resp.raise_for_status()

        content_type = resp.headers.get('Content-Type', '')
        if 'text/html' in content_type:
            raise ValueError(
                f'Google Drive returned HTML instead of file. '
                f'Make sure the file is shared as "Anyone with the link". '
                f'URL: {url}'
            )

    with open(dest, 'wb') as f:
        for chunk in resp.iter_content(8192):
            if chunk:
                f.write(chunk)

    size = os.path.getsize(dest)
    if size < 100:
        raise ValueError(f'Downloaded file too small ({size} bytes) — likely an error page, not the actual file.')

    return size


# ── Main endpoint ─────────────────────────────────────────────────────────────
@app.route('/create-video', methods=['POST'])
def create_video():
    data = request.get_json(force=True)

    image1_url = data.get('image1_url')
    image2_url = data.get('image2_url')
    music_url  = data.get('music_url')
    text1      = data.get('text1', '2026')
    text2      = data.get('text2', '2017')
    image1_b64 = data.get('image1_b64')
    image2_b64 = data.get('image2_b64')

    if not ((image1_url or image1_b64) and (image2_url or image2_b64) and music_url):
        return jsonify({'error': 'Provide (image1_url or image1_b64), (image2_url or image2_b64), and music_url'}), 400

    # Clean up old /tmp dirs to prevent disk fill-up
    try:
        for entry in os.listdir('/tmp'):
            full = os.path.join('/tmp', entry)
            if os.path.isdir(full) and len(entry) == 36:  # UUID dirs
                shutil.rmtree(full, ignore_errors=True)
    except Exception:
        pass

    tmp    = f'/tmp/{uuid.uuid4()}'
    # Save as .png since gpt-image-1 returns PNG format
    img1   = f'{tmp}/img1.png'
    img2   = f'{tmp}/img2.png'
    music  = f'{tmp}/music.mp3'
    output = f'{tmp}/nostalgia.mp4'
    os.makedirs(tmp, exist_ok=True)

    # ── Save/download assets ──────────────────────────────────────────────────
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

        music_size = download_file(music_url, music)

    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return jsonify({'error': f'Asset download/decode failed: {str(e)}'}), 500

    # ── Verify music file is valid audio (not HTML) ───────────────────────────
    try:
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', music],
            capture_output=True, text=True, timeout=15
        )
        if probe.returncode != 0 or not probe.stdout.strip():
            shutil.rmtree(tmp, ignore_errors=True)
            return jsonify({
                'error': 'Music file is not valid audio. Check that music_url points to an actual MP3 file and is publicly accessible.',
                'ffprobe_error': probe.stderr[:500]
            }), 400
        music_duration = float(probe.stdout.strip())
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return jsonify({'error': f'Music validation failed: {str(e)}'}), 500

    # ── Build FFmpeg command ──────────────────────────────────────────────────
    t1 = text1.replace("'", "\\'").replace(':', '\\:')
    t2 = text2.replace("'", "\\'").replace(':', '\\:')

    filter_complex = (
        f"[0:v]scale=720:1280:force_original_aspect_ratio=increase,"
        f"crop=720:1280,setsar=1,"
        f"drawtext=text='{t1}':"
        f"fontsize=130:fontcolor=white@0.95:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"shadowcolor=black@0.75:shadowx=5:shadowy=5,"
        f"fade=t=in:st=0:d=1,"
        f"fade=t=out:st=5:d=1[v1];"

        f"[1:v]scale=720:1280:force_original_aspect_ratio=increase,"
        f"crop=720:1280,setsar=1,"
        f"drawtext=text='{t2}':"
        f"fontsize=130:fontcolor=white@0.95:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"shadowcolor=black@0.75:shadowx=5:shadowy=5,"
        f"fade=t=in:st=0:d=1,"
        f"fade=t=out:st=5:d=1[v2];"

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
        '-t', '12',           # force exactly 12s (don't rely on -shortest with music)
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
        return jsonify({'error': 'FFmpeg timed out (>5 min)'}), 504

    if result.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        # Return first 500 chars (startup errors) + last 2000 chars (failure reason)
        stderr = result.stderr
        error_detail = stderr[:500] + '\n...\n' + stderr[-2000:] if len(stderr) > 2500 else stderr
        return jsonify({
            'error': 'FFmpeg failed',
            'returncode': result.returncode,
            'details': error_detail,
            'music_size_bytes': music_size,
            'music_duration_sec': music_duration
        }), 500

    if not os.path.exists(output) or os.path.getsize(output) == 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return jsonify({'error': 'FFmpeg succeeded but output file is empty'}), 500

    return send_file(
        output,
        mimetype='video/mp4',
        as_attachment=True,
        download_name='india_nostalgia.mp4'
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
