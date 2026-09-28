# Instagram 사진 출처 찾기 봇

가지고 있는 **사진 한 장**으로 인스타그램에서 그 사진이 올라간 **게시물**과 **올린 사용자**를 찾아주는 봇입니다.
텔레그램 봇으로 쓰거나, 명령줄(CLI)에서 바로 쓸 수 있습니다.

```
🔎 인스타그램 후보 3건을 찾았어요 (완전 일치 2건).

1. [완전 일치] 게시물 · 유사도 98%
   올린 사람: @gildong (Gil Dong)
   링크: https://www.instagram.com/p/C1a2B3c4D5e/
   프로필: https://www.instagram.com/gildong/
   날짜: 2 years ago
   제목: Gil Dong on Instagram: "Sunset in Jeju"

2. [완전 일치] 프로필 · 유사도 91%
   올린 사람: @repost_account (Repost)
   프로필: https://www.instagram.com/repost_account/
...
```

## 동작 방식

인스타그램은 "사진으로 게시물 찾기" 기능이나 공식 API 를 제공하지 않습니다.
그래서 이 봇은 **Google Lens 역이미지 검색**([SerpApi](https://serpapi.com/google-lens-api) 경유) 결과에서
`instagram.com` 링크만 골라내는 방식으로 동작합니다. 인스타그램에 직접 로그인하거나 크롤링하지 않습니다.

1. 사진을 JPEG 으로 다시 압축해 500KB 이하로 줄입니다. (이 과정에서 GPS 등 EXIF 정보가 제거됩니다)
2. SerpApi 에 업로드한 뒤 Google Lens 로 **완전 일치(exact_matches)** → **유사 이미지(visual_matches)** 순서로 검색합니다.
3. 결과 중 인스타그램 게시물/릴스/스토리/프로필 링크만 추려 중복을 합칩니다.
4. 링크(`instagram.com/<사용자>/p/<코드>/`)나 제목(`이름 (@사용자)`)에서 **올린 사용자**를 알아냅니다.
5. 검색 결과 썸네일을 내려받아 원본 사진과의 **유사도**(지각 해시)를 계산하고, 신뢰도 순으로 정렬합니다.
6. (선택) 사용자 이름을 알 수 없는 게시물은 Google 검색 스니펫(`... - gildong on March 3, 2024`)으로 게시자를 보완합니다.

## 준비물

| 항목 | 설명 |
| --- | --- |
| Python 3.10+ | |
| SerpApi API 키 | [serpapi.com](https://serpapi.com) 가입 후 [API 키 확인](https://serpapi.com/manage-api-key). 무료 플랜 제공 (월 제공량은 요금제 참고) |
| 텔레그램 봇 토큰 | 텔레그램 봇으로 쓸 때만 필요. 텔레그램에서 [@BotFather](https://t.me/BotFather) → `/newbot` |

## 설치

```bash
git clone https://github.com/loadmin4/Instargram_load_bot.git
cd Instargram_load_bot
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # 그리고 .env 에 키를 입력
```

아이폰 HEIC 사진을 CLI 로 바로 검색하려면 `pip install pillow-heif` 도 설치하세요.

## 사용법

### 1) 텔레그램 봇

```bash
python -m insta_finder.telegram_bot
```

봇과의 대화창에 사진을 보내면 결과를 답장합니다. 화질을 유지하려면 사진을 **파일로** 보내도 됩니다.

> 검색할 때마다 SerpApi 크레딧이 사용되므로, `.env` 의 `ALLOWED_USER_IDS` 에 본인 텔레그램 ID 를 넣어
> 다른 사람이 봇을 쓰지 못하게 하는 것을 권장합니다. 권한이 없는 사용자가 사진을 보내면 봇이 그 사람의 ID 를 알려주므로,
> 처음엔 비워둔 채 한 번 보내보고 ID 를 확인해도 됩니다.

### 2) 명령줄(CLI)

```bash
python -m insta_finder 사진.jpg
python -m insta_finder 사진.jpg --json                 # JSON 출력
python -m insta_finder 사진.jpg --types exact_matches  # 완전 일치만 (크레딧 절약)
python -m insta_finder 사진.jpg --resolve-usernames 3  # 게시자 모르는 상위 3건 추가 조회
```

`pip install -e .` 로 설치하면 `insta-finder 사진.jpg`, `insta-finder-bot` 명령으로도 실행할 수 있습니다.

## 설정 (.env)

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `SERPAPI_API_KEY` | (필수) | SerpApi API 키 |
| `TELEGRAM_BOT_TOKEN` | | 텔레그램 봇 토큰 (봇 실행 시 필수) |
| `ALLOWED_USER_IDS` | 비어 있음(모두 허용) | 봇 사용을 허락할 텔레그램 사용자 ID, 쉼표로 구분 |
| `SEARCH_TYPES` | `exact_matches,visual_matches` | 검색 종류. 종류 하나당 크레딧 1회 사용 |
| `MAX_RESULTS` | `10` | 보여줄 최대 결과 수 |
| `LENS_LANGUAGE` | `en` | Google Lens 언어. 제목 형식이 일정해 사용자 이름 추출이 잘 되므로 `en` 권장 |
| `LENS_COUNTRY` | | Google Lens 국가 코드 (예: `kr`) |
| `LENS_QUERY` | | 사진과 함께 넣을 검색어 (예: `instagram`) |
| `VERIFY_THUMBNAILS` | `1` | 썸네일 유사도 계산 여부 |
| `RESOLVE_USERNAMES` | `0` | 게시자를 모르는 상위 N개 게시물을 Google 검색으로 추가 조회 (N 크레딧 추가) |

**크레딧 사용량**: 기본 설정에서 사진 1장당 Lens 검색 2회(완전 일치 + 유사 이미지)가 사용되고,
`RESOLVE_USERNAMES` 를 켜면 그만큼 추가됩니다.

## 한계

- **공개 계정 + Google 에 색인된 게시물만** 찾을 수 있습니다. 비공개 계정, 최근 올라온 게시물, 스토리 대부분은 찾기 어렵습니다.
- 크게 자르거나, 필터·글자를 입힌 사진, 화면 캡처는 정확도가 떨어집니다. 원본에 가까울수록 잘 찾습니다.
- 찾은 계정이 **최초 게시자가 아니라 퍼간(리포스트) 계정**일 수 있습니다. 여러 후보의 날짜를 비교해 보세요.
- `instagram.com/p/<코드>/` 형태의 링크는 URL 에 사용자 이름이 없어서, 제목에 표시 이름만 있으면
  "표시 이름"으로만 보여줍니다. 이때는 `RESOLVE_USERNAMES` 를 켜거나 링크를 직접 열어 확인하세요.
  (Meta 의 oEmbed API 는 2025년 11월부터 게시자 정보를 돌려주지 않아 사용하지 않습니다.)

## 올바른 사용

이 도구는 사진의 **출처 확인, 저작권자 표기, 무단 도용 확인** 같은 용도를 위한 것입니다.
찾은 정보로 다른 사람을 추적하거나 괴롭히는 데 사용하지 마세요. SerpApi 및 Google, Instagram 의 이용 약관을 따르세요.

## 프로젝트 구조

```
insta_finder/
  finder.py          검색 흐름 (업로드 → Lens 검색 → 인스타그램 링크 추출 → 유사도 정렬)
  serpapi_client.py  SerpApi 이미지 업로드 / Google Lens / Google 검색 클라이언트
  instagram.py       인스타그램 URL·제목·스니펫에서 게시물/사용자 이름 추출
  image_utils.py     업로드용 압축, EXIF 제거, dHash 유사도
  formatting.py      결과 메시지 (한국어)
  config.py          .env 설정
  cli.py             명령줄 도구
  telegram_bot.py    텔레그램 봇
tests/               pytest 테스트 (네트워크 없이 가짜 응답으로 검증)
```

## 테스트

```bash
pip install pytest
python -m pytest
```
