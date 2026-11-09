from flask import Flask, render_template, request, jsonify, send_file, after_this_request, session, redirect
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
except Exception as e:
    pass

app = Flask(__name__)
app.secret_key = 'cosmic_secret_youtube_admin_key_#987654'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, 'service.db')
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, 'temp_downloads')
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# [핵심] 2026년 최신 데이터센터 봇 차단 우회 옵션 (mweb/tv client 세션 풀 활용)
def get_base_ydl_opts():
    opts = {
        'quiet': True,
        'no_warnings': True,
        # 유튜브 TV 및 모바일 웹 클라이언트는 데이터센터 IP 차단 검사를 하지 않음
        'extractor_args': {
            'youtube': {
                'player_client': ['tv', 'mweb', 'android'],
                'player_skip': ['webpage', 'configs'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/4.0 Chrome/76.0.3809.146 TV Safari/537.36',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        }
    }
    
    cookies_path = os.path.join(BASE_DIR, 'cookies.txt')
    if os.path.exists(cookies_path):
        opts['cookiefile'] = cookies_path

    return opts

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
        try: cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        except: pass
        try: cursor.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
        except: pass

        cursor.execute("SELECT id FROM users WHERE email = 'admin@cosmic.com'")
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO users (email, password, is_admin, is_banned) VALUES (?, ?, 1, 0)",
                ('admin@cosmic.com', generate_password_hash('admin1234'))
            )
        conn.commit()

init_db()

def is_admin():
    return session.get('is_admin') == 1

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

    if is_member:
        max_quota = "무제한"
        remaining = "무제한"
    else:
        max_quota = 5
        remaining = max(0, 5 - used_count)

    return {
        'is_logged_in': is_member,
        'email': session.get('user_email', ''),
        'is_admin': is_admin(),
        'used': used_count,
        'max': max_quota,
        'remaining': remaining,
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

def extract_video_id(url):
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11}).*",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
        r"embed\/([0-9A-Za-z_-]{11})"
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match: return match.group(1)
    return None

def format_time(seconds):
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"

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
        return jsonify({'error': '관리자에 의해 이용이 정지된 계정입니다.'}), 403

    session.permanent = True
    session['user_id'] = row[0]
    session['user_email'] = email
    session['is_admin'] = row[2]

    return jsonify({
        'success': True, 
        'email': email, 
        'is_admin': bool(row[2]),
        'sponsor_url': get_coupang_sponsor_url()
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/admin')
def admin_page():
    if not is_admin(): return redirect('/')
    return render_template('admin.html')

@app.route('/api/admin/users', methods=['GET'])
def admin_get_users():
    if not is_admin(): return jsonify({'error': '권한이 없습니다.'}), 403
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, email, is_admin, is_banned, created_at FROM users ORDER BY id DESC")
        rows = cursor.fetchall()
    return jsonify([{'id': r[0], 'email': r[1], 'is_admin': bool(r[2]), 'is_banned': bool(r[3]), 'created_at': r[4]} for r in rows])

@app.route('/api/admin/users/<int:user_id>/toggle-ban', methods=['POST'])
def admin_toggle_ban(user_id):
    if not is_admin(): return jsonify({'error': '권한이 없습니다.'}), 403
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned, is_admin FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        if not user or user[1] == 1: return jsonify({'error': '처리 불가 대상입니다.'}), 400
        new_status = 0 if user[0] == 1 else 1
        cursor.execute("UPDATE users SET is_banned = ? WHERE id = ?", (new_status, user_id))
        conn.commit()
    return jsonify({'success': True, 'is_banned': bool(new_status)})

@app.route('/api/admin/users/<int:user_id>/delete', methods=['POST'])
def admin_delete_user(user_id):
    if not is_admin(): return jsonify({'error': '권한이 없습니다.'}), 403
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        if not user or user[0] == 1: return jsonify({'error': '삭제 불가 대상입니다.'}), 400
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        cursor.execute("DELETE FROM usage_logs WHERE identifier = ?", (f"user_{user_id}",))
        conn.commit()
    return jsonify({'success': True})

# ----------------- [화질 분석 API] -----------------
@app.route('/api/video/formats', methods=['POST'])
def get_video_formats():
    data = request.json or {}
    url = data.get('url', '').strip()
    video_id = extract_video_id(url)
    if not video_id:
        return jsonify({'error': '유효한 유튜브 URL이 아닙니다.'}), 400

    clean_url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        **get_base_ydl_opts(),
        'skip_download': True,
        'noplaylist': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
            formats = info.get('formats', [])
            duration = info.get('duration', 0)
            title = info.get('title', 'YouTube 동영상')

            audio_size = (128 * 1000 / 8) * duration if duration else 0
            available_resolutions = {}

            for f in formats:
                h = f.get('height')
                if h and h in [144, 240, 360, 480, 720, 1080, 1440, 2160]:
                    v_size = f.get('filesize') or f.get('filesize_approx')
                    if not v_size and f.get('tbr') and duration:
                        v_size = (f.get('tbr') * 1000 / 8) * duration

                    total_bytes = (v_size or 0) + audio_size
                    if h not in available_resolutions or total_bytes > available_resolutions[h]:
                        available_resolutions[h] = total_bytes

            format_list = []
            if not available_resolutions:
                available_resolutions = {1080: 0, 720: 0, 480: 0, 360: 0}

            for h in sorted(available_resolutions.keys(), reverse=True):
                est_bytes = available_resolutions[h]
                if est_bytes > 0:
                    mb = est_bytes / (1024 * 1024)
                    size_str = f"약 {mb:.1f} MB" if mb < 1000 else f"약 {mb/1024:.2f} GB"
                else:
                    size_str = "용량 최적화"

                label = f"{h}p"
                if h >= 1080: label += " (FHD 고화질)"
                elif h >= 720: label += " (HD 표준)"
                elif h >= 480: label += " (일반화질)"
                else: label += " (저용량)"

                format_list.append({
                    'height': h,
                    'label': label,
                    'size_str': size_str
                })

            return jsonify({
                'success': True,
                'title': title,
                'formats': format_list
            })
    except Exception as e:
        return jsonify({
            'success': True,
            'title': 'YouTube 동영상',
            'formats': [
                {'height': 1080, 'label': '1080p (FHD 고화질)', 'size_str': '용량 최적화'},
                {'height': 720, 'label': '720p (HD 표준)', 'size_str': '용량 최적화'},
                {'height': 480, 'label': '480p (일반화질)', 'size_str': '용량 최적화'},
                {'height': 360, 'label': '360p (저용량)', 'size_str': '용량 최적화'}
            ]
        })

# ----------------- [대본 추출 API] -----------------
@app.route('/api/transcript', methods=['POST'])
def get_transcript():
    if 'user_id' in session:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_banned FROM users WHERE id = ?", (session['user_id'],))
            row = cursor.fetchone()
            if row and row[0] == 1: return jsonify({'error': '이용이 정지된 계정입니다.'}), 403
    else:
        quota = get_today_usage_info()
        if quota['remaining'] != '무제한' and quota['remaining'] <= 0:
            return jsonify({'error': '오늘 무료 한도를 소진하셨습니다. 회원가입 시 무제한 이용이 가능합니다!'}), 429

    data = request.json
    url = data.get('url', '').strip()
    target_lang = data.get('lang', 'en')

    video_id = extract_video_id(url)
    if not video_id: return jsonify({'error': '유효한 유튜브 URL이 아닙니다.'}), 400

    clean_url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        **get_base_ydl_opts(),
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['all']
    }

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

# ----------------- [미디어 다운로드 API] -----------------
@app.route('/api/download')
def download_media():
    if 'user_id' in session:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_banned FROM users WHERE id = ?", (session['user_id'],))
            row = cursor.fetchone()
            if row and row[0] == 1: return "이용이 정지된 계정입니다.", 403
    else:
        quota = get_today_usage_info()
        if quota['remaining'] != '무제한' and quota['remaining'] <= 0:
            return "오늘 무료 한도를 모두 소진하셨습니다. 회원가입 시 무제한 이용이 가능합니다.", 429

    url = request.args.get('url', '').strip()
    download_type = request.args.get('type', 'mp3')
    height = request.args.get('quality', 'best')

    video_id = extract_video_id(url)
    if not video_id: return "유효한 유튜브 URL이 아닙니다.", 400

    clean_url = f"https://www.youtube.com/watch?v={video_id}"
    base_filename = os.path.join(DOWNLOAD_FOLDER, f"{video_id}_{download_type}_{height}")

    for f in glob.glob(f"{base_filename}.*"):
        try: os.remove(f)
        except: pass

    base_ydl_opts = get_base_ydl_opts()

    if download_type == 'mp3':
        ydl_opts = {
            **base_ydl_opts,
            'format': 'ba/b',
            'outtmpl': f"{base_filename}.%(ext)s",
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        }
    else:
        if height != 'best' and height.isdigit():
            format_str = f"best[height<={height}]/bestvideo[height<={height}]+bestaudio/best"
        else:
            format_str = "best/bestvideo+bestaudio"

        ydl_opts = {
            **base_ydl_opts,
            'format': format_str,
            'outtmpl': f"{base_filename}.%(ext)s",
            'merge_output_format': 'mp4',
            'postprocessor_args': ['-movflags', 'faststart'],
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            video_title = info.get('title', 'download')
            safe_title = re.sub(r'[\\/*?:"<>|]', "", video_title)[:60]

        target_ext = 'mp3' if download_type == 'mp3' else 'mp4'
        expected_file = f"{base_filename}.{target_ext}"
        if not os.path.exists(expected_file):
            matched = glob.glob(f"{base_filename}.*")
            if matched: expected_file = matched[0]
            else: return "파일 생성 실패", 500

        record_usage()

        @after_this_request
        def cleanup(response):
            try:
                if os.path.exists(expected_file): os.remove(expected_file)
            except: pass
            return response

        quality_suffix = f"_{height}p" if download_type == 'mp4' and height.isdigit() else ""
        return send_file(expected_file, as_attachment=True, download_name=f"{safe_title}{quality_suffix}.{target_ext}")

    except Exception as e:
        return f"다운로드 오류: {str(e)}", 500

@app.route('/api/board', methods=['GET'])
def get_posts():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT author, content, created_at FROM board_posts ORDER BY id DESC LIMIT 20")
        rows = cursor.fetchall()
    return jsonify([{'author': r[0], 'content': r[1], 'created_at': r[2]} for r in rows])

@app.route('/api/board', methods=['POST'])
def add_post():
    data = request.json or {}
    content = data.get('content', '').strip()
    if not content: return jsonify({'error': '내용을 입력해주세요.'}), 400
    author = session.get('user_email', '익명 우주인')
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO board_posts (author, content) VALUES (?, ?)", (author, content))
        conn.commit()
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(debug=True, port=5000)