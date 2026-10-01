# Instagram 사진 출처 찾기 봇

가지고 있는 **사진 한 장**으로 인스타그램에서 그 사진이 올라간 **게시물**과 **올린 사용자**를 찾아주는 봇입니다.
텔레그램 봇으로 쓰거나, 명령줄(CLI)에서 바로 쓸 수 있습니다.

```
🔎 @gildong, #제주도 의 게시물 132개를 비교해서 같은 사진 1건을 찾았어요.

1. [직접 비교] 게시물 · 유사도 97%
   올린 사람: @gildong
   링크: https://www.instagram.com/p/C1a2B3c4D5e/
   프로필: https://www.instagram.com/gildong/
   날짜: 2024-03-03
   캡션: 제주 노을 🌅
```

## 검색 방식 두 가지

인스타그램에는 로그인을 해도 **"사진으로 검색" 기능이 없습니다.** 그래서 두 가지 방식을 제공합니다.

| | 1) 크롤링 (기본, 무료) | 2) Google Lens (선택, 유료 API) |
| --- | --- | --- |
| 방법 | 인스타그램에 로그인해 **지정한 @계정 / #해시태그**의 게시물 사진을 하나씩 내려받아 비교 | [SerpApi](https://serpapi.com/google-lens-api) 로 Google Lens 역이미지 검색 후 인스타그램 링크만 추림 |
| 검색 범위 | 내가 지정한 대상의 게시물 (대상 하나당 기본 최근 200개) | 인스타그램 전체 (Google 에 색인된 공개 게시물) |
| 필요한 것 | 인스타그램 계정 | SerpApi API 키 |
| 올린 사람 | 항상 정확히 나옴 | 링크/제목에 있으면 나옴 (로그인 세션이 있으면 보완) |
| 주의 | 인스타그램 약관상 자동 수집 금지 → **계정 제한 위험** | 사진 1장당 크레딧 2회 |

둘 다 설정하면, 텔레그램에서 **캡션에 @계정/#해시태그를 적으면 크롤링**, **캡션 없이 보내면 Google Lens** 로 찾습니다.

### 크롤링은 어떻게 비교하나요?

1. 지정한 계정의 최신 게시물(해시태그는 인기 게시물 → 최근 게시물 순)을 가져옵니다.
2. 게시물 이미지(여러 장 게시물은 모든 장, 릴스는 표지)를 내려받아 원본 사진과 **지각 해시(dHash)** 로 비교합니다.
   인스타그램이 1:1, 4:5 로 잘라 올린 사진도 비율을 맞춰 비교합니다.
3. 유사도가 기준(기본 0.82) 이상이면 같은 사진으로 판단하고, 3건을 찾으면 멈춥니다.

## ⚠️ 크롤링 전에 꼭 읽어주세요

