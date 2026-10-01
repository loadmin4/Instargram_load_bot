from pathlib import Path

import instaloader
import pytest

from insta_finder.crawler import (
    CrawlerError,
    InstagramSession,
    LoginError,
    Target,
    TwoFactorRequiredError,
    parse_targets,
    to_crawled_post,
)

# ---------------------------------------------------------------- 대상 파싱


def test_parse_targets():
    text = (
        "이 사진 찾아줘 @gildong #제주도 #Sunset_Jeju https://www.instagram.com/foo_bar/ "
        "메일 a@b.com 번호 #123 그리고 @Gildong 또, @dot.name."
    )
    assert parse_targets(text) == [
        Target("profile", "gildong"),
        Target("hashtag", "제주도"),
        Target("hashtag", "sunset_jeju"),
        Target("profile", "foo_bar"),
        Target("profile", "dot.name"),
    ]


@pytest.mark.parametrize("text", [None, "", "그냥 설명", "#123", "@.", "https://www.instagram.com/p/C1a2B3c4D5e/"])
def test_parse_targets_empty(text):
    assert parse_targets(text) == []


def test_target_str():
    assert str(Target("profile", "gildong")) == "@gildong"
    assert str(Target("hashtag", "제주도")) == "#제주도"


# ---------------------------------------------------------------- instaloader 게시물 변환


def iphone_media(code="C1a2B3c4D5e", media_type=8, product_type="carousel_container"):
    """로그인 상태의 프로필 게시물 응답(iphone struct) 모양."""
    media = {
        "code": code,
        "pk": "3141592653",
        "media_type": media_type,
        "product_type": product_type,
        "taken_at": 1709424000,  # 2024-03-03
        "caption": {"text": "제주 노을 🌅"},
        "has_liked": False,
        "like_count": 10,
        "image_versions2": {"candidates": [{"url": "https://cdn.example/cover.jpg"}]},
        "user": {
            "pk": "1",
            "username": "GilDong",
            "is_private": False,
            "full_name": "Gil Dong",
            "profile_pic_url": "https://cdn.example/pic.jpg",
        },
    }
    if media_type == 8:
        media["carousel_media"] = [
            {"media_type": 1, "image_versions2": {"candidates": [{"url": "https://cdn.example/1.jpg"}]}},
            {"media_type": 1, "image_versions2": {"candidates": [{"url": "https://cdn.example/2.jpg"}]}},
        ]
    return media


def test_to_crawled_post_from_profile_feed():
    post = instaloader.Post.from_iphone_struct(None, iphone_media())
    crawled = to_crawled_post(post)
    assert crawled.shortcode == "C1a2B3c4D5e"
    assert crawled.kind == "post"
    assert crawled.image_urls == ["https://cdn.example/1.jpg", "https://cdn.example/2.jpg"]
    assert crawled.username == "gildong"
    assert crawled.taken_at.strftime("%Y-%m-%d") == "2024-03-03"
    assert crawled.caption == "제주 노을 🌅"
    assert crawled.url == "https://www.instagram.com/p/C1a2B3c4D5e/"


def test_to_crawled_post_reel():
    post = instaloader.Post.from_iphone_struct(
        None, iphone_media(code="Cz9Y8x7W6v5", media_type=2, product_type="clips")
    )
    crawled = to_crawled_post(post)
    assert crawled.kind == "reel"
    assert crawled.image_urls == ["https://cdn.example/cover.jpg"]  # 영상은 표지 이미지로 비교
    assert crawled.url == "https://www.instagram.com/reel/Cz9Y8x7W6v5/"


def test_to_crawled_post_from_hashtag_graphql_without_username():
    node = {
        "shortcode": "B7abcdefgh",
        "__typename": "GraphImage",
        "is_video": False,
        "display_url": "https://cdn.example/tag.jpg",
        "taken_at_timestamp": 1709424000,
        "owner": {"id": "42"},  # 해시태그 결과에는 사용자 ID 만 있다
        "edge_media_to_caption": {"edges": [{"node": {"text": "#제주도"}}]},
    }
    crawled = to_crawled_post(instaloader.Post(None, node))
    assert crawled.username is None  # 추가 요청 없이 모르면 None
    assert crawled.image_urls == ["https://cdn.example/tag.jpg"]
    assert crawled.caption == "#제주도"


