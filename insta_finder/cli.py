"""명령줄에서 사진 파일로 검색하기.

    # 크롤링: 로그인한 인스타그램 세션으로 지정한 계정/해시태그의 게시물과 비교
    python -m insta_finder 사진.jpg -t @gildong -t "#제주도"

    # AI + 크롤링: Claude 가 사진을 보고 뒤질 해시태그/계정을 골라서 비교 (ANTHROPIC_API_KEY)
    python -m insta_finder 사진.jpg --hint "제주도 여행"

    # Google Lens(SerpApi): 인스타그램 전체에서 찾기 (AI 가 꺼져 있거나 --lens 일 때)
    python -m insta_finder 사진.jpg --lens
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from pathlib import Path

from .ai_targets import SuggestionError, TargetSuggester
from .config import Settings
from .crawl_finder import CrawlFinder
from .crawler import CrawlerError, InstagramSession, LoginError, parse_targets
from .finder import VALID_SEARCH_TYPES, PhotoFinder
from .formatting import format_report
from .image_utils import InvalidImageError
from .serpapi_client import SerpApiClient, SerpApiError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="insta-finder",
        description="사진으로 인스타그램 게시물과 올린 사용자를 찾습니다.",
    )
    parser.add_argument("image", type=Path, help="검색할 사진 파일 경로")
    parser.add_argument(
        "-t",
        "--target",
        action="append",
        default=[],
        metavar="@계정|#태그",
        help="이 계정/해시태그의 게시물을 직접 비교 (여러 번 지정 가능, 인스타그램 로그인 필요)",
    )
    parser.add_argument("--hint", help="AI: 사진에 대한 힌트 (장소 이름 등)")
    parser.add_argument("--lens", action="store_true", help="AI 대신 Google Lens 로 검색")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    parser.add_argument("--max-posts", type=int, help="크롤링: 대상 하나당 비교할 최대 게시물 수")
    parser.add_argument("--threshold", type=float, help="크롤링: 같은 사진으로 볼 유사도 (0~1)")
    parser.add_argument("--max-results", type=int, help="Lens: 최대 결과 수")
    parser.add_argument(
        "--types",
        help=f"Lens: 검색 종류, 쉼표로 구분 ({', '.join(VALID_SEARCH_TYPES)})",
    )
    parser.add_argument("--no-verify", action="store_true", help="Lens: 썸네일 유사도 비교 끄기")
    parser.add_argument(
        "--resolve-usernames",
        type=int,
        metavar="N",
        help="Lens: 사용자 이름을 모르는 상위 N개 게시물의 게시자 추가 조회",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="디버그 로그 출력")
    return parser


def _progress(count: int, target: str) -> None:
    print(f"\r  {target} 확인 중... 게시물 {count}개 비교", end="", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    # httpx 요청 로그에는 API 키가 담긴 URL 이 찍히므로 숨긴다
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    targets = parse_targets(" ".join(args.target))
    if args.target and not targets:
        print("대상은 @계정 또는 #해시태그 형식으로 지정하세요.", file=sys.stderr)
        return 2

    try:
        settings = Settings.from_env()
        finder_changes: dict = {}
        if args.max_results is not None:
            finder_changes["max_results"] = max(1, args.max_results)
        if args.types:
            finder_changes["search_types"] = tuple(
                t.strip() for t in args.types.split(",") if t.strip()
            )
        if args.no_verify:
            finder_changes["verify_thumbnails"] = False
        if args.resolve_usernames is not None:
            finder_changes["resolve_usernames"] = max(0, args.resolve_usernames)
        finder_options = dataclasses.replace(settings.finder, **finder_changes)
        crawl_changes: dict = {}
        if args.max_posts is not None:
            crawl_changes["max_posts"] = args.max_posts
        if args.threshold is not None:
            crawl_changes["threshold"] = args.threshold
        crawl_options = dataclasses.replace(settings.crawl, **crawl_changes)
    except ValueError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2

    if targets and not settings.crawl_enabled:
        print(
            "크롤링하려면 .env 에 INSTAGRAM_USERNAME 을 설정하고 "
            "`python -m insta_finder.login` 으로 로그인하세요.",
            file=sys.stderr,
        )
        return 2
    use_ai = not targets and settings.ai_enabled and not args.lens
    if args.lens and not settings.lens_enabled:
        print("--lens 를 쓰려면 SERPAPI_API_KEY 를 설정하세요.", file=sys.stderr)
        return 2
    if not targets and settings.anthropic_api_key and not settings.crawl_enabled and not settings.lens_enabled:
        print(
            "AI 가 고른 대상을 크롤링하려면 인스타그램 로그인이 필요해요. "
            ".env 에 INSTAGRAM_USERNAME 을 설정하고 `python -m insta_finder.login` 을 실행하세요.",
            file=sys.stderr,
        )
        return 2
    if not targets and not use_ai and not settings.lens_enabled:
        print(
            "어디서 찾을지 -t @계정 또는 -t \"#해시태그\" 로 지정하세요.\n"
            "(인스타그램에는 사진 검색 기능이 없어서 뒤질 대상이 필요합니다. "
            "ANTHROPIC_API_KEY 를 설정하면 AI 가 대상을 골라주고, "
            "SERPAPI_API_KEY 를 설정하면 Google Lens 로 전체 검색할 수 있어요.)",
            file=sys.stderr,
        )
        return 2

    try:
        image_bytes = args.image.read_bytes()
    except OSError as exc:
        print(f"파일을 읽을 수 없습니다: {exc}", file=sys.stderr)
        return 2

    session = None
    if settings.crawl_enabled:
        session = InstagramSession(
            settings.instagram_username,
            settings.instagram_password,
            settings.instagram_session_file,
        )

    closers = []
    try:
        note = None
        max_posts = None
        if use_ai:
            print("AI 가 사진을 분석하는 중...", file=sys.stderr)
            suggestion = TargetSuggester(
                model=settings.ai_model, max_targets=crawl_options.max_targets
            ).suggest(image_bytes, hint=args.hint)
            print(f"  {suggestion.description}", file=sys.stderr)
            if not suggestion.targets:
                print(
                    "AI 가 사진에서 단서를 찾지 못했어요. --hint 로 장소 이름 등을 알려주거나 "
                    "-t 로 대상을 직접 지정하세요.",
                    file=sys.stderr,
                )
                return 1
            targets = suggestion.targets
            note = suggestion.summary()
            max_posts = args.max_posts or settings.ai_max_posts
            print(f"  대상: {' '.join(str(t) for t in targets)}", file=sys.stderr)
        if targets:
            crawl_finder = CrawlFinder(session, crawl_options)
            closers.append(crawl_finder.close)
            report = crawl_finder.find(image_bytes, targets, progress=_progress, max_posts=max_posts)
            report.note = note
            print(file=sys.stderr)
        else:
            client = SerpApiClient(
                settings.serpapi_api_key,
                language=settings.lens_language,
                country=settings.lens_country,
            )
            closers.append(client.close)
            finder = PhotoFinder(
                client, finder_options, owner_lookup=session.post_owner if session else None
            )
            closers.append(finder.close)
            report = finder.find(image_bytes)
    except InvalidImageError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except SuggestionError as exc:
        print(f"AI 분석 실패: {exc}", file=sys.stderr)
        return 1
    except (LoginError, CrawlerError) as exc:
        print(f"\n인스타그램 오류: {exc}", file=sys.stderr)
        return 1
    except SerpApiError as exc:
        print(f"검색 실패: {exc}", file=sys.stderr)
        return 1
    finally:
        for close in reversed(closers):
            close()

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
