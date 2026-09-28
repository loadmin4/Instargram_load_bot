import json

import httpx
import pytest

from insta_finder.finder import FinderOptions, PhotoFinder
from insta_finder.formatting import format_report
from insta_finder.serpapi_client import SerpApiClient, SerpApiError
from tests.conftest import make_image, to_bytes

API_KEY = "test-secret-key"

EXACT = {
    "exact_matches": [
        {
            "position": 1,
            "title": "Some Blog - Travel photos",
            "link": "https://blog.example.com/travel",
            "source": "Example Blog",
            "thumbnail": "https://thumbs.example/blog.jpg",
        },
        {
            "position": 2,
            "title": 'Gil Dong on Instagram: "Sunset in Jeju"',
            "link": "https://www.instagram.com/p/C1a2B3c4D5e/?img_index=1",
            "source": "Instagram",
            "thumbnail": "https://thumbs.example/same.jpg",
            "date": "2 years ago",
        },
        {
            "position": 3,
            "title": "Repost (@repost_account) • Instagram photos and videos",
            "link": "https://www.instagram.com/repost_account/",
            "source": "Instagram",
            "thumbnail": "https://thumbs.example/other.jpg",
        },
    ]
}

VISUAL = {
    "visual_matches": [
        {
            "position": 1,
            "title": "Gil Dong on Instagram",
            # 같은 게시물 - 중복 제거되어야 함
            "link": "https://www.instagram.com/gildong/p/C1a2B3c4D5e/",
            "source": "Instagram",
            "thumbnail": "https://thumbs.example/same.jpg",
        },
        {
            "position": 2,
            "title": "Similar sunset",
            "link": "https://www.instagram.com/reel/Cz9Y8x7W6v5/",
            "source": "Instagram",
            "image": {"link": "https://thumbs.example/other.jpg", "width": 100, "height": 100},
        },
        {
            "position": 3,
            "title": "Pinterest",
            "link": "https://www.pinterest.com/pin/1/",
            "source": "Pinterest",
        },
    ]
}

GOOGLE = {
    "organic_results": [
        {
            "link": "https://www.instagram.com/p/C1a2B3c4D5e/",
            "title": 'Gil Dong on Instagram: "Sunset in Jeju"',
            "snippet": '1,234 likes, 56 comments - gildong on March 3, 2024: "Sunset in Jeju".',
        }
    ]
}


class FakeServer:
    """SerpApi 와 썸네일 서버를 흉내내는 httpx 전송 계층."""

    def __init__(self, photo_bytes: bytes, lens_responses=None):
        self.photo_bytes = photo_bytes
        self.other_bytes = to_bytes(make_image(seed=99))
        self.lens_responses = lens_responses or {"exact_matches": EXACT, "visual_matches": VISUAL}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        if url.host == "serpapi.com" and url.path == "/image":
            assert b"test-secret-key" in request.content
            return httpx.Response(200, json={"image_id": "img-123"})
        if url.host == "serpapi.com" and url.path == "/search.json":
            params = url.params
            assert params["api_key"] == API_KEY
            if params["engine"] == "google_lens":
                assert params["image_id"] == "img-123"
                return httpx.Response(200, json=self.lens_responses.get(params["type"], {}))
            if params["engine"] == "google":
                return httpx.Response(200, json=GOOGLE)
        if url.host == "thumbs.example":
            if url.path == "/same.jpg":
                return httpx.Response(200, content=self.photo_bytes)
            if url.path == "/other.jpg":
                return httpx.Response(200, content=self.other_bytes)
            return httpx.Response(404)
        return httpx.Response(500)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def count(self, engine: str) -> int:
        return sum(1 for r in self.requests if r.url.params.get("engine") == engine)


def make_finder(server: FakeServer, **options) -> PhotoFinder:
    http = server.client()
    client = SerpApiClient(API_KEY, http=http)
    return PhotoFinder(client, FinderOptions(**options), http=http)


