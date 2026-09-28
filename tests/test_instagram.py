import pytest

from insta_finder.instagram import (
    author_from_title,
    is_valid_username,
    parse_instagram_url,
    username_from_snippet,
    username_from_title,
)


@pytest.mark.parametrize(
    "url, kind, shortcode, username, canonical",
    [
        ("https://www.instagram.com/p/C1a2B3c4D5e/", "post", "C1a2B3c4D5e", None,
         "https://www.instagram.com/p/C1a2B3c4D5e/"),
        ("https://instagram.com/p/C1a2B3c4D5e?igsh=abc", "post", "C1a2B3c4D5e", None,
         "https://www.instagram.com/p/C1a2B3c4D5e/"),
        ("https://www.instagram.com/reel/Cx_Y-z12345/", "reel", "Cx_Y-z12345", None,
         "https://www.instagram.com/reel/Cx_Y-z12345/"),
        ("https://www.instagram.com/reels/Cx_Y-z12345/", "reel", "Cx_Y-z12345", None,
         "https://www.instagram.com/reel/Cx_Y-z12345/"),
        ("https://www.instagram.com/tv/B7abcdefgh/", "tv", "B7abcdefgh", None,
         "https://www.instagram.com/tv/B7abcdefgh/"),
        ("https://www.instagram.com/gil.dong_99/p/C1a2B3c4D5e/", "post", "C1a2B3c4D5e",
         "gil.dong_99", "https://www.instagram.com/p/C1a2B3c4D5e/"),
        ("https://m.instagram.com/gildong/reel/C1a2B3c4D5e/", "reel", "C1a2B3c4D5e", "gildong",
         "https://www.instagram.com/reel/C1a2B3c4D5e/"),
        ("https://www.instagram.com/stories/gildong/3141592653589793/", "story",
         "3141592653589793", "gildong",
         "https://www.instagram.com/stories/gildong/3141592653589793/"),
        ("https://www.instagram.com/gildong/", "profile", None, "gildong",
         "https://www.instagram.com/gildong/"),
        ("https://www.instagram.com/gildong/tagged/", "profile", None, "gildong",
         "https://www.instagram.com/gildong/"),
    ],
)
def test_parse_instagram_url(url, kind, shortcode, username, canonical):
    ref = parse_instagram_url(url)
    assert ref is not None
    assert ref.kind == kind
    assert ref.shortcode == shortcode
    assert ref.username == username
    assert ref.url == canonical


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://www.pinterest.com/pin/123/",
        "https://notinstagram.com/p/C1a2B3c4D5e/",
        "https://www.instagram.com/",
        "https://www.instagram.com/explore/tags/cat/",
        "https://www.instagram.com/accounts/login/",
        "https://www.instagram.com/p/",
        "https://www.instagram.com/p/%%%/",
        "not a url",
    ],
)
def test_parse_instagram_url_rejects(url):
    assert parse_instagram_url(url) is None


@pytest.mark.parametrize(
    "value, ok",
    [
        ("gildong", True),
        ("gil.dong_99", True),
        ("a" * 30, True),
        ("a" * 31, False),
        (".gildong", False),
        ("gildong.", False),
        ("gil..dong", False),
        ("explore", False),
        ("홍길동", False),
        ("", False),
    ],
)
def test_is_valid_username(value, ok):
    assert is_valid_username(value) is ok


@pytest.mark.parametrize(
    "title, expected",
    [
        ("홍길동 (@gildong) • Instagram photos and videos", "gildong"),
        ("@gil.dong_99 • Instagram 사진 및 동영상", "gil.dong_99"),
        ("Photo by @gildong on Instagram", "gildong"),
        ("홍길동 on Instagram: \"오늘의 사진\"", None),
        ("contact@gildong.com", None),
        ("", None),
    ],
)
def test_username_from_title(title, expected):
    assert username_from_title(title) == expected


@pytest.mark.parametrize(
    "title, expected",
    [
        ("홍길동 on Instagram: \"오늘의 사진\"", "홍길동"),
        ("홍길동 (@gildong) • Instagram photos and videos", "홍길동"),
        ("gildong on Instagram: \"hello\"", "gildong"),
        ("홍길동님의 Instagram 게시물", "홍길동"),
        ("Instagram의 홍길동님: \"사진\"", "홍길동"),
        ("Gil Dong | Instagram", "Gil Dong"),
        ("Instagram", None),
        ("Some random page", None),
    ],
)
def test_author_from_title(title, expected):
    assert author_from_title(title) == expected


@pytest.mark.parametrize(
    "snippet, expected",
    [
        ('1,234 likes, 56 comments - gildong on March 3, 2024: "오늘의 사진"', "gildong"),
        ('12K likes, 1 comment - gil.dong_99 on July 14, 2025: "hi"', "gil.dong_99"),
        ('gildong on May 5, 2023: "sunset"', "gildong"),
        ("좋아요 1,234개, 댓글 56개 - gildong님, 2024년 3월 3일", "gildong"),
        ("Nothing useful here", None),
    ],
)
def test_username_from_snippet(snippet, expected):
    assert username_from_snippet(snippet) == expected
