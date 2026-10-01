import base64
import io
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from PIL import Image

from insta_finder.ai_targets import (
    PhotoClues,
    SuggestionError,
    TargetSuggester,
    clues_to_targets,
)
from insta_finder.crawler import Target
from tests.conftest import make_image, to_bytes


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def fake_client(response=None, error=None):
    messages = FakeMessages(response, error)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def clues(**overrides):
    data = {
        "description": "제주 성산일출봉 일출",
        "accounts": [],
        "hashtags": ["성산일출봉", "제주일출"],
        "places": ["성산일출봉"],
    }
    data.update(overrides)
    return PhotoClues(**data)


def ok(parsed):
    return SimpleNamespace(stop_reason="end_turn", parsed_output=parsed)


def test_suggest_sends_image_and_parses_targets():
    client, messages = fake_client(ok(clues(accounts=["@jeju_photo"])))
    big = to_bytes(make_image(seed=5, size=(4000, 3000)), quality=95)

    suggestion = TargetSuggester(client).suggest(big, hint="제주도 여행")

    assert suggestion.description == "제주 성산일출봉 일출"
    assert suggestion.targets == [
        Target("profile", "jeju_photo"),
        Target("hashtag", "성산일출봉"),
        Target("hashtag", "제주일출"),
    ]
    assert suggestion.places == ["성산일출봉"]

    kwargs = messages.kwargs
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["output_format"] is PhotoClues
    assert kwargs["fallbacks"] == "default"
    assert kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    image_block, text_block = kwargs["messages"][0]["content"]
    assert image_block["source"]["media_type"] == "image/jpeg"
    sent = Image.open(io.BytesIO(base64.standard_b64decode(image_block["source"]["data"])))
    assert max(sent.size) <= 1568  # Claude 권장 해상도로 줄여서 보냄
    assert "제주도 여행" in text_block["text"]
    assert "face" in kwargs["system"]  # 얼굴로 사람을 식별하지 말라는 지시 포함


def test_summary_lists_targets():
    client, _ = fake_client(ok(clues()))
    suggestion = TargetSuggester(client).suggest(to_bytes(make_image(seed=1)))
    assert suggestion.summary() == (
        "🤖 AI 분석: 제주 성산일출봉 일출\n→ #성산일출봉 #제주일출 에서 찾아봤어요."
    )


def test_clues_to_targets_filters_and_limits():
    targets = clues_to_targets(
        clues(
            accounts=["@Jeju.Photo", "not a handle!", "explore", "jeju.photo"],
            hashtags=["#성산 일출봉", "2024", "제주일출", "bad-tag", "#제주일출", "jeju", "a", "b"],
        ),
        max_targets=4,
    )
    assert targets == [
        Target("profile", "jeju.photo"),
        Target("hashtag", "성산일출봉"),
        Target("hashtag", "제주일출"),
        Target("hashtag", "jeju"),
    ]


def test_refusal_raises():
    client, _ = fake_client(SimpleNamespace(stop_reason="refusal", parsed_output=None))
    with pytest.raises(SuggestionError, match="거절"):
        TargetSuggester(client).suggest(to_bytes(make_image(seed=1)))


def _status_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


@pytest.mark.parametrize(
    "error, expected",
    [
        (_status_error(anthropic.AuthenticationError, 401), "ANTHROPIC_API_KEY"),
        (_status_error(anthropic.RateLimitError, 429), "한도"),
        (_status_error(anthropic.InternalServerError, 500), "HTTP 500"),
        (
            anthropic.APIConnectionError(
                request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            ),
            "연결",
        ),
    ],
)
def test_api_errors_are_wrapped(error, expected):
    client, _ = fake_client(error=error)
    with pytest.raises(SuggestionError, match=expected):
        TargetSuggester(client).suggest(to_bytes(make_image(seed=1)))