- 인스타그램 [이용약관](https://help.instagram.com/581066165581870)은 허가 없는 자동 수집을 금지합니다.
  사용 시 **로그인 차단, 보안 확인 요청, 계정 정지**가 생길 수 있으며 그 책임은 사용자에게 있습니다.
- **본 계정 대신 검색용 부계정**을 쓰세요.
- 요청을 줄이기 위해 이 봇은
  - 로그인 세션을 파일에 저장해 재사용하고 (매번 로그인하지 않음),
  - 게시물마다 추가 요청을 보내지 않으며 (목록 응답에 있는 이미지 주소만 사용),
  - [instaloader](https://instaloader.github.io/) 의 기본 요청 속도 제한을 따르고,
  - 크롤링 검색은 한 번에 하나만 실행합니다.
- 그래도 너무 자주, 많이 검색하면 제한에 걸립니다. `CRAWL_MAX_POSTS` 를 필요 이상으로 키우지 마세요.
- 비공개 계정은 내 계정이 팔로우하고 있을 때만 볼 수 있습니다.

## 설치

```bash
git clone https://github.com/loadmin4/Instargram_load_bot.git
cd Instargram_load_bot
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # 그리고 .env 를 채우기
```

아이폰 HEIC 사진을 CLI 로 바로 검색하려면 `pip install pillow-heif` 도 설치하세요.

### 인스타그램 로그인 (크롤링용, 한 번만)

```bash
python -m insta_finder.login
```

아이디·비밀번호(화면에 안 보임)·2단계 인증 코드를 입력하면 `instagram.session` 파일에 로그인 세션이 저장됩니다.
이후 봇과 CLI 는 이 세션을 재사용하므로 **비밀번호를 .env 에 적지 않아도 됩니다.**
세션 파일은 비밀번호와 같으니 공유하거나 git 에 올리지 마세요 (`.gitignore` 에 포함됨).

인스타그램이 "본인 확인"을 요구하면 인스타그램 앱/웹에서 승인한 뒤 다시 실행하세요.
세션이 만료되면 봇이 알려주며, 같은 명령으로 다시 로그인하면 됩니다.

## 사용법

### 1) 텔레그램 봇

```bash
python -m insta_finder.telegram_bot
```

텔레그램 봇 토큰은 [@BotFather](https://t.me/BotFather) → `/newbot` 으로 받습니다.

- **크롤링**: 사진을 보내면서 사진 설명(캡션)에 `@gildong #제주도` 처럼 뒤질 대상을 적습니다.
  진행 상황("게시물 120개 비교했어요")이 표시되고, 끝나면 결과로 바뀝니다.
- **Google Lens**: (SerpApi 키가 있을 때) 캡션 없이 사진만 보냅니다.

> `.env` 의 `ALLOWED_USER_IDS` 에 본인 텔레그램 ID 를 꼭 넣으세요. 비워두면 **아무나 내 인스타그램 계정으로 크롤링**할 수 있습니다.
> 권한이 없는 사용자가 사진을 보내면 봇이 그 사람의 ID 를 알려주므로, 처음엔 비워둔 채 한 번 보내보고 ID 를 확인해도 됩니다.

### 2) 명령줄(CLI)

```bash
# 크롤링 (# 은 셸에서 주석이므로 따옴표로 감싸세요)
python -m insta_finder 사진.jpg -t @gildong
python -m insta_finder 사진.jpg -t @gildong -t "#제주도" --max-posts 500

# Google Lens (SERPAPI_API_KEY 필요)
python -m insta_finder 사진.jpg
python -m insta_finder 사진.jpg --types exact_matches   # 완전 일치만 (크레딧 절약)

python -m insta_finder 사진.jpg -t @gildong --json      # JSON 출력
```

`pip install -e .` 로 설치하면 `insta-finder`, `insta-finder-login`, `insta-finder-bot` 명령으로도 실행할 수 있습니다.

## 설정 (.env)

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `INSTAGRAM_USERNAME` | | 크롤링에 쓸 인스타그램 아이디. 설정하면 크롤링이 켜짐 |
| `INSTAGRAM_PASSWORD` | | 비워두고 `python -m insta_finder.login` 사용 권장 |
| `INSTAGRAM_SESSION_FILE` | `instagram.session` | 로그인 세션 파일 위치 |
| `CRAWL_MAX_POSTS` | `200` | 대상 하나당 최대로 비교할 게시물 수 |
| `CRAWL_MATCH_THRESHOLD` | `0.82` | 같은 사진으로 판단할 유사도 (0~1). 오탐이 있으면 올리고, 못 찾으면 내리세요 |
| `CRAWL_MAX_MATCHES` | `3` | 이만큼 찾으면 중단 (0 = 끝까지) |
| `CRAWL_MAX_TARGETS` | `5` | 한 번에 지정할 수 있는 대상 수 |
| `TELEGRAM_BOT_TOKEN` | | 텔레그램 봇 토큰 (봇 실행 시 필수) |
| `ALLOWED_USER_IDS` | 비어 있음(모두 허용) | 봇 사용을 허락할 텔레그램 사용자 ID, 쉼표로 구분 |
| `SERPAPI_API_KEY` | | 설정하면 Google Lens 검색이 켜짐 |
| `SEARCH_TYPES` | `exact_matches,visual_matches` | Lens 검색 종류. 종류 하나당 크레딧 1회 |
| `MAX_RESULTS` | `10` | Lens 결과 최대 수 |
| `LENS_LANGUAGE` / `LENS_COUNTRY` | `en` / | Google Lens 언어·국가 |
| `LENS_QUERY` | | 사진과 함께 넣을 검색어 (예: `instagram`) |
| `VERIFY_THUMBNAILS` | `1` | Lens 결과 썸네일과 유사도 계산 |
| `RESOLVE_USERNAMES` | 로그인 시 `3`, 아니면 `0` | Lens 결과 중 게시자를 모르는 상위 N개를 추가 조회 (로그인 세션 또는 Google 검색 이용) |

## 한계

- 크롤링은 **지정한 대상 안에서만** 찾습니다. 어디 올라갔는지 전혀 모르면 Google Lens 방식을 쓰거나,
  사진과 관련 있을 만한 해시태그(장소, 행사 이름 등)를 여러 개 지정해 보세요.
- 해시태그는 인스타그램이 보여주는 인기/최근 게시물만 볼 수 있어 오래된 게시물은 찾기 어렵습니다.
- 인스타그램 내부 구조가 바뀌면 크롤링이 깨질 수 있습니다. 그럴 땐 `pip install -U instaloader` 로 업데이트하세요.
- 크게 자르거나 필터·글자를 입힌 사진, 화면 캡처는 정확도가 떨어집니다.
- 찾은 계정이 **최초 게시자가 아니라 퍼간 계정**일 수 있습니다. 날짜를 비교해 보세요.

### 인스타그램 공식 API 는 왜 안 쓰나요?

공식 API(Instagram Graph API)로는 이 용도를 해결할 수 없습니다.

- 사진으로 검색하는 기능이 없습니다.
- 내 계정과 다른 사람 계정 모두 **비즈니스/크리에이터 계정**이어야 하고, 개인 계정 게시물은 볼 수 없습니다.
- 해시태그 검색은 7일에 30개로 제한되고, 결과에 **게시자 아이디가 나오지 않습니다.**
- 개인 계정용 Basic Display API 는 종료되었습니다.

## 올바른 사용

이 도구는 사진의 **출처 확인, 저작권자 표기, 무단 도용 확인** 같은 용도를 위한 것입니다.
찾은 정보로 다른 사람을 추적하거나 괴롭히는 데 사용하지 마세요.

## 프로젝트 구조

```
insta_finder/
  crawler.py         인스타그램 로그인/세션, @계정·#해시태그 게시물 가져오기 (instaloader)
  crawl_finder.py    크롤링한 게시물 이미지와 원본 사진 비교
  login.py           세션 파일 만들기 (python -m insta_finder.login)
  finder.py          Google Lens 검색 흐름 (업로드 → 검색 → 인스타그램 링크 추출 → 유사도 정렬)
  serpapi_client.py  SerpApi 클라이언트
  instagram.py       인스타그램 URL·제목·스니펫에서 게시물/사용자 이름 추출
  image_utils.py     업로드용 압축, EXIF 제거, 비율 맞춤 dHash 유사도
  formatting.py      결과 메시지 (한국어)
  config.py          .env 설정
  cli.py             명령줄 도구
  telegram_bot.py    텔레그램 봇
tests/               pytest 테스트 (네트워크·실제 로그인 없이 가짜 응답으로 검증)
```

## 테스트

```bash
pip install pytest
python -m pytest
```
