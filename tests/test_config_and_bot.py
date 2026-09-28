import asyncio
from types import SimpleNamespace

import pytest

from insta_finder.config import Settings
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
    [{"ALLOWED_USER_IDS": "abc"}, {"MAX_RESULTS": "many"}, {"SEARCH_TYPES": "products"}],
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
    def __init__(self, photo=None, document=None):
        self.photo = photo or []
        self.document = document
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
        self.calls: list[bytes] = []

    def find(self, data):
        self.calls.append(data)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_bot(finder, allowed=()):
    env = {"SERPAPI_API_KEY": "k", "TELEGRAM_BOT_TOKEN": "1:t"}
    if allowed:
        env["ALLOWED_USER_IDS"] = ",".join(str(a) for a in allowed)
    bot = InstaFinderBot(Settings.from_env(env))
    bot.finder = finder
    return bot


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

    context = run_handler(make_bot(finder), message)

    assert finder.calls == [b"image-bytes"]
    assert "찾는 중" in message.replies[0]
    assert "@gildong" in message.edits[0]
    assert context.user_data["busy"] is False


def test_handle_photo_reports_search_error():
    message = FakeMessage(photo=[FakeAttachment()])
    finder = FakeFinder(SerpApiError("Your account has run out of searches."))
    run_handler(make_bot(finder), message)
    assert "검색 서비스 오류" in message.edits[0]


def test_handle_image_document():
    document = FakeAttachment()
    document.mime_type = "image/png"
    finder = FakeFinder(SearchReport())
    message = FakeMessage(document=document)
    run_handler(make_bot(finder), message)
    assert finder.calls == [b"image-bytes"]
    assert "찾지 못했어요" in message.edits[0]


def test_handle_photo_denied_for_unknown_user():
    finder = FakeFinder(SearchReport())
    message = FakeMessage(photo=[FakeAttachment()])
    run_handler(make_bot(finder, allowed=(123,)), message, user_id=7)
    assert finder.calls == []
    assert "권한이 없어요" in message.replies[0]
    assert "7" in message.replies[0]


def test_handle_photo_rejects_while_busy():
    finder = FakeFinder(SearchReport())
    message = FakeMessage(photo=[FakeAttachment()])
    run_handler(make_bot(finder), message, user_data={"busy": True})
    assert finder.calls == []
    assert "검색하는 중" in message.replies[0]


def test_build_application_registers_handlers():
    app = make_bot(FakeFinder(SearchReport())).build_application()
    assert len(app.handlers[0]) == 3
