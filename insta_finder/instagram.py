"""인스타그램 URL 과 검색 결과 텍스트에서 게시물/사용자 정보를 추출한다.

인스타그램에 직접 접속하지 않고, 검색 엔진이 돌려준 링크와 제목만으로 판단한다.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from .models import InstagramRef

INSTAGRAM_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
    "instagr.am",
    "www.instagr.am",
}

# URL 첫 경로에 오지만 사용자 이름이 아닌 것들
RESERVED_PATHS = {
    "p", "reel", "reels", "tv", "stories", "explore", "accounts", "about",
    "developer", "developers", "legal", "direct", "web", "api", "graphql",
    "static", "emails", "challenge", "privacy", "terms", "session", "oauth",
    "ar", "directory", "lite", "igtv", "locations", "tags", "topics", "press",
    "business", "help", "download", "embed", "share", "s", "invites", "nametag",
}

# 게시물 경로 → 종류
POST_PATHS = {"p": "post", "reel": "reel", "reels": "reel", "tv": "tv"}
_CANONICAL_POST_PATH = {"post": "p", "reel": "reel", "tv": "tv"}

_USERNAME = r"[A-Za-z0-9._]{1,30}"
_USERNAME_RE = re.compile(rf"^{_USERNAME}$")
_SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]{5,64}$")

# 제목에서 사용자 이름을 찾는 패턴 (확실한 것부터)
_TITLE_USERNAME_PATTERNS = [
    # "홍길동 (@gildong) • Instagram photos and videos"
    re.compile(rf"\(@({_USERNAME})\)"),
    # "@gildong • Instagram" / "@gildong on Instagram"
    re.compile(rf"(?:^|\s)@({_USERNAME})\b"),
]

# "홍길동 on Instagram: ..." 처럼 표시 이름(또는 사용자 이름)이 오는 패턴
_TITLE_AUTHOR_PATTERNS = [
    re.compile(rf"^\s*(.+?)\s*\(@{_USERNAME}\)"),
    re.compile(r"^\s*(.+?)\s+on Instagram\b", re.IGNORECASE),
    re.compile(r"^\s*(.+?)님의 Instagram"),
    re.compile(r"^\s*Instagram의\s+(.+?)님"),
    re.compile(r"^\s*(.+?)\s*\|\s*Instagram\b", re.IGNORECASE),
]

# Google 검색 스니펫: "1,234 likes, 56 comments - gildong on March 3, 2024: ..."
_SNIPPET_USERNAME_PATTERNS = [
    re.compile(rf"comments?\s*-\s*({_USERNAME})\s+on\s+[A-Z][a-z]+\s+\d{{1,2}},\s*\d{{4}}"),
    re.compile(rf"\b({_USERNAME})\s+on\s+[A-Z][a-z]+\s+\d{{1,2}},\s*\d{{4}}\s*:"),
    re.compile(rf"댓글\s*[\d,.]+[^-]*-\s*({_USERNAME})님"),
]


def is_valid_username(value: str | None) -> bool:
    if not value or not _USERNAME_RE.match(value):
        return False
    if value.lower() in RESERVED_PATHS:
        return False
    # 인스타그램은 마침표로 시작/끝나거나 연속 마침표를 허용하지 않는다
    if value.startswith(".") or value.endswith(".") or ".." in value:
        return False
    return True


def is_instagram_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return False
    return host in INSTAGRAM_HOSTS


def post_url(kind: str, shortcode: str) -> str:
    return f"https://www.instagram.com/{_CANONICAL_POST_PATH.get(kind, 'p')}/{shortcode}/"


def profile_url(username: str) -> str:
    return f"https://www.instagram.com/{username}/"


def parse_instagram_url(url: str | None) -> InstagramRef | None:
    """인스타그램 URL 을 게시물/릴스/스토리/프로필로 분류한다.

    지원 형식:
      /p/<code>/, /reel/<code>/, /reels/<code>/, /tv/<code>/
      /<username>/p/<code>/, /<username>/reel/<code>/
      /stories/<username>/<id>/
      /<username>/
    """
    if not is_instagram_url(url):
        return None
    parts = [unquote(p) for p in urlparse(url.strip()).path.split("/") if p]
    if not parts:
        return None

    head = parts[0].lower()

    if head in POST_PATHS:
        if len(parts) >= 2 and _SHORTCODE_RE.match(parts[1]):
            kind = POST_PATHS[head]
            return InstagramRef(kind=kind, url=post_url(kind, parts[1]), shortcode=parts[1])
        return None

    if head == "stories":
        if len(parts) >= 2 and is_valid_username(parts[1]):
            username = parts[1]
            story_id = parts[2] if len(parts) >= 3 and parts[2].isdigit() else None
            story_url = (
                f"https://www.instagram.com/stories/{username}/{story_id}/"
                if story_id
                else f"https://www.instagram.com/stories/{username}/"
            )
            return InstagramRef(kind="story", url=story_url, shortcode=story_id, username=username)
        return None

    if is_valid_username(parts[0]):
        username = parts[0]
        if len(parts) >= 3 and parts[1].lower() in POST_PATHS and _SHORTCODE_RE.match(parts[2]):
            kind = POST_PATHS[parts[1].lower()]
            return InstagramRef(
                kind=kind, url=post_url(kind, parts[2]), shortcode=parts[2], username=username
            )
        return InstagramRef(kind="profile", url=profile_url(username), username=username)

    return None


def username_from_title(title: str | None) -> str | None:
    """검색 결과 제목에서 확실한 사용자 이름(@handle)을 찾는다."""
    if not title:
        return None
    for pattern in _TITLE_USERNAME_PATTERNS:
        m = pattern.search(title)
        if m and is_valid_username(m.group(1)):
            return m.group(1)
    return None


def author_from_title(title: str | None) -> str | None:
    """"홍길동 on Instagram: ..." 형태의 제목에서 게시자 표시 이름을 찾는다.

    인스타그램은 이름이 설정된 계정은 표시 이름을, 아니면 사용자 이름을 쓰므로
    반환값이 사용자 이름이라는 보장은 없다.
    """
    if not title:
        return None
    for pattern in _TITLE_AUTHOR_PATTERNS:
        m = pattern.search(title)
        if m:
            name = m.group(1).strip().strip('"').strip()
            if name and name.lower() != "instagram" and len(name) <= 60:
                return name
    return None


def username_from_snippet(text: str | None) -> str | None:
    """Google 검색 스니펫("... - gildong on March 3, 2024: ...")에서 사용자 이름을 찾는다."""
    if not text:
        return None
    for pattern in _SNIPPET_USERNAME_PATTERNS:
        m = pattern.search(text)
        if m and is_valid_username(m.group(1)):
            return m.group(1)
    return None