def test_to_crawled_post_uses_owner_hint_and_skips_imageless():
    node = {"shortcode": "B7abcdefgh", "display_url": "https://cdn.example/x.jpg"}
    assert to_crawled_post(instaloader.Post(None, node), owner_hint="gildong").username == "gildong"
    assert to_crawled_post(instaloader.Post(None, {"shortcode": "B7abcdefgh"})) is None


# ---------------------------------------------------------------- 로그인/세션


class FakeLoader:
    def __init__(self, session_valid=True, login_error=None, two_factor=False):
        self.context = object()
        self.session_valid = session_valid
        self.login_error = login_error
        self.two_factor = two_factor
        self.calls = []

    def load_session_from_file(self, username, filename):
        self.calls.append(("load", username))

    def test_login(self):
        self.calls.append(("test",))
        return "me" if self.session_valid else None

    def login(self, user, passwd):
        self.calls.append(("login", user))
        if self.login_error:
            raise self.login_error
        if self.two_factor:
            raise instaloader.TwoFactorAuthRequiredException("2FA")

    def two_factor_login(self, code):
        self.calls.append(("2fa", code))

    def save_session_to_file(self, filename):
        self.calls.append(("save",))
        Path(filename).write_text("session")


def test_login_reuses_saved_session(tmp_path):
    session_file = tmp_path / "ig.session"
    session_file.write_text("x")
    loader = FakeLoader()
    session = InstagramSession("me", session_file=session_file, loader=loader)
    session.login()
    session.login()  # 두 번째는 아무 요청도 하지 않음
    assert loader.calls == [("load", "me"), ("test",)]


def test_login_falls_back_to_password_and_saves(tmp_path):
    session_file = tmp_path / "sub" / "ig.session"
    loader = FakeLoader()
    session = InstagramSession("@me", "pw", session_file=session_file, loader=loader)
    session.login()
    assert ("login", "me") in loader.calls
    assert session_file.exists()
    assert oct(session_file.stat().st_mode & 0o777) == "0o600"


def test_login_expired_session_without_password(tmp_path):
    session_file = tmp_path / "ig.session"
    session_file.write_text("x")
    session = InstagramSession("me", session_file=session_file, loader=FakeLoader(session_valid=False))
    with pytest.raises(LoginError, match="insta_finder.login"):
        session.login()


def test_login_two_factor(tmp_path):
    loader = FakeLoader(two_factor=True)
    session = InstagramSession("me", session_file=tmp_path / "s", loader=loader)
    with pytest.raises(TwoFactorRequiredError):
        session.password_login("pw")
    session.password_login("pw", two_factor_code=lambda: "123456")
    assert ("2fa", "123456") in loader.calls


def test_login_bad_password(tmp_path):
    loader = FakeLoader(login_error=instaloader.BadCredentialsException("bad"))
    session = InstagramSession("me", "pw", session_file=tmp_path / "s", loader=loader)
    with pytest.raises(LoginError, match="비밀번호"):
        session.login()


def test_session_repr_hides_password():
    assert "secret-pw" not in repr(InstagramSession("me", "secret-pw", loader=FakeLoader()))


# ---------------------------------------------------------------- 게시물 가져오기


class FakeProfile:
    def __init__(self, posts, is_private=False, followed=False):
        self.username = "gildong"
        self.is_private = is_private
        self.followed_by_viewer = followed
        self._posts = posts

    def get_posts(self):
        return iter(self._posts)


def logged_in_session(tmp_path):
    session_file = tmp_path / "ig.session"
    session_file.write_text("x")
    return InstagramSession("me", session_file=session_file, loader=FakeLoader())


