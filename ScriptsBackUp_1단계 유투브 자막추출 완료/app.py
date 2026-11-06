from flask import Flask, render_template, request, jsonify
import yt_dlp
import re
import json
import urllib.request

app = Flask(__name__)

def extract_video_id(url):
    """다양한 유튜브 링크 형태에서 11자리 비디오 ID 추출"""
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11}).*",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
        r"embed\/([0-9A-Za-z_-]{11})"
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def format_time(seconds):
    """초 단위를 00:00 형태로 변환"""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/transcript', methods=['POST'])
def get_transcript():
    data = request.json
    url = data.get('url', '').strip()
    target_lang = data.get('lang', 'en') # 'en' 또는 'ko'

    video_id = extract_video_id(url)
    if not video_id:
        return jsonify({'error': '유효한 유튜브 URL이 아닙니다.'}), 400

    clean_url = f"https://www.youtube.com/watch?v={video_id}"

    # 유튜브 429 차단을 회피하기 위한 완벽한 브라우저 모방 헤더
    browser_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Referer': 'https://www.youtube.com/',
        'Origin': 'https://www.youtube.com'
    }

    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['all'],
        'quiet': True,
        'no_warnings': True,
        'http_headers': browser_headers
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 1. 동영상 자막 정보 추출
            info = ydl.extract_info(clean_url, download=False)
            
            subtitles = info.get('subtitles', {})
            auto_subtitles = info.get('automatic_captions', {})

            target_tracks = None
            need_translate = False

            # (1) 사용자가 원하는 언어의 자막이 있는지 확인
            if target_lang in subtitles:
                target_tracks = subtitles[target_lang]
            elif target_lang in auto_subtitles:
                target_tracks = auto_subtitles[target_lang]
            else:
                # (2) 원하는 언어가 없다면 다른 언어(영어 등)를 가져와 유튜브 자동번역 준비
                need_translate = True
                fallback_lang = 'en' if target_lang == 'ko' else 'ko'
                target_tracks = subtitles.get(fallback_lang) or auto_subtitles.get(fallback_lang)

                if not target_tracks:
                    all_tracks = list(subtitles.values()) + list(auto_subtitles.values())
                    if all_tracks:
                        target_tracks = all_tracks[0]

            if not target_tracks:
                return jsonify({'error': '이 영상에는 자막이 제공되지 않습니다.'}), 404

            # JSON 형식의 자막 다운로드 주소(URL) 찾기
            download_url = None
            for item in target_tracks:
                if item.get('ext') == 'json3':
                    download_url = item.get('url')
                    break
            if not download_url:
                download_url = target_tracks[0].get('url')

            # 원본에 없는 언어일 경우, 유튜브 자동 번역 파라미터(&tlang=) 추가
            if need_translate:
                separator = '&' if '?' in download_url else '?'
                download_url += f"{separator}tlang={target_lang}"

            # 2. 자막 데이터 안전 다운로드 (내부 클래스 에러 없이 URL 문자열 직접 전달)
            try:
                with ydl.urlopen(download_url) as resp:
                    content = resp.read().decode('utf-8')
            except Exception:
                # ydl.urlopen이 실패할 경우를 대비한 2차 안전망
                req = urllib.request.Request(download_url, headers=browser_headers)
                with urllib.request.urlopen(req) as resp:
                    content = resp.read().decode('utf-8')

            sub_data = json.loads(content)

            plain_text_list = []
            timestamp_text_list = []

            # 3. JSON3 자막 데이터 파싱
            events = sub_data.get('events', [])
            for ev in events:
                if 'segs' in ev:
                    text_parts = [seg.get('utf8', '') for seg in ev['segs']]
                    full_text = "".join(text_parts).replace('\n', ' ').strip()
                    if full_text:
                        start_sec = ev.get('tStartMs', 0) / 1000.0
                        start_time = format_time(start_sec)
                        plain_text_list.append(full_text)
                        timestamp_text_list.append(f"[{start_time}] {full_text}")

            if not plain_text_list:
                return jsonify({'error': '자막 데이터가 비어 있습니다.'}), 404

            return jsonify({
                'success': True,
                'plain_text': "\n".join(plain_text_list),
                'timestamp_text': "\n".join(timestamp_text_list)
            })

    except Exception as e:
        return jsonify({'error': f'대본 추출 실패: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)