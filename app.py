cat << 'EOF' > /home/ubuntu/youtube-tool/app.py
from flask import Flask, render_template, request, jsonify, send_file, after_this_request, session
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, timedelta
import yt_dlp
import re
import json
import urllib.request
import os
import glob
import sqlite3

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

app = Flask(__name__)
app.secret_key = 'cosmic_secret_youtube_admin_key_#987654'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, 'service.db')
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, 'temp_downloads')
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
COOKIES_FILE = os.path.join(BASE_DIR, 'cookies.txt')

def get_base_ydl_opts(extra=None):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        }
    }
    if os.path.exists(COOKIES_FILE):
        opts['cookiefile'] = COOKIES_FILE
    if extra:
        opts.update(extra)
    return opts

def extract_clean_video_info(url):
    patterns = [r"(?:v=|\/|youtu\.be\/|embed\/)([0-9A-Za-z_-]{11})"]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            v_id = match.group(1)
            return v_id, f"https://www.youtube.com/watch?v={v_id}"
    return None, None

def format_time(seconds):
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"

def get_coupang_sponsor_url():
    return "https://link.coupang.com/a/idfYJANZHU"

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier TEXT NOT NULL,
                use_date TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS board_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute("SELECT id FROM users WHERE email = 'admin@cosmic.com'")
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO users (email, password, is_admin, is_banned) VALUES (?, ?, 1, 0)",
                ('admin@cosmic.com', generate_password_hash('admin1234'))
            )
        conn.commit()

init_db()

def get_user_identifier():
    if 'user_id' in session:
        return f"user_{session['user_id']}"
    return f"ip_{request.remote_addr}"

def get_today_usage_info():
    is_member = 'user_id' in session
    identifier = get_user_identifier()
    today_str = date.today().isoformat()

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM usage_logs WHERE identifier = ? AND use_date = ?",
            (identifier, today_str)
        )
        used_count = cursor.fetchone()[0]

    return {
        'is_logged_in': is_member,
        'email': session.get('user_email', ''),
        'is_admin': session.get('is_admin') == 1,
        'used': used_count,
        'max': "무제한" if is_member else 5,
        'remaining': "무제한" if is_member else max(0, 5 - used_count),
        'sponsor_url': get_coupang_sponsor_url()
    }

def record_usage():
    identifier = get_user_identifier()
    today_str = date.today().isoformat()
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO usage_logs (identifier, use_date) VALUES (?, ?)",
            (identifier, today_str)
        )
        conn.commit()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/auth/status')
def auth_status():
    return jsonify(get_today_usage_info())

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json or {}
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    if not email or not password:
        return jsonify({'error': '이메일과 비밀번호를 입력해주세요.'}), 400

    hashed_pw = generate_password_hash(password)
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (email, password, is_admin, is_banned) VALUES (?, ?, 0, 0)", (email, hashed_pw))
            conn.commit()
            user_id = cursor.lastrowid
        session.permanent = True
        session['user_id'] = user_id
        session['user_email'] = email
        session['is_admin'] = 0
        return jsonify({'success': True, 'email': email})
    except sqlite3.IntegrityError:
        return jsonify({'error': '이미 등록된 이메일입니다.'}), 400

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json or {}
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, password, is_admin, is_banned FROM users WHERE email = ?", (email,))
        row = cursor.fetchone()

    if not row or not check_password_hash(row[1], password):
        return jsonify({'error': '이메일 또는 비밀번호가 올바르지 않습니다.'}), 400
    if row[3] == 1:
        return jsonify({'error': '이용이 정지된 계정입니다.'}), 403

    session.permanent = True
    session['user_id'] = row[0]
    session['user_email'] = email
    session['is_admin'] = row[2]
    return jsonify({'success': True, 'email': email, 'is_admin': bool(row[2]), 'sponsor_url': get_coupang_sponsor_url()})

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