def test_find_returns_instagram_matches_ranked(photo_bytes):
    server = FakeServer(photo_bytes)
    report = make_finder(server).find(photo_bytes)

    assert report.total_results == 6
    urls = [m.ref.url for m in report.matches]
    # 인스타그램 링크만, 중복 제거, 완전 일치 → 유사도 순
    assert urls == [
        "https://www.instagram.com/p/C1a2B3c4D5e/",
        "https://www.instagram.com/repost_account/",
        "https://www.instagram.com/reel/Cz9Y8x7W6v5/",
    ]
    top = report.matches[0]
    assert top.match_type == "exact_matches"
    # 완전 일치 결과에는 없던 사용자 이름을 유사 이미지 결과의 URL 에서 합쳐온다
    assert top.username == "gildong"
    assert top.display_name == "Gil Dong"
    assert top.date == "2 years ago"
    assert top.similarity is not None and top.similarity >= 0.95

    profile = report.matches[1]
    assert profile.ref.kind == "profile"
    assert profile.username == "repost_account"
    assert profile.similarity is not None and profile.similarity < top.similarity

    assert report.matches[2].thumbnail == "https://thumbs.example/other.jpg"
    assert server.count("google") == 0


def test_find_without_verification_skips_thumbnails(photo_bytes):
    server = FakeServer(photo_bytes)
    report = make_finder(server, verify_thumbnails=False).find(photo_bytes)
    assert all(m.similarity is None for m in report.matches)
    assert not any(r.url.host == "thumbs.example" for r in server.requests)


def test_find_respects_search_types_and_max_results(photo_bytes):
    server = FakeServer(photo_bytes)
    report = make_finder(server, search_types=("exact_matches",), max_results=1).find(photo_bytes)
    assert len(report.matches) == 1
    assert server.count("google_lens") == 1


def test_resolve_usernames_uses_google_snippet(photo_bytes):
    lens = {"exact_matches": {"exact_matches": [EXACT["exact_matches"][1]]}}
    server = FakeServer(photo_bytes, lens_responses=lens)
    report = make_finder(server, search_types=("exact_matches",), resolve_usernames=3).find(
        photo_bytes
    )
    assert report.matches[0].username == "gildong"
    assert server.count("google") == 1


def test_find_no_results_is_not_an_error(photo_bytes):
    no_results = {"error": "Google Lens hasn't returned any results for this query."}
    server = FakeServer(
        photo_bytes, lens_responses={"exact_matches": no_results, "visual_matches": no_results}
    )
    report = make_finder(server).find(photo_bytes)
    assert report.matches == []
    assert "찾지 못했어요" in format_report(report)


def test_serpapi_error_is_raised_without_leaking_key(photo_bytes):
    def handler(request):
        if request.url.path == "/image":
            return httpx.Response(200, json={"image_id": "img-123"})
        return httpx.Response(401, json={"error": "Invalid API key."})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    finder = PhotoFinder(SerpApiClient(API_KEY, http=http), http=http)
    with pytest.raises(SerpApiError) as info:
        finder.find(photo_bytes)
    assert "Invalid API key" in str(info.value)


def test_serpapi_non_json_error_does_not_leak_key():
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(502, text="bad")))
    client = SerpApiClient(API_KEY, http=http)
    with pytest.raises(SerpApiError) as info:
        client.lens(image_id="x")
    assert API_KEY not in str(info.value)
    assert info.value.__cause__ is None


def test_format_report_lists_uploader(photo_bytes):
    server = FakeServer(photo_bytes)
    text = format_report(make_finder(server).find(photo_bytes))
    assert "@gildong (Gil Dong)" in text
    assert "https://www.instagram.com/p/C1a2B3c4D5e/" in text
    assert "https://www.instagram.com/gildong/" in text
    assert "완전 일치" in text


def test_report_to_dict_is_json_serializable(photo_bytes):
    server = FakeServer(photo_bytes)
    report = make_finder(server).find(photo_bytes)
    data = json.loads(json.dumps(report.to_dict()))
    assert data["matches"][0]["ref"]["shortcode"] == "C1a2B3c4D5e"


def test_invalid_search_type():
    with pytest.raises(ValueError):
        FinderOptions(search_types=("products",))
