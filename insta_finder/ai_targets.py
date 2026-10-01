"""Claude 가 사진을 보고 인스타그램에서 뒤질 해시태그/계정을 추정한다.

인스타그램에는 사진 검색 기능이 없으므로, 사진 속 장소·행사·간판·워터마크 등을 단서로
그 사진이 올라갔을 법한 #해시태그 와 @계정 을 고르고, 크롤링 검색의 대상으로 쓴다.

얼굴로 사람을 알아내는 일은 하지 않는다 (사진 속 글자와 장면만 단서로 쓴다).
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from . import image_utils, instagram
from .crawler import Target

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
# Claude 가 이미지를 처리하는 최대 해상도에 맞춘다
_MAX_IMAGE_SIDE = 1568
_HASHTAG_RE = re.compile(r"^\w{1,100}$")

SYSTEM_PROMPT = """\
You help find where a photo was posted on Instagram. Instagram has no image search, so the \
caller will scan the posts under the hashtags and accounts you suggest and compare each image \
with this photo. Your suggestions decide where to look.

Use only what is visible in the photo and the user's hint:
- Text in the image: watermarks, @handles, logos, shop signs, menus, posters, Instagram UI in \
screenshots. A handle you can read clearly is the strongest clue.
- The scene: landmarks, cities, venues, events, festivals, dishes, products, artworks.

Do not try to recognize who a person is from their face or body, and do not guess accounts \
of people. Only list an account when its handle is written in the image or the hint, or it is \
the official account of a place/brand/event you identified from the scene and you are confident \
of the exact handle.

Hashtags: write them the way Instagram users actually tag such photos, without '#' and without \
spaces. Order from most specific to least (exact place or event first, then city/region). \
Include Korean tags when the scene is in Korea or the hint is Korean, plus English tags if \
they are commonly used. Skip generic tags that match millions of unrelated photos (love, \
instagood, photooftheday, daily, travel, food). Suggest fewer, better tags rather than many.\
"""


class PhotoClues(BaseModel):
    description: str = Field(description="사진 내용을 한국어 한 문장으로 요약")
    accounts: list[str] = Field(
        description="Instagram usernames without '@' (only per the rules), best first"
    )
    hashtags: list[str] = Field(
        description="Hashtags without '#', most specific first"
    )
    places: list[str] = Field(description="Place or event names you identified, if any")


class SuggestionError(RuntimeError):
    """AI 분석 실패 (API 키 오류, 거절, 네트워크 오류 등)."""


@dataclass
class TargetSuggestion:
    description: str
    targets: list[Target] = field(default_factory=list)
    places: list[str] = field(default_factory=list)

    def summary(self) -> str:
        shown = " ".join(str(t) for t in self.targets)
        return f"🤖 AI 분석: {self.description}\n→ {shown} 에서 찾아봤어요."


def clues_to_targets(clues: PhotoClues, max_targets: int) -> list[Target]:
    """계정을 먼저(가장 정확한 단서), 그다음 해시태그 순으로 중복 없이 고른다."""
    targets: list[Target] = []
    for raw in clues.accounts:
        name = raw.strip().lstrip("@").rstrip(".").lower()
        if instagram.is_valid_username(name):
            targets.append(Target("profile", name))
    for raw in clues.hashtags:
        name = re.sub(r"\s+", "", raw.strip().lstrip("#")).lower()
        if _HASHTAG_RE.match(name) and not name.isdigit():
            targets.append(Target("hashtag", name))
    unique: list[Target] = []
    for target in targets:
        if target not in unique:
            unique.append(target)
    return unique[:max_targets]


class TargetSuggester:
    def __init__(self, client: Any = None, model: str = DEFAULT_MODEL, max_targets: int = 5) -> None:
        self._client = client
        self.model = model
        self.max_targets = max_targets

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            # ANTHROPIC_API_KEY 환경 변수 (또는 `ant auth login` 프로필)를 사용한다
            self._client = anthropic.Anthropic()
        return self._client

    def suggest(self, image_bytes: bytes, hint: str | None = None) -> TargetSuggestion:
        import anthropic

        jpeg = image_utils.prepare_for_upload(image_bytes, max_side=_MAX_IMAGE_SIDE)
        text = "Where on Instagram should I look for this photo?"
        if hint and hint.strip():
            text += f"\n\nHint from the user (may be Korean): {hint.strip()}"
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": base64.standard_b64encode(jpeg).decode("ascii"),
                                },
                            },
                            {"type": "text", "text": text},
                        ],
                    }
                ],
                output_config={"effort": "medium"},
                output_format=PhotoClues,
                # 안전 분류기가 거절하면 서버가 다른 모델로 자동 재시도한다
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            raise SuggestionError("Claude API 키가 올바르지 않아요. ANTHROPIC_API_KEY 를 확인하세요.") from None
        except anthropic.RateLimitError:
            raise SuggestionError("Claude API 사용량 한도에 걸렸어요. 잠시 후 다시 시도하세요.") from None
        except anthropic.APIStatusError as exc:
            raise SuggestionError(f"Claude API 오류 (HTTP {exc.status_code}): {exc.message}") from None
        except anthropic.APIConnectionError:
            raise SuggestionError("Claude API 에 연결할 수 없어요.") from None

        if response.stop_reason == "refusal":
            raise SuggestionError("AI 가 이 사진의 분석을 거절했어요. 캡션에 @계정 이나 #해시태그 를 직접 적어주세요.")
        clues = response.parsed_output
        if clues is None:
            raise SuggestionError(f"AI 응답을 해석하지 못했어요 (stop_reason={response.stop_reason}).")
        log.info("AI 추정: %s", clues.model_dump())
        return TargetSuggestion(
            description=clues.description.strip(),
            targets=clues_to_targets(clues, self.max_targets),
            places=[p.strip() for p in clues.places if p.strip()],
        )
