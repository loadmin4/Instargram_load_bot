import asyncio
from types import SimpleNamespace

import pytest

from insta_finder.config import Settings
from insta_finder.crawler import LoginError, Target
from insta_finder.models import InstagramRef, Match, SearchReport
from insta_finder.serpapi_client import SerpApiError
from insta_finder.telegram_bot import InstaFinderBot, split_message


def test_settings_defaults():
    settings = Settings.from_env({"SERPAPI_API_KEY": " key "})
    assert settings.serpapi_api_key == "key"
    assert settings.allowed_user_ids == frozenset()
    assert settings.lens_language == "en"
    assert settings.finder.search_types == ("exact_matches", "visual_matches")
    assert settings.finder.max_results == 10
    assert settings.finder.verify_thumbnails is True
    assert settings.finder.resolve_usernames == 0
    assert settings.lens_enabled and not settings.crawl_enabled
    assert settings.instagram_session_file == "instagram.session"
    assert settings.crawl.max_posts == 200
    assert settings.crawl.threshold == 0.82


def test_settings_instagram():
    settings = Settings.from_env(
        {
            "INSTAGRAM_USERNAME": "@me",
            "INSTAGRAM_PASSWORD": "secret-pw",
            "INSTAGRAM_SESSION_FILE": "/tmp/x.session",
            "CRAWL_MAX_POSTS": "50",
            "CRAWL_MATCH_THRESHOLD": "0.9",
            "CRAWL_MAX_MATCHES": "0",
        }
    )
    assert settings.crawl_enabled and not settings.lens_enabled
    assert settings.instagram_username == "me"
    assert settings.instagram_session_file == "/tmp/x.session"
    assert (settings.crawl.max_posts, settings.crawl.threshold, settings.crawl.max_matches) == (
        50,
        0.9,
        0,
    )
    # 로그인 세션이 있으면 게시자 조회를 기본으로 켠다
    assert settings.finder.resolve_usernames == 3


def test_settings_repr_hides_secrets():
    settings = Settings.from_env(
        {
            "SERPAPI_API_KEY": "serp-secret",
            "TELEGRAM_BOT_TOKEN": "tg-secret",
            "INSTAGRAM_USERNAME": "me",
            "INSTAGRAM_PASSWORD": "pw-secret",
        }
    )
    text = repr(settings)
    assert "serp-secret" not in text and "tg-secret" not in text and "pw-secret" not in text


def test_settings_custom_values():
    settings = Settings.from_env(
        {
            "ALLOWED_USER_IDS": "123, 456",
            "SEARCH_TYPES": "exact_matches",
            "MAX_RESULTS": "3",
            "VERIFY_THUMBNAILS": "0",
            "RESOLVE_USERNAMES": "2",
            "LENS_LANGUAGE": "ko",
            "LENS_COUNTRY": "kr",
            "LENS_QUERY": "instagram",
        }
    )
    assert settings.allowed_user_ids == frozenset({123, 456})
    assert settings.finder.search_types == ("exact_matches",)
    assert settings.finder.max_results == 3
    assert settings.finder.verify_thumbnails is False
    assert settings.finder.resolve_usernames == 2
    assert settings.lens_language == "ko"
    assert settings.lens_country == "kr"
    assert settings.finder.query_hint == "instagram"


@pytest.mark.parametrize(
    "env",
    [
        {"ALLOWED_USER_IDS": "abc"},
        {"MAX_RESULTS": "many"},
        {"SEARCH_TYPES": "products"},
        {"CRAWL_MATCH_THRESHOLD": "high"},
        {"CRAWL_MATCH_THRESHOLD": "2"},
    ],
)
def test_settings_invalid(env):
    with pytest.raises(ValueError):
        Settings.from_env(env)


def test_split_message_short():
    assert split_message("hello") == ["hello"]


def test_split_message_respects_limit_and_paragraphs():
    blocks = [f"block {i} " + "x" * 30 for i in range(10)]
    chunks = split_message("\n\n".join(blocks), limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "\n\n".join(chunks) == "\n\n".join(blocks)


def test_split_message_very_long_block():
    chunks = split_message("y" * 250, limit=100)
    assert [len(c) for c in chunks] == [100, 100, 50]


# ---------------------------------------------------------------- 텔레그램 핸들러

class FakeMessage:
    def __init__(self, photo=None, document=None, caption=None):
        self.photo = photo or []
        self.document = document
        self.caption = caption
        self.chat_id = 1
        self.replies: list[str] = []
        self.edits: list[str] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return self

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)
        return self


class FakeAttachment:
    file_size = 1000

    async def get_file(self):
        async def download_as_bytearray():
            return bytearray(b"image-bytes")

        return SimpleNamespace(download_as_bytearray=download_as_bytearray)


class FakeFinder:
    def __init__(self, result):
        self.result = result
        self.calls: list = []

    def find(self, data, *args):
        self.calls.append((data, *args[:1]))
        if isinstance(self.result, Exception):
            raise self.result
        if args and len(args) > 1 and args[1] is not None:
            args[1](24, "@gildong")  # 진행 상황 콜백
        return self.result


def make_bot(lens=None, crawl=None, allowed=()):
    env = {"TELEGRAM_BOT_TOKEN": "1:t"}
    if allowed:
        env["ALLOWED_USER_IDS"] = ",".join(str(a) for a in allowed)
    return InstaFinderBot(Settings.from_env(env), lens_finder=lens, crawl_finder=crawl)


