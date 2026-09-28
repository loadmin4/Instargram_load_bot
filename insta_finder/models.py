"""검색 결과를 표현하는 데이터 클래스."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# 검색 종류별 우선순위 (작을수록 신뢰도가 높음)
MATCH_TYPE_PRIORITY = {"exact_matches": 0, "visual_matches": 1}


@dataclass(frozen=True)
class InstagramRef:
    """인스타그램 URL 을 해석한 결과."""

    kind: str  # "post" | "reel" | "tv" | "story" | "profile"
    url: str  # 정규화된 URL
    shortcode: str | None = None
    username: str | None = None

    @property
    def is_post(self) -> bool:
        return self.kind in {"post", "reel", "tv"}


@dataclass
class Match:
    """역이미지 검색으로 찾은 인스타그램 후보 하나."""

    ref: InstagramRef
    match_type: str  # "exact_matches" | "visual_matches"
    position: int
    title: str = ""
    link: str = ""
    thumbnail: str | None = None
    date: str | None = None
    username: str | None = None
    display_name: str | None = None
    similarity: float | None = None  # 0.0 ~ 1.0, 썸네일 비교 결과

    @property
    def is_exact(self) -> bool:
        return self.match_type == "exact_matches"

    def sort_key(self) -> tuple:
        return (
            MATCH_TYPE_PRIORITY.get(self.match_type, 99),
            -(self.similarity if self.similarity is not None else -1.0),
            self.position,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ref"] = asdict(self.ref)
        return data


@dataclass
class SearchReport:
    """한 번의 사진 검색 결과 요약."""

    matches: list[Match] = field(default_factory=list)
    total_results: int = 0  # Google Lens 가 돌려준 전체 결과 수 (인스타그램 외 포함)
    search_types: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_results": self.total_results,
            "search_types": list(self.search_types),
            "matches": [m.to_dict() for m in self.matches],
        }
