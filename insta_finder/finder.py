"""사진 한 장으로 인스타그램 게시물과 게시자를 찾는 핵심 로직."""

from __future__ import annotations

import io
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import httpx
from PIL import Image

from . import image_utils, instagram
from .models import Match, SearchReport
from .serpapi_client import SerpApiClient

log = logging.getLogger(__name__)

VALID_SEARCH_TYPES = ("exact_matches", "visual_matches")
_IMAGE_MAX_BYTES = 10 * 1024 * 1024

# 게시물 shortcode → 게시자 사용자 이름 (예: 로그인한 인스타그램 세션으로 조회)
OwnerLookup = Callable[[str], "str | None"]


@dataclass
class FinderOptions:
    search_types: tuple[str, ...] = ("exact_matches", "visual_matches")
    max_results: int = 10
    verify_thumbnails: bool = True
    resolve_usernames: int = 0  # 사용자 이름이 없는 상위 N개 게시물의 게시자를 추가 조회
    query_hint: str | None = None  # Lens 에 함께 넘길 검색어 (예: "instagram")

    def __post_init__(self) -> None:
        unknown = [t for t in self.search_types if t not in VALID_SEARCH_TYPES]
        if unknown:
            raise ValueError(f"지원하지 않는 검색 종류: {', '.join(unknown)}")
        if not self.search_types:
            raise ValueError("검색 종류를 하나 이상 지정해야 합니다.")


class PhotoFinder:
    """Google Lens(SerpApi) 역이미지 검색으로 인스타그램 게시물을 찾는다."""

    def __init__(
        self,
        client: SerpApiClient,
        options: FinderOptions | None = None,
        http: httpx.Client | None = None,
        owner_lookup: OwnerLookup | None = None,
    ) -> None:
        self.client = client
        self.options = options or FinderOptions()
        self._http = http or httpx.Client(timeout=15.0, follow_redirects=True)
        # 지정하면 게시자 조회에 SerpApi Google 검색 대신 이것을 쓴다 (크레딧 절약)
        self.owner_lookup = owner_lookup

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ 메인 흐름
    def find(self, image_bytes: bytes) -> SearchReport:
        """사진을 역이미지 검색해 인스타그램 게시물/계정 후보를 돌려준다."""
        query_image = image_utils.load_image(image_bytes)
        upload = image_utils.prepare_for_upload(image_bytes)
        image_id = self.client.upload_image(upload)

        report = SearchReport(search_types=self.options.search_types)
        candidates: list[Match] = []
        for search_type in self.options.search_types:
            data = self.client.lens(
                image_id=image_id, search_type=search_type, query=self.options.query_hint
            )
            items = data.get(search_type) or []
            report.total_results += len(items)
            candidates.extend(self._extract_matches(items, search_type))

        matches = _dedupe(candidates)

        if self.options.verify_thumbnails and matches:
            self._score_thumbnails(query_image, matches)
        if self.options.resolve_usernames > 0:
            self._resolve_usernames(matches)

        matches.sort(key=Match.sort_key)
        report.matches = matches[: self.options.max_results]
        return report

    # ------------------------------------------------------------------ 단계별 처리
    @staticmethod
    def _extract_matches(items: Iterable[dict[str, Any]], search_type: str) -> list[Match]:
        matches = []
        for index, item in enumerate(items, start=1):
            link = item.get("link") or ""
            ref = instagram.parse_instagram_url(link)
            if ref is None:
                continue
            title = item.get("title") or ""
            thumbnail = item.get("thumbnail") or item.get("image")
            if isinstance(thumbnail, dict):
                thumbnail = thumbnail.get("link")
            matches.append(
                Match(
                    ref=ref,
                    match_type=search_type,
                    position=int(item.get("position") or index),
                    title=title,
                    link=link,
                    thumbnail=thumbnail,
                    date=item.get("date"),
                    username=ref.username or instagram.username_from_title(title),
                    display_name=instagram.author_from_title(title),
                )
            )
        return matches

    def _score_thumbnails(self, query_image: Image.Image, matches: list[Match]) -> None:
        query = image_utils.QueryImage(query_image)

        def score(match: Match) -> None:
            if not match.thumbnail:
                return
            try:
                candidate = download_image(self._http, match.thumbnail)
                match.similarity = round(query.similarity(candidate), 3)
            except Exception as exc:  # 썸네일 하나 실패해도 전체 검색은 계속
                log.debug("썸네일 비교 실패 %s: %s", match.thumbnail, exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(score, matches))

    def _resolve_usernames(self, matches: list[Match]) -> None:
        """사용자 이름을 모르는 게시물의 게시자를 찾는다.

        owner_lookup(인스타그램 세션)이 있으면 그것을, 없으면 Google 검색 스니펫을 쓴다.
        """
        pending = [m for m in sorted(matches, key=Match.sort_key) if m.ref.is_post and not m.username]
        for match in pending[: self.options.resolve_usernames]:
            shortcode = match.ref.shortcode or ""
            try:
                if self.owner_lookup is not None:
                    match.username = self.owner_lookup(shortcode)
                else:
                    data = self.client.google(f'site:instagram.com "{shortcode}"')
                    match.username = _username_from_google(data, shortcode)
            except Exception as exc:  # 조회 실패는 결과에서 사용자 이름만 빠질 뿐
                log.warning("게시자 조회 실패 (%s): %s", match.ref.url, exc)


def download_image(http: httpx.Client, url: str, max_bytes: int = _IMAGE_MAX_BYTES) -> Image.Image:
    if not url.startswith("https://"):
        raise ValueError("https 이미지만 내려받습니다.")
    with http.stream("GET", url) as response:
        response.raise_for_status()
        chunks = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("이미지가 너무 큽니다.")
            chunks.append(chunk)
    return image_utils.load_image(b"".join(chunks))


def _dedupe(matches: list[Match]) -> list[Match]:
    """같은 게시물/계정이 여러 번 나오면 가장 신뢰도 높은 것 하나만 남기고 정보를 합친다."""
    best: dict[str, Match] = {}
    for match in sorted(matches, key=Match.sort_key):
        key = match.ref.url
        kept = best.get(key)
        if kept is None:
            best[key] = match
            continue
        kept.username = kept.username or match.username
        kept.display_name = kept.display_name or match.display_name
        kept.thumbnail = kept.thumbnail or match.thumbnail
        kept.date = kept.date or match.date
    return list(best.values())


def _username_from_google(data: dict[str, Any], shortcode: str) -> str | None:
    for result in data.get("organic_results") or []:
        ref = instagram.parse_instagram_url(result.get("link"))
        if ref is None or ref.shortcode != shortcode:
            continue
        if ref.username:
            return ref.username
        for text in (result.get("snippet"), result.get("title")):
            username = instagram.username_from_snippet(text) or instagram.username_from_title(text)
            if username:
                return username
    return None