def run_handler(bot, message, user_id=7, user_data=None):
    async def send_chat_action(*args, **kwargs):
        return True

    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=user_id))
    context = SimpleNamespace(
        user_data={} if user_data is None else user_data,
        bot=SimpleNamespace(send_chat_action=send_chat_action),
    )
    asyncio.run(bot.handle_image(update, context))
    return context


def test_handle_photo_replies_with_results():
    match = Match(
        ref=InstagramRef(
            kind="post", url="https://www.instagram.com/p/C1a2B3c4D5e/", shortcode="C1a2B3c4D5e"
        ),
        match_type="exact_matches",
        position=1,
        username="gildong",
    )
    finder = FakeFinder(SearchReport(matches=[match], total_results=1))
    message = FakeMessage(photo=[FakeAttachment(), FakeAttachment()])

    context = run_handler(make_bot(lens=finder), message)

    assert finder.calls == [(b"image-bytes",)]
    assert "찾는 중" in message.replies[0]
    assert "@gildong" in message.edits[0]
    assert context.user_data["busy"] is False


def test_handle_photo_reports_search_error():
    message = FakeMessage(photo=[FakeAttachment()])
    finder = FakeFinder(SerpApiError("Your account has run out of searches."))
    run_handler(make_bot(lens=finder), message)
    assert "검색 서비스 오류" in message.edits[0]


def test_handle_image_document():
    document = FakeAttachment()
    document.mime_type = "image/png"
    finder = FakeFinder(SearchReport())
    message = FakeMessage(document=document)
    run_handler(make_bot(lens=finder), message)
    assert finder.calls == [(b"image-bytes",)]
    assert "찾지 못했어요" in message.edits[0]


def test_handle_photo_denied_for_unknown_user():
    finder = FakeFinder(SearchReport())
    message = FakeMessage(photo=[FakeAttachment()])
    run_handler(make_bot(lens=finder, allowed=(123,)), message, user_id=7)
    assert finder.calls == []
    assert "권한이 없어요" in message.replies[0]
    assert "7" in message.replies[0]


def test_handle_photo_rejects_while_busy():
    finder = FakeFinder(SearchReport())
    message = FakeMessage(photo=[FakeAttachment()])
    run_handler(make_bot(lens=finder), message, user_data={"busy": True})
    assert finder.calls == []
    assert "검색하는 중" in message.replies[0]


def test_build_application_registers_handlers():
    app = make_bot(lens=FakeFinder(SearchReport())).build_application()
    assert len(app.handlers[0]) == 3


def crawl_report():
    match = Match(
        ref=InstagramRef(
            kind="post", url="https://www.instagram.com/p/C1a2B3c4D5e/", shortcode="C1a2B3c4D5e"
        ),
        match_type="crawl",
        position=5,
        username="gildong",
        similarity=0.97,
    )
    return SearchReport(
        matches=[match], total_results=24, mode="crawl", search_types=("crawl",), targets=("@gildong",)
    )


def test_caption_with_targets_uses_crawl():
    lens, crawl = FakeFinder(SearchReport()), FakeFinder(crawl_report())
    message = FakeMessage(photo=[FakeAttachment()], caption="이거 찾아줘 @gildong #제주도")
    run_handler(make_bot(lens=lens, crawl=crawl), message)
    assert lens.calls == []
    assert crawl.calls == [(b"image-bytes", [Target("profile", "gildong"), Target("hashtag", "제주도")])]
    assert "@gildong, #제주도 의 게시물과 비교하는 중" in message.replies[0]
    assert "같은 사진 1건" in message.edits[-1]
    assert "유사도 97%" in message.edits[-1]


def test_crawl_progress_updates_status():
    class SlowFinder(FakeFinder):
        def find(self, data, targets, progress):
            import time

            progress(12, "@gildong")
            time.sleep(0.3)
            return self.result

    bot = make_bot(crawl=SlowFinder(crawl_report()))
    bot.progress_interval = 0.05
    message = FakeMessage(photo=[FakeAttachment()], caption="@gildong")
    run_handler(bot, message)
    assert any("게시물 12개 비교" in text for text in message.edits[:-1])
    assert "같은 사진 1건" in message.edits[-1]


def test_no_caption_without_lens_asks_for_targets():
    crawl = FakeFinder(crawl_report())
    message = FakeMessage(photo=[FakeAttachment()])
    context = run_handler(make_bot(crawl=crawl), message)
    assert crawl.calls == []
    assert "어디서 찾을지" in message.replies[0]
    assert not context.user_data.get("busy")


def test_targets_without_crawl_configured():
    lens = FakeFinder(SearchReport())
    message = FakeMessage(photo=[FakeAttachment()], caption="@gildong")
    run_handler(make_bot(lens=lens), message)
    assert lens.calls == []
    assert "INSTAGRAM_USERNAME" in message.replies[0]


def test_crawl_login_error_message():
    crawl = FakeFinder(LoginError("세션이 만료됐어요."))
    message = FakeMessage(photo=[FakeAttachment()], caption="#제주도")
    run_handler(make_bot(crawl=crawl), message)
    assert "로그인 문제" in message.edits[-1]
    assert "세션이 만료됐어요." in message.edits[-1]
