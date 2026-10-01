"""환경 변수(.env) 설정 읽기."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .crawl_finder import CrawlOptions
from .finder import FinderOptions

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

DEFAULT_SESSION_FILE = "instagram.session"


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _int(value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"숫자가 아닌 설정값입니다: {value!r}") from exc


def _float(value: str | None, default: float) -> float:
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"숫자가 아닌 설정값입니다: {value!r}") from exc


def _list(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


@dataclass
class Settings:
    # 비밀 값은 repr 에서 빼서 로그/오류 메시지에 찍히지 않게 한다
    serpapi_api_key: str = field(repr=False)
    telegram_bot_token: str = field(repr=False)
    allowed_user_ids: frozenset[int]
    lens_language: str | None
    lens_country: str | None
    finder: FinderOptions
    instagram_username: str
    instagram_password: str = field(repr=False)
    instagram_session_file: str
    crawl: CrawlOptions

    @property
    def lens_enabled(self) -> bool:
        return bool(self.serpapi_api_key)

    @property
    def crawl_enabled(self) -> bool:
        return bool(self.instagram_username)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)
        search_types = tuple(_list(env.get("SEARCH_TYPES"))) or ("exact_matches", "visual_matches")
        try:
            allowed = frozenset(int(x) for x in _list(env.get("ALLOWED_USER_IDS")))
        except ValueError as exc:
            raise ValueError("ALLOWED_USER_IDS 는 쉼표로 구분한 숫자여야 합니다.") from exc
        instagram_username = env.get("INSTAGRAM_USERNAME", "").strip().lstrip("@")
        # 인스타그램 세션이 있으면 게시자 조회를 무료로 할 수 있으므로 기본으로 켠다
        resolve_default = 3 if instagram_username else 0
        return cls(
            serpapi_api_key=env.get("SERPAPI_API_KEY", "").strip(),
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", "").strip(),
            allowed_user_ids=allowed,
            lens_language=env.get("LENS_LANGUAGE", "en").strip() or None,
            lens_country=env.get("LENS_COUNTRY", "").strip() or None,
            finder=FinderOptions(
                search_types=search_types,
                max_results=max(1, _int(env.get("MAX_RESULTS"), 10)),
                verify_thumbnails=_bool(env.get("VERIFY_THUMBNAILS"), True),
                resolve_usernames=max(0, _int(env.get("RESOLVE_USERNAMES"), resolve_default)),
                query_hint=env.get("LENS_QUERY", "").strip() or None,
            ),
            instagram_username=instagram_username,
            instagram_password=env.get("INSTAGRAM_PASSWORD", ""),
            instagram_session_file=env.get("INSTAGRAM_SESSION_FILE", "").strip()
            or DEFAULT_SESSION_FILE,
            crawl=CrawlOptions(
                max_posts=_int(env.get("CRAWL_MAX_POSTS"), 200),
                threshold=_float(env.get("CRAWL_MATCH_THRESHOLD"), 0.82),
                max_matches=max(0, _int(env.get("CRAWL_MAX_MATCHES"), 3)),
                max_targets=max(1, _int(env.get("CRAWL_MAX_TARGETS"), 5)),
            ),
        )
