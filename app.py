from flask import Flask, render_template, request, jsonify, send_file, after_this_request, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, timedelta
from time import gmtime, strftime
import yt_dlp
import re
import json
import urllib.request
import hmac
import hashlib
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

# ==============================================================
# [대안 2: 주거용 프록시 설정 지점]
# 추후 유료 프록시 발급 시 'http://아이디:비밀번호@ip:port' 형태로 입력하세요.
# 비워두면 로컬/오라클 직접 연결 모드로 안전하게 동작합니다.
# ==============================================================
RESIDENTIAL_PROXY_URL = os.environ.get('YOUTUBE_PROXY', '')

# ----------------- [쿠팡 파트너스 공식 API HMAC 연동] -----------------
COUPANG_ACCESS_KEY = "9f8ecd0b-a35b-46eb-96ae-09bbeecf441b"
COUPANG_SECRET_KEY = "f344e15c7daedb64065b05f879dd12c635b77b3b"
COUPANG_DOMAIN = "https://api-gateway.coupang.com"
COUPANG_URL_PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink"

def generate_coupang_hmac(method, url_path, secret_key, access_key):
    path, *query = url_path.split("?")
    datetime_gmt = strftime('%y%m%d', gmtime()) + 'T' + strftime('%H%M%S', gmtime()) + 'Z'
    message = datetime_gmt + method + path + (query[0] if query else "")

    signature = hmac.new(bytes(secret_key, "utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"CEA algorithm=HmacSHA256, access-key={access_key}, signed-date={datetime_gmt}, signature={signature}"

def get_dynamic_coupang_link():
    default_fallback_link = "https://link.coupang.com/a/idfYJANZHU"
    target_coupang_urls = ["https://www.coupang.com/np/goldbox", "https://www.coupang.com/np/categories/194276"]
    
    try:
        auth_header = generate_coupang_hmac("POST", COUPANG_URL_PATH, COUPANG_SECRET_KEY, COUPANG_ACCESS_KEY)
        api_full_url = f"{COUPANG_DOMAIN}{COUPANG_URL_PATH}"
        req_body = json.dumps({"coupangUrls": target_coupang_urls}).encode('utf-8')
        req = urllib.request.Request(
            api_full_url, data=req_body,
            headers={"Authorization": auth_header, "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=4) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            if res_data.get("rCode") == "0" and res_data.get("data"):
                return res_data["data"][0].get("shortenUrl", default_fallback_link)
    except Exception:
        pass
    return default_fallback_link

# ----------------- [데이터베이스 및 방문자 집계 시스템] -----------------
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
                action_type TEXT DEFAULT 'download',
                use_date TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_visitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                visit_date TEXT NOT NULL,
                user_agent TEXT,
                UNIQUE(ip_address, visit_date)
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

@app.before_request
def track_visitor():
    if request.path.startswith('/static') or request.path.startswith('/api/auth/status'):
        return
    today_str = date.today().isoformat()
    client_ip = request.remote_addr or 'unknown'
    user_agent = request.headers.get('User-Agent', '')[:100]

    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO daily_visitors (ip_address, visit_date, user_agent) VALUES (?, ?, ?)",
                (client_ip, today_str, user_agent)
            )
            conn.commit()
    except Exception:
        pass

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

    return {
        'is_logged_in': is_member,
        'email': session.get('user_email', ''),
        'is_admin': is_admin(),
        'used': used_count,
        'max': "무제한" if is_member else 5,
        'remaining': "무제한" if is_member else max(0, 5 - used_count),
    }

def record_usage(action='download'):
    identifier = get_user_identifier()
    today_str = date.today().isoformat()
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO usage_logs (identifier, action_type, use_date) VALUES (?, ?, ?)",
            (identifier, action, today_str)
        )
        conn.commit()

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

# 봇 우회 초기 메타데이터 전용 옵션 (프록시 태움: 트래픽 0.05MB 극소량 소모)
def get_meta_ydl_opts(extra=None):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        }
    }
    if RESIDENTIAL_PROXY_URL:
        opts['proxy'] = RESIDENTIAL_PROXY_URL
    if os.path.exists(COOKIES_FILE):
        opts['cookiefile'] = COOKIES_FILE
    if extra:
        opts.update(extra)
    return opts

# ----------------- [기본 라우팅 및 SEO 사이트맵] -----------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/robots.txt')
def robots():
    content = "User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\nSitemap: " + request.host_url + "sitemap.xml"
    return content, 200, {'Content-Type': 'text/plain'}