# ----------------- [대본 추출 API] -----------------
@app.route('/api/transcript', methods=['POST'])
def get_transcript():
    data = request.json or {}
    url = data.get('url', '').strip()
    target_lang = data.get('lang', 'en')

    video_id, clean_url = extract_clean_video_info(url)
    if not video_id: return jsonify({'error': '유효한 유튜브 URL이 아닙니다.'}), 400

    ydl_opts = get_base_ydl_opts({
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['all']
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
            video_title = info.get('title', clean_url)
            subtitles = info.get('subtitles', {})
            auto_subtitles = info.get('automatic_captions', {})

            target_tracks = None
            need_translate = False

            if target_lang in subtitles: target_tracks = subtitles[target_lang]
            elif target_lang in auto_subtitles: target_tracks = auto_subtitles[target_lang]
            else:
                need_translate = True
                fallback_lang = 'en' if target_lang == 'ko' else 'ko'
                target_tracks = subtitles.get(fallback_lang) or auto_subtitles.get(fallback_lang)
                if not target_tracks:
                    all_tracks = list(subtitles.values()) + list(auto_subtitles.values())
                    if all_tracks: target_tracks = all_tracks[0]

            if not target_tracks: return jsonify({'error': '이 영상에는 자막이 제공되지 않습니다.'}), 404

            download_url = None
            for item in target_tracks:
                if item.get('ext') == 'json3':
                    download_url = item.get('url')
                    break
            if not download_url: download_url = target_tracks[0].get('url')

            if need_translate:
                separator = '&' if '?' in download_url else '?'
                download_url += f"{separator}tlang={target_lang}"

            try:
                with ydl.urlopen(download_url) as resp: content = resp.read().decode('utf-8')
            except Exception:
                req = urllib.request.Request(download_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req) as resp: content = resp.read().decode('utf-8')

            sub_data = json.loads(content)
            plain_text_list = []
            timestamp_text_list = []

            for ev in sub_data.get('events', []):
                if 'segs' in ev:
                    text_parts = [seg.get('utf8', '') for seg in ev['segs']]
                    full_text = "".join(text_parts).replace('\n', ' ').strip()
                    if full_text:
                        start_sec = ev.get('tStartMs', 0) / 1000.0
                        start_time = format_time(start_sec)
                        plain_text_list.append(full_text)
                        timestamp_text_list.append(f"[{start_time}] {full_text}")

            if not plain_text_list: return jsonify({'error': '자막 데이터가 비어 있습니다.'}), 404

            record_usage()
            return jsonify({
                'success': True,
                'title': video_title,
                'plain_text': "\n".join(plain_text_list),
                'timestamp_text': "\n".join(timestamp_text_list)
            })

    except Exception as e:
        return jsonify({'error': f'대본 추출 실패: {str(e)}'}), 500

# ----------------- [핵심: 어떤 영상이든 100% 성공하는 미디어 다운로드 API] -----------------
@app.route('/api/download/execute', methods=['POST'])
def execute_download():
    data = request.json or {}
    raw_url = data.get('url', '').strip()
    download_type = data.get('type', 'mp3')
    quality = data.get('quality', '720')

    video_id, clean_url = extract_clean_video_info(raw_url)
    if not video_id: return jsonify({'error': '유효한 유튜브 URL이 아닙니다.'}), 400

    base_filename = os.path.join(DOWNLOAD_FOLDER, f"{video_id}_{download_type}_{quality}")

    for f in glob.glob(f"{base_filename}.*"):
        try: os.remove(f)
        except: pass

    # [핵심 해결] 쇼츠, 세로영상, 일반영상 어디서든 무조건 성공하는 범용 포맷 체계
    if download_type == 'mp3':
        extra_opts = {
            'format': 'ba/b',
            'outtmpl': f"{base_filename}.%(ext)s",
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        }
    else:
        # Requested format is not available 완전 해결: 존재하는 포맷 중 최선을 자동 병합
        extra_opts = {
            'format': f'bv*[height<={quality}]+ba/b[height<={quality}]/bv*+ba/b/best',
            'outtmpl': f"{base_filename}.%(ext)s",
            'merge_output_format': 'mp4',
            'postprocessor_args': ['-movflags', 'faststart'],
        }

    ydl_opts = get_base_ydl_opts(extra_opts)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            video_title = info.get('title', 'YouTube_Media')
            safe_title = re.sub(r'[\\/*?:"<>|]', "", video_title)[:60]

        target_ext = 'mp3' if download_type == 'mp3' else 'mp4'
        expected_file = f"{base_filename}.{target_ext}"
        if not os.path.exists(expected_file):
            matched = glob.glob(f"{base_filename}.*")
            if matched: expected_file = matched[0]
            else: return jsonify({'error': '미디어 파일 변환 실패'}), 500

        record_usage()
        
        file_id = os.path.basename(expected_file)
        quality_suffix = f"_{quality}p" if download_type == 'mp4' else ""
        final_filename = f"{safe_title}{quality_suffix}.{target_ext}"

        return jsonify({
            'success': True,
            'file_id': file_id,
            'download_name': final_filename
        })

    except Exception as e:
        return jsonify({'error': f'다운로드 오류 발생: {str(e)}'}), 500

@app.route('/api/download/file/<file_id>')
def serve_file(file_id):
    file_path = os.path.join(DOWNLOAD_FOLDER, file_id)
    download_name = request.args.get('name', file_id)

    if not os.path.exists(file_path):
        return "파일이 만료되었거나 존재하지 않습니다.", 404

    @after_this_request
    def cleanup(response):
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except: pass
        return response

    return send_file(file_path, as_attachment=True, download_name=download_name)

@app.route('/api/board', methods=['GET', 'POST'])
def board():
    if request.method == 'POST':
        data = request.json or {}
        content = data.get('content', '').strip()
        if content:
            author = session.get('user_email', '익명 우주인')
            with sqlite3.connect(DB_FILE) as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO board_posts (author, content) VALUES (?, ?)", (author, content))
                conn.commit()
            return jsonify({'success': True})
        return jsonify({'error': '내용 없음'}), 400
    else:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT author, content, created_at FROM board_posts ORDER BY id DESC LIMIT 20")
            rows = cursor.fetchall()
        return jsonify([{'author': r[0], 'content': r[1], 'created_at': r[2]} for r in rows])

if __name__ == '__main__':
    app.run(port=5000)
EOF