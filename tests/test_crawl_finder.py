import threading
from datetime import datetime, timezone

import httpx
import pytest

from insta_finder.crawl_finder import CrawlFinder, CrawlOptions
from insta_finder.crawler import CrawledPost, CrawlerError, LoginError, Target
from insta_finder.formatting import format_report
from tests.conftest import make_image, to_bytes


class FakeSession:
    """대상별 게시물 목록을 돌려주는 가짜 인스타그램 세션."""

    def __init__(self, posts_by_target, errors=None, owners=None):
        self.posts_by_target = posts_by_target
        self.errors = errors or {}
        self.owners = owners or {}
        self.lock = threading.RLock()
        self.requested_limits = []
        self.owner_lookups = []

    def iter_posts(self, target, limit):
        self.requested_limits.append((str(target), limit))
        if str(target) in self.errors:
            raise self.errors[str(target)]
        yield from self.posts_by_target.get(str(target), [])[:limit]

    def post_owner(self, shortcode):
        self.owner_lookups.append(shortcode)
        return self.owners.get(shortcode)


def post(code, *images, username=None):
    return CrawledPost(
        shortcode=code,
        kind="post",
        image_urls=[f"https://cdn.example/{name}.jpg" for name in images],
        username=username,
        taken_at=datetime(2024, 3, 3, tzinfo=timezone.utc),
        caption=f"caption {code}",
    )


@pytest.fixture
def cdn(photo):
    """같은 사진(4:5 로 잘림), 다른 사진들을 내주는 가짜 이미지 서버."""
    from insta_finder.image_utils import crop_to_aspect

    images = {
        "same": to_bytes(crop_to_aspect(photo, 4 / 5).resize((1080, 1350)), quality=80),
        **{f"other{i}": to_bytes(make_image(seed=100 + i)) for i in range(30)},
    }
    requested = []

    def handler(request):
        requested.append(request.url.path)
        name = request.url.path.strip("/").removesuffix(".jpg")
        if name in images:
            return httpx.Response(200, content=images[name])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.requested = requested
    return client


def test_finds_matching_post_inside_carousel(photo_bytes, cdn):
    session = FakeSession(
        {
            "@gildong": [
                post("Aaaaaaaaaa1", "other0", username="gildong"),
                post("Bbbbbbbbbb2", "other1", "same", "other2", username="gildong"),
                post("Ccccccccccc", "other3", username="gildong"),
            ]
        }
    )
    report = CrawlFinder(session, http=cdn).find(photo_bytes, [Target("profile", "gildong")])

    assert report.mode == "crawl"
    assert report.targets == ("@gildong",)
    assert report.total_results == 3
    assert len(report.matches) == 1
    match = report.matches[0]
    assert match.ref.url == "https://www.instagram.com/p/Bbbbbbbbbb2/"
    assert match.username == "gildong"
    assert match.similarity >= 0.9
    assert match.date == "2024-03-03"
    assert session.owner_lookups == []  # 이미 아는 사용자 이름은 조회하지 않음

    text = format_report(report)
    assert "@gildong 의 게시물 3개를 비교해서 같은 사진 1건" in text
    assert "[직접 비교]" in text
    assert "캡션: caption Bbbbbbbbbb2" in text


def test_hashtag_match_looks_up_owner(photo_bytes, cdn):
    session = FakeSession(
        {"#제주도": [post("Aaaaaaaaaa1", "other0"), post("Bbbbbbbbbb2", "same")]},
        owners={"Bbbbbbbbbb2": "uploader"},
    )
    report = CrawlFinder(session, http=cdn).find(photo_bytes, [Target("hashtag", "제주도")])
    assert report.matches[0].username == "uploader"
    assert session.owner_lookups == ["Bbbbbbbbbb2"]  # 일치한 게시물만 조회


def test_no_match_reports_count_and_errors(photo_bytes, cdn):
    session = FakeSession(
        {"@gildong": [post(f"A{i:010d}", f"other{i}") for i in range(20)]},
        errors={"@ghost": CrawlerError("@ghost 계정을 찾을 수 없어요.")},
    )
    progress = []
    report = CrawlFinder(session, http=cdn).find(
        photo_bytes,
        [Target("profile", "ghost"), Target("profile", "gildong")],
        progress=lambda count, target: progress.append((count, target)),
    )
    assert report.matches == []
    assert report.total_results == 20
    assert report.errors == ["@ghost 계정을 찾을 수 없어요."]
    assert progress[-1] == (20, "@gildong")

    text = format_report(report)
    assert "게시물 20개를 비교했지만 같은 사진을 찾지 못했어요" in text
    assert "⚠️ @ghost 계정을 찾을 수 없어요." in text


def test_stops_after_max_matches(photo_bytes, cdn):
    posts = [post(f"S{i:010d}", "same", username="a") for i in range(30)]
    session = FakeSession({"#tag": posts, "@later": [post("Lllllllllll", "same")]})
    finder = CrawlFinder(session, CrawlOptions(max_matches=2), http=cdn)
    report = finder.find(photo_bytes, [Target("hashtag", "tag"), Target("profile", "later")])
    assert len(report.matches) == 2
    assert report.total_results == 12  # 첫 묶음(12개)에서 멈춤
    assert [t for t, _ in session.requested_limits] == ["#tag"]  # 다음 대상은 보지 않음


def test_options_limit_posts_and_targets(photo_bytes, cdn):
    session = FakeSession({})
    finder = CrawlFinder(session, CrawlOptions(max_posts=7, max_targets=2), http=cdn)
    report = finder.find(
        photo_bytes, [Target("profile", "a"), Target("profile", "b"), Target("profile", "c")]
    )
    assert session.requested_limits == [("@a", 7), ("@b", 7)]
    assert report.targets == ("@a", "@b")


def test_login_error_aborts(photo_bytes, cdn):
    session = FakeSession({}, errors={"@a": LoginError("세션 만료")})
    with pytest.raises(LoginError):
        CrawlFinder(session, http=cdn).find(photo_bytes, [Target("profile", "a")])
    assert session.lock.acquire(blocking=False)  # 오류가 나도 lock 은 풀려 있어야 함


def test_requires_targets(photo_bytes):
    with pytest.raises(ValueError):
        CrawlFinder(FakeSession({})).find(photo_bytes, [])


@pytest.mark.parametrize("kwargs", [{"threshold": 0}, {"threshold": 1.5}, {"max_posts": 0}])
def test_invalid_options(kwargs):
    with pytest.raises(ValueError):
        CrawlOptions(**kwargs)
