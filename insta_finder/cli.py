"""명령줄에서 사진 파일로 검색하기.

    python -m insta_finder 사진.jpg
    python -m insta_finder 사진.jpg --json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from pathlib import Path

from .config import Settings
from .finder import VALID_SEARCH_TYPES, PhotoFinder
from .formatting import format_report
from .image_utils import InvalidImageError
from .serpapi_client import SerpApiClient, SerpApiError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="insta-finder",
        description="사진으로 인스타그램 게시물과 올린 사용자를 찾습니다 (Google Lens 역이미지 검색).",
    )
    parser.add_argument("image", type=Path, help="검색할 사진 파일 경로")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    parser.add_argument("--max-results", type=int, help="최대 결과 수")
    parser.add_argument(
        "--types",
        help=f"검색 종류, 쉼표로 구분 ({', '.join(VALID_SEARCH_TYPES)})",
    )
    parser.add_argument("--no-verify", action="store_true", help="썸네일 유사도 비교 끄기")
    parser.add_argument(
        "--resolve-usernames",
        type=int,
        metavar="N",
        help="사용자 이름을 모르는 상위 N개 게시물을 Google 검색으로 추가 조회 (N 크레딧 추가 사용)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="디버그 로그 출력")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    # httpx 요청 로그에는 API 키가 담긴 URL 이 찍히므로 숨긴다
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    try:
        settings = Settings.from_env()
        changes: dict = {}
        if args.max_results is not None:
            changes["max_results"] = max(1, args.max_results)
        if args.types:
            changes["search_types"] = tuple(t.strip() for t in args.types.split(",") if t.strip())
        if args.no_verify:
            changes["verify_thumbnails"] = False
        if args.resolve_usernames is not None:
            changes["resolve_usernames"] = max(0, args.resolve_usernames)
        options = dataclasses.replace(settings.finder, **changes)
    except ValueError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2

    if not settings.serpapi_api_key:
        print("SERPAPI_API_KEY 환경 변수(.env)를 설정하세요.", file=sys.stderr)
        return 2
    try:
        image_bytes = args.image.read_bytes()
    except OSError as exc:
        print(f"파일을 읽을 수 없습니다: {exc}", file=sys.stderr)
        return 2

    client = SerpApiClient(
        settings.serpapi_api_key, language=settings.lens_language, country=settings.lens_country
    )
    finder = PhotoFinder(client, options)
    try:
        report = finder.find(image_bytes)
    except InvalidImageError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except SerpApiError as exc:
        print(f"검색 실패: {exc}", file=sys.stderr)
        return 1
    finally:
        finder.close()
        client.close()

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
