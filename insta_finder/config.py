"""환경 변수(.env) 설정 읽기."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .finder import FinderOptions

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


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


def _list(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


@dataclass
class Settings:
    serpapi_api_key: str
    telegram_bot_token: str
    allowed_user_ids: frozenset[int]
    lens_language: str | None
    lens_country: str | None
    finder: FinderOptions

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)
        search_types = tuple(_list(env.get("SEARCH_TYPES"))) or ("exact_matches", "visual_matches")
        try:
            allowed = frozenset(int(x) for x in _list(env.get("ALLOWED_USER_IDS")))
        except ValueError as exc:
            raise ValueError("ALLOWED_USER_IDS 는 쉼표로 구분한 숫자여야 합니다.") from exc
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
                resolve_usernames=max(0, _int(env.get("RESOLVE_USERNAMES"), 0)),
                query_hint=env.get("LENS_QUERY", "").strip() or None,
            ),
        )
