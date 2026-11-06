import sys
import traceback
from importlib.metadata import version, PackageNotFoundError
from youtube_transcript_api import YouTubeTranscriptApi

print("=" * 50)
print(f"1. 파이썬 버전: {sys.version.split()[0]}")

# 안전한 방식으로 라이브러리 버전 확인
try:
    pkg_ver = version("youtube-transcript-api")
    print(f"2. 라이브러리 버전: {pkg_ver}")
except PackageNotFoundError:
    print("2. 라이브러리 버전: 설치 정보를 찾을 수 없음")
print("=" * 50)

# 스티브 잡스 스탠포드 연설 비디오 ID
video_id = "UF8uR6Z6KLc"

try:
    print("\n[테스트 1] 유튜브에 존재하는 자막 목록(List) 조회 중...")
    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
    print(">> [성공] 자막 목록을 정상적으로 가져왔습니다!")
    
    print("\n[발견된 자막 언어들]")
    for t in transcript_list:
        print(f"   - 언어: {t.language} (코드: {t.language_code}) / 자동생성: {t.is_generated}")

    print("\n[테스트 2] 실제 영어(en) 자막 텍스트 다운로드 시도 중...")
    transcript = transcript_list.find_transcript(['en'])
    data = transcript.fetch()
    
    print(f">> [성공] 총 {len(data)}개의 대사 라인을 가져왔습니다!")
    print(f">> 첫 번째 대사 미리보기: \"{data[0]['text']}\"")
    print("\n🎉 축하합니다! 유튜브와 정상적으로 통신하고 있습니다.")

except Exception as e:
    print("\n❌ 통신 중 오류가 발생했습니다!")
    print("-" * 50)
    traceback.print_exc()
    print("-" * 50)