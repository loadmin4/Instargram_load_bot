"""로그인한 인스타그램 세션으로 지정한 계정/해시태그의 게시물을 훑으며 같은 사진을 찾는다."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

import httpx

from . import image_utils
from .crawler import CrawledPost, CrawlerError, LoginError, Target
from .finder import download_image
from .models import InstagramRef, Match, SearchReport

log = logging.getLogger(__name__)

# (지금까지 비교한 게시물 수, 지금 보고 있는 대상)
ProgressCallback = Callable[[int, str], None]

_BATCH_SIZE = 12
_MAX_IMAGES_PER_POST = 10


class PostSource(Protocol):
    def iter_posts(self, target: Target, limit: int) -> Iterable[CrawledPost]: ...

    def post_owner(self, shortcode: str) -> str | None: ...


@dataclass
class CrawlOptions:
    max_posts: int = 200  # 대상 하나당 최대로 비교할 게시물 수
    threshold: float = 0.82  # 이 이상이면 같은 사진으로 판단 (0.0~1.0)
    max_matches: int = 3  # 이만큼 찾으면 중단 (0 = 끝까지)
    max_targets: int = 5  # 한 번에 뒤질 수 있는 대상 수

    def __post_init__(self) -> None:
        if not 0.0 < self.threshold <= 1.0:
            raise ValueError("CRAWL_MATCH_THRESHOLD 는 0 초과 1 이하여야 합니다.")
        if self.max_posts < 1:
            raise ValueError("CRAWL_MAX_POSTS 는 1 이상이어야 합니다.")


class CrawlFinder:
    def __init__(
        self,
        session: PostSource,
        options: CrawlOptions | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self.session = session
        self.options = options or CrawlOptions()
        self._http = http or httpx.Client(timeout=20.0, follow_redirects=True)

    def close(self) -> None:
        self._http.close()

    def find(
        self,
        image_bytes: bytes,
        targets: list[Target],
        progress: ProgressCallback | None = None,
    ) -> SearchReport:
        if not targets:
            raise ValueError("검색할 계정(@아이디)이나 해시태그(#태그)를 하나 이상 지정하세요.")
        targets = targets[: self.options.max_targets]
        query = image_utils.QueryImage(image_utils.load_image(image_bytes))
        report = SearchReport(
            mode="crawl", search_types=("crawl",), targets=tuple(str(t) for t in targets)
        )
        found: dict[str, Match] = {}

        lock = getattr(self.session, "lock", None)
        if lock is not None:
            lock.acquire()
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for target in targets:
                    try:
                        self._scan_target(target, query, pool, report, found, progress)
                    except LoginError:
                        raise
                    except CrawlerError as exc:
                        report.errors.append(str(exc))
                    if self._enough(found):
                        break
            matches = sorted(found.values(), key=lambda m: -(m.similarity or 0.0))
            if self.options.max_matches > 0:
                matches = matches[: self.options.max_matches]
            self._fill_usernames(matches)
        finally:
            if lock is not None:
                lock.release()

        report.matches = matches
        return report

    # ------------------------------------------------------------------ 내부
    def _enough(self, found: dict[str, Match]) -> bool:
        return self.options.max_matches > 0 and len(found) >= self.options.max_matches

    def _scan_target(
        self,
        target: Target,
        query: image_utils.QueryImage,
        pool: ThreadPoolExecutor,
        report: SearchReport,
        found: dict[str, Match],
        progress: ProgressCallback | None,
    ) -> None:
        batch: list[CrawledPost] = []
        for post in self.session.iter_posts(target, self.options.max_posts):
            batch.append(post)
            if len(batch) >= _BATCH_SIZE:
                self._compare_batch(batch, query, pool, report, found)
                batch = []
                if progress:
                    progress(report.total_results, str(target))
                if self._enough(found):
                    return
        if batch:
            self._compare_batch(batch, query, pool, report, found)
            if progress:
                progress(report.total_results, str(target))

    def _compare_batch(
        self,
        batch: list[CrawledPost],
        query: image_utils.QueryImage,
        pool: ThreadPoolExecutor,
        report: SearchReport,
        found: dict[str, Match],
    ) -> None:
        jobs = [
            (post, url) for post in batch for url in post.image_urls[:_MAX_IMAGES_PER_POST]
        ]
        scores = pool.map(lambda job: self._similarity(query, job[1]), jobs)
        best: dict[str, float] = {}
        for (post, _), score in zip(jobs, scores):
            if score is not None:
                best[post.shortcode] = max(score, best.get(post.shortcode, 0.0))

        for post in batch:
            report.total_results += 1
            score = best.get(post.shortcode)
            if score is None or score < self.options.threshold:
                continue
            kept = found.get(post.shortcode)
            if kept is not None and (kept.similarity or 0.0) >= score:
                continue
            found[post.shortcode] = Match(
                ref=InstagramRef(
                    kind=post.kind, url=post.url, shortcode=post.shortcode, username=post.username
                ),
                match_type="crawl",
                position=report.total_results,
                title=_short(post.caption),
                link=post.url,
                thumbnail=post.image_urls[0],
                date=post.taken_at.strftime("%Y-%m-%d") if post.taken_at else None,
                username=post.username,
                similarity=round(score, 3),
            )

    def _similarity(self, query: image_utils.QueryImage, url: str) -> float | None:
        try:
            return query.similarity(download_image(self._http, url))
        except Exception as exc:  # 이미지 하나 실패해도 계속
            log.debug("이미지 비교 실패 %s: %s", url, exc)
            return None

    def _fill_usernames(self, matches: Iterable[Match]) -> None:
        """해시태그 검색 결과처럼 게시자를 모르는 게시물만 추가로 조회한다."""
        for match in matches:
            if match.username or not match.ref.shortcode:
                continue
            try:
                match.username = self.session.post_owner(match.ref.shortcode)
            except CrawlerError as exc:
                log.info("게시자 조회 실패: %s", exc)


def _short(text: str | None, limit: int = 80) -> str:
    if not text:
        return ""
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 3] + "..."
