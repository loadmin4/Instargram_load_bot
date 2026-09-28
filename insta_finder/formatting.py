"""검색 결과를 사람이 읽기 좋은 한국어 문장으로 만든다."""

from __future__ import annotations

from .models import Match, SearchReport

_KIND_LABEL = {
    "post": "게시물",
    "reel": "릴스",
    "tv": "IGTV",
    "story": "스토리",
    "profile": "프로필",
}
_TYPE_LABEL = {"exact_matches": "완전 일치", "visual_matches": "유사 이미지"}


def _uploader(match: Match) -> str:
    if match.username:
        name = f"@{match.username}"
        if match.display_name and match.display_name.lower() != match.username.lower():
            name += f" ({match.display_name})"
        return name
    if match.display_name:
        return f"{match.display_name} (표시 이름, 링크에서 확인 필요)"
    return "알 수 없음 (링크에서 확인)"


def format_match(index: int, match: Match) -> str:
    kind = _KIND_LABEL.get(match.ref.kind, match.ref.kind)
    header = f"{index}. [{_TYPE_LABEL.get(match.match_type, match.match_type)}] {kind}"
    if match.similarity is not None:
        header += f" · 유사도 {match.similarity * 100:.0f}%"
    lines = [header, f"   올린 사람: {_uploader(match)}"]
    if match.ref.kind == "profile":
        lines.append(f"   프로필: {match.ref.url}")
    else:
        lines.append(f"   링크: {match.ref.url}")
        if match.username:
            lines.append(f"   프로필: https://www.instagram.com/{match.username}/")
    if match.date:
        lines.append(f"   날짜: {match.date}")
    if match.title:
        title = match.title if len(match.title) <= 80 else match.title[:77] + "..."
        lines.append(f"   제목: {title}")
    return "\n".join(lines)


def format_report(report: SearchReport) -> str:
    if not report.matches:
        return (
            "인스타그램에서 이 사진을 찾지 못했어요. 😢\n"
            f"(Google Lens 전체 결과 {report.total_results}건 중 인스타그램 링크 없음)\n\n"
            "팁: 원본에 가까운 사진(캡처 대신 원본, 자르지 않은 사진)일수록 잘 찾아요.\n"
            "비공개 계정이나 검색엔진에 노출되지 않은 게시물은 찾을 수 없어요."
        )
    exact = sum(1 for m in report.matches if m.is_exact)
    head = f"🔎 인스타그램 후보 {len(report.matches)}건을 찾았어요"
    head += f" (완전 일치 {exact}건)." if exact else "."
    body = "\n\n".join(format_match(i, m) for i, m in enumerate(report.matches, start=1))
    footer = (
        "※ '완전 일치'는 같은 사진, '유사 이미지'는 비슷한 사진일 수 있어요.\n"
        "※ 최초 게시자가 아니라 퍼간 계정일 수도 있으니 날짜를 함께 확인하세요."
    )
    return f"{head}\n\n{body}\n\n{footer}"