@app.route('/sitemap.xml')
def sitemap():
    base = request.host_url.rstrip('/')
    today = date.today().isoformat()
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{base}/</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>"""
    return xml, 200, {'Content-Type': 'application/xml'}

@app.route('/api/sponsor/coupang')
def get_coupang_ad_url():
    return jsonify({'success': True, 'url': get_dynamic_coupang_link()})

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
    return jsonify({'success': True, 'email': email, 'is_admin': bool(row[2])})

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

# ----------------- [관리자 통계 API] -----------------
@app.route('/admin')
def admin_page():
    if not is_admin():
        return redirect('/')
    return render_template('admin.html')

@app.route('/api/admin/stats')
def admin_stats():
    if not is_admin():
        return jsonify({'error': '권한 없음'}), 403

    today_str = date.today().isoformat()
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(DISTINCT ip_address) FROM daily_visitors WHERE visit_date = ?", (today_str,))
        today_visitors = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM daily_visitors")
        total_visits = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM usage_logs WHERE use_date = ?", (today_str,))
        today_actions = cursor.fetchone()[0]

        cursor.execute('''
            SELECT visit_date, COUNT(DISTINCT ip_address) 
            FROM daily_visitors 
            GROUP BY visit_date 
            ORDER BY visit_date DESC LIMIT 7
        ''')
        daily_trends = [{'date': r[0], 'count': r[1]} for r in cursor.fetchall()]

    return jsonify({
        'today_visitors': today_visitors,
        'total_visits': total_visits,
        'today_actions': today_actions,
        'daily_trends': daily_trends
    })

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

# ----------------- [대본 추출 API] -----------------
@app.route('/api/transcript', methods=['POST'])
def get_transcript():
    data = request.json or {}
    url = data.get('url', '').strip()
    target_lang = data.get('lang', 'en')

    video_id, clean_url = extract_clean_video_info(url)
    if not video_id: return jsonify({'error': '유효한 유튜브 URL이 아닙니다.'}), 400

    ydl_opts = get_meta_ydl_opts({
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

            req = urllib.request.Request(download_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as resp:
                content = resp.read().decode('utf-8')

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

            record_usage('transcript')
            return jsonify({
                'success': True,
                'title': video_title,
                'plain_text': "\n".join(plain_text_list),
                'timestamp_text': "\n".join(timestamp_text_list)
            })

    except Exception as e:
        return jsonify({'error': f'대본 추출 실패: {str(e)}'}), 500

# ----------------- [핵심: 프록시 요금 99.9% 절약 하이브리드 미디어 다운로드 엔진] -----------------
@app.route('/api/download/execute', methods=['POST'])
def execute_download():
    if 'user_id' in session:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_banned FROM users WHERE id = ?", (session['user_id'],))
            row = cursor.fetchone()
            if row and row[0] == 1: return jsonify({'error': '이용이 정지된 계정입니다.'}), 403
    else:
        quota = get_today_usage_info()
        if quota['remaining'] != '무제한' and quota['remaining'] <= 0:
            return jsonify({'error': '오늘 무료 한도를 모두 소진하셨습니다. 회원가입 시 무제한 이용이 가능합니다.'}), 429

    data = request.json or {}
    raw_url = data.get('url', '').strip()
    download_type = data.get('type', 'mp3')

    video_id, clean_url = extract_clean_video_info(raw_url)
    if not video_id: return jsonify({'error': '유효한 유튜브 URL이 아닙니다.'}), 400

    # 1. [비용 방어막] 영상 길이 사전 검사 (60분 초과 대용량 파일 다운로드 차단)
    # 봇 차단 우회용 가벼운 메타데이터만 조회 (프록시 소모량: 0.05MB 극소량)
    meta_opts = get_meta_ydl_opts({'skip_download': True})
    try:
        with yt_dlp.YoutubeDL(meta_opts) as ydl:
            meta_info = ydl.extract_info(clean_url, download=False)
            duration = meta_info.get('duration', 0)
            if duration > 3600:
                return jsonify({'error': '무료 서버 안정성을 위해 60분 이내의 클립 영상만 변환이 가능합니다.'}), 400
    except Exception as e:
        pass

    base_filename = os.path.join(DOWNLOAD_FOLDER, f"{video_id}_{download_type}")

    for f in glob.glob(f"{base_filename}.*"):
        try: os.remove(f)
        except: pass

    # 2. [비용 절감 핵심] 본문 1GB 다운로드 시에는 유료 프록시를 완전히 끄고
    #    오라클/로컬 자체의 '무제한 무료 대역폭'으로 다이렉트 고속 다운로드
    download_opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'proxy': '',  # 대용량 다운로드 시 프록시 경유 해제 -> 요금 청구 0원
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        }
    }
    if os.path.exists(COOKIES_FILE):
        download_opts['cookiefile'] = COOKIES_FILE

    if download_type == 'mp3':
        download_opts.update({
            'format': 'ba/b',
            'outtmpl': f"{base_filename}.%(ext)s",
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        })
    else:
        # 단일 최적 파일 우선, 없으면 자동 결합
        download_opts.update({
            'format': 'b/bv*+ba/best',
            'outtmpl': f"{base_filename}.%(ext)s",
            'merge_output_format': 'mp4',
            'postprocessor_args': ['-movflags', 'faststart'],
        })

    try:
        with yt_dlp.YoutubeDL(download_opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            video_title = info.get('title', 'YouTube_Media')
            safe_title = re.sub(r'[\\/*?:"<>|]', "", video_title)[:60]

        target_ext = 'mp3' if download_type == 'mp3' else 'mp4'
        expected_file = f"{base_filename}.{target_ext}"
        if not os.path.exists(expected_file):
            matched = glob.glob(f"{base_filename}.*")
            if matched: expected_file = matched[0]
            else: return jsonify({'error': '미디어 파일 변환 실패'}), 500

        record_usage('download')
        
        file_id = os.path.basename(expected_file)
        final_filename = f"{safe_title}.{target_ext}"

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
    return jsonify([])

if __name__ == '__main__':
    app.run(port=5000)