def test_iter_posts_profile_respects_limit(tmp_path, monkeypatch):
    posts = [
        instaloader.Post.from_iphone_struct(None, iphone_media(code=f"C1a2B3c4D{i:02d}"))
        for i in range(5)
    ]
    monkeypatch.setattr(instaloader.Profile, "from_username", lambda ctx, name: FakeProfile(posts))
    result = list(logged_in_session(tmp_path).iter_posts(Target("profile", "gildong"), limit=3))
    assert [p.shortcode for p in result] == ["C1a2B3c4D00", "C1a2B3c4D01", "C1a2B3c4D02"]


def test_iter_posts_private_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(
        instaloader.Profile, "from_username", lambda ctx, name: FakeProfile([], is_private=True)
    )
    with pytest.raises(CrawlerError, match="비공개"):
        list(logged_in_session(tmp_path).iter_posts(Target("profile", "gildong"), limit=3))


def test_iter_posts_missing_profile(tmp_path, monkeypatch):
    def missing(ctx, name):
        raise instaloader.ProfileNotExistsException("nope")

    monkeypatch.setattr(instaloader.Profile, "from_username", missing)
    with pytest.raises(CrawlerError, match="찾을 수 없어요"):
        list(logged_in_session(tmp_path).iter_posts(Target("profile", "ghost"), limit=3))


class FakeHashtag:
    def __init__(self, top, recent_error=None, recent=()):
        self.top = top
        self.recent_error = recent_error
        self.recent = recent

    def get_top_posts(self):
        return iter(self.top)

    def get_posts_resumable(self):
        if self.recent_error:
            raise self.recent_error
        return iter(self.recent)

    def get_posts(self):
        return iter(self.recent)


def graphql_post(code):
    return instaloader.Post(None, {"shortcode": code, "display_url": f"https://cdn.example/{code}.jpg"})


def test_iter_posts_hashtag_continues_after_one_source_fails_and_dedupes(tmp_path, monkeypatch):
    tag = FakeHashtag(
        top=[graphql_post("Atop0000001"), graphql_post("Bboth000002")],
        recent_error=instaloader.QueryReturnedBadRequestException("blocked"),
        recent=[graphql_post("Bboth000002"), graphql_post("Crecent0003")],
    )
    monkeypatch.setattr(instaloader.Hashtag, "from_name", lambda ctx, name: tag)
    result = list(logged_in_session(tmp_path).iter_posts(Target("hashtag", "제주도"), limit=10))
    assert [p.shortcode for p in result] == ["Atop0000001", "Bboth000002", "Crecent0003"]


def test_iter_posts_hashtag_all_sources_fail(tmp_path, monkeypatch):
    class Broken(FakeHashtag):
        def get_top_posts(self):
            raise instaloader.QueryReturnedBadRequestException("blocked")

        def get_posts(self):
            raise instaloader.QueryReturnedBadRequestException("blocked")

    tag = Broken(top=[], recent_error=instaloader.QueryReturnedBadRequestException("blocked"))
    monkeypatch.setattr(instaloader.Hashtag, "from_name", lambda ctx, name: tag)
    with pytest.raises(CrawlerError, match="가져오지 못했어요"):
        list(logged_in_session(tmp_path).iter_posts(Target("hashtag", "x"), limit=10))


def test_iter_posts_rate_limited(tmp_path, monkeypatch):
    class Limited(FakeHashtag):
        def get_top_posts(self):
            raise instaloader.TooManyRequestsException("429")

    monkeypatch.setattr(instaloader.Hashtag, "from_name", lambda ctx, name: Limited(top=[]))
    with pytest.raises(CrawlerError, match="요청 제한"):
        list(logged_in_session(tmp_path).iter_posts(Target("hashtag", "x"), limit=10))


def test_post_owner(tmp_path, monkeypatch):
    class FakePost:
        owner_username = "gildong"

    monkeypatch.setattr(instaloader.Post, "from_shortcode", lambda ctx, code: FakePost())
    assert logged_in_session(tmp_path).post_owner("C1a2B3c4D5e") == "gildong"
