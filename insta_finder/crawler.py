"""인스타그램에 로그인해 지정한 계정/해시태그의 게시물을 가져온다 (instaloader 사용).

인스타그램에는 "사진으로 검색" 기능이 없으므로, 뒤질 대상(@계정, #해시태그)을 정해
그 게시물 이미지들을 하나씩 내려받아 비교하는 방식이다.

주의: 자동화된 수집은 인스타그램 이용약관에 어긋나며 계정이 제한될 수 있다.
요청 수를 줄이기 위해
  - 로그인 세션을 파일에 저장해 재사용하고 (매번 로그인하지 않음)
  - 게시물마다 추가 API 요청을 보내지 않으며 (iphone_support=False, 목록 응답만 사용)
  - instaloader 의 기본 요청 속도 제한(RateController)을 그대로 따른다.
"""

from __future__ import annotations

import itertools
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from . import instagram

log = logging.getLogger(__name__)


class CrawlerError(RuntimeError):
    """대상 하나를 가져오지 못함 (없는 계정, 비공개 계정, 요청 제한 등)."""


class LoginError(CrawlerError):
    """로그인 실패 또는 세션 만료. 이 오류가 나면 검색 전체를 중단한다."""


class TwoFactorRequiredError(LoginError):
    """2단계 인증 코드가 필요함."""


LOGIN_HINT = "터미널에서 `python -m insta_finder.login` 을 실행해 다시 로그인하세요."


def _instaloader():
    try:
        import instaloader
    except ImportError as exc:  # pragma: no cover
        raise CrawlerError("instaloader 가 설치되지 않았습니다: pip install instaloader") from exc
    return instaloader


# ---------------------------------------------------------------------- 검색 대상


@dataclass(frozen=True)
class Target:
    kind: str  # "profile" | "hashtag"
    name: str

    def __str__(self) -> str:
        return f"@{self.name}" if self.kind == "profile" else f"#{self.name}"


_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9._]{1,30})")
_HASHTAG_RE = re.compile(r"(?<![\w&#])#(\w{1,100})")
_URL_RE = re.compile(r"https?://\S+")


def parse_targets(text: str | None) -> list[Target]:
    """"@gildong #제주도 https://instagram.com/foo" 같은 문장에서 검색 대상을 뽑는다."""
    if not text:
        return []
    found: list[tuple[int, Target]] = []
    for m in _URL_RE.finditer(text):
        ref = instagram.parse_instagram_url(m.group(0))
        if ref is not None and ref.kind == "profile" and ref.username:
            found.append((m.start(), Target("profile", ref.username.lower())))
    without_urls = _URL_RE.sub(lambda m: " " * len(m.group(0)), text)
    for m in _MENTION_RE.finditer(without_urls):
        name = m.group(1).rstrip(".")
        if instagram.is_valid_username(name):
            found.append((m.start(), Target("profile", name.lower())))
    for m in _HASHTAG_RE.finditer(without_urls):
        name = m.group(1).strip("_")
        if name and not name.isdigit():
            found.append((m.start(), Target("hashtag", name.lower())))

    targets: list[Target] = []
    for _, target in sorted(found, key=lambda item: item[0]):
        if target not in targets:
            targets.append(target)
    return targets


# ---------------------------------------------------------------------- 게시물


@dataclass
class CrawledPost:
    shortcode: str
    kind: str  # "post" | "reel"
    image_urls: list[str]
    username: str | None = None  # 추가 요청 없이 알 수 있을 때만
    taken_at: datetime | None = None
    caption: str | None = None

    @property
    def url(self) -> str:
        return instagram.post_url(self.kind, self.shortcode)


def _node(post: Any) -> dict[str, Any]:
    # instaloader 의 공개 속성 중 일부(owner_username, get_sidecar_nodes 등)는 값이 없으면
    # 게시물마다 추가 API 요청을 보내므로, 목록 응답으로 받은 원본 데이터만 읽는다.
    node = getattr(post, "_node", None)
    return node if isinstance(node, dict) else {}


def _image_urls(node: dict[str, Any]) -> list[str]:
    urls = []
    edges = (node.get("edge_sidecar_to_children") or {}).get("edges") or []
    for edge in edges:
        url = (edge.get("node") or {}).get("display_url")
        if url:
            urls.append(url)
    if not urls:
        url = node.get("display_url") or node.get("display_src") or node.get("thumbnail_src")
        if url:
            urls.append(url)
    return urls


def _known_username(post: Any, node: dict[str, Any]) -> str | None:
    owner = node.get("owner") or {}
    if isinstance(owner, dict) and owner.get("username"):
        return str(owner["username"])
    profile = getattr(post, "_owner_profile", None)
    if profile is not None:
        try:
            return profile.username
        except Exception:  # 프로필 정보가 덜 채워진 경우
            return None
    return None


def to_crawled_post(post: Any, owner_hint: str | None = None) -> CrawledPost | None:
    node = _node(post)
    shortcode = node.get("shortcode") or node.get("code")
    urls = _image_urls(node)
    if not shortcode or not urls:
        return None
    product_type = node.get("product_type") or (node.get("iphone_struct") or {}).get("product_type")
    timestamp = node.get("date") or node.get("taken_at_timestamp")
    caption = node.get("caption")
    if caption is None:
        edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
        caption = ((edges[0].get("node") or {}).get("text")) if edges else None
    return CrawledPost(
        shortcode=shortcode,
        kind="reel" if product_type == "clips" else "post",
        image_urls=urls,
        username=_known_username(post, node) or owner_hint,
        taken_at=datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp else None,
        caption=caption if isinstance(caption, str) else None,
    )


# ---------------------------------------------------------------------- 세션


class InstagramSession:
    """로그인 세션을 관리하고 게시물을 가져온다.

    instaloader 컨텍스트는 동시에 여러 요청을 보내면 요청 제한에 걸리기 쉬우므로
    lock 으로 한 번에 하나의 작업만 하게 한다.
    """

    def __init__(
        self,
        username: str,
        password: str | None = None,
        session_file: str | os.PathLike | None = None,
        *,
        loader: Any = None,
    ) -> None:
        if not username:
            raise ValueError("INSTAGRAM_USERNAME 이 설정되지 않았습니다.")
        self.username = username.strip().lstrip("@")
        self.session_file = Path(session_file) if session_file else None
        self._password = password or None
        self._loader = loader
        self._logged_in = False
        self.lock = threading.RLock()

    def __repr__(self) -> str:  # 비밀번호가 로그에 찍히지 않도록
        return f"InstagramSession(username={self.username!r}, session_file={self.session_file!r})"

    @staticmethod
    def new_loader() -> Any:
        il = _instaloader()
        return il.Instaloader(
            quiet=True,
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            save_metadata=False,
            compress_json=False,
            # True 이면 로그인 상태에서 게시물 이미지 URL 을 읽을 때마다 API 요청이 하나씩 더 나간다
            iphone_support=False,
            max_connection_attempts=2,
            request_timeout=60.0,
        )

    @property
    def loader(self) -> Any:
        if self._loader is None:
            self._loader = self.new_loader()
        return self._loader

    # ------------------------------------------------------------------ 로그인
    def login(self) -> None:
        """저장된 세션을 불러오고, 없거나 만료됐으면 비밀번호로 로그인한다."""
        with self.lock:
            if self._logged_in:
                return
            il = _instaloader()
            if self.session_file and self.session_file.exists():
                try:
                    self.loader.load_session_from_file(self.username, str(self.session_file))
                    if self.loader.test_login():
                        self._logged_in = True
                        return
                    log.warning("저장된 인스타그램 세션이 만료됐습니다.")
                except (OSError, il.InstaloaderException) as exc:
                    log.warning("인스타그램 세션을 불러오지 못했습니다: %s", exc)

            if not self._password:
                raise LoginError(f"저장된 로그인 세션이 없거나 만료됐어요. {LOGIN_HINT}")
            self.password_login(self._password)

    def password_login(self, password: str, two_factor_code: Callable[[], str] | None = None) -> None:
        il = _instaloader()
        with self.lock:
            try:
                try:
                    self.loader.login(self.username, password)
                except il.TwoFactorAuthRequiredException:
                    if two_factor_code is None:
                        raise TwoFactorRequiredError(
                            f"2단계 인증 코드가 필요해요. {LOGIN_HINT}"
                        ) from None
                    self.loader.two_factor_login(two_factor_code())
            except il.BadCredentialsException:
                raise LoginError("인스타그램 아이디 또는 비밀번호가 틀렸어요.") from None
            except il.InstaloaderException as exc:
                # 보안 확인(checkpoint)이 뜨면 앱/웹에서 직접 "본인 맞음"을 눌러야 한다
                raise LoginError(
                    f"인스타그램 로그인 실패: {exc}\n"
                    "인스타그램 앱이나 웹에서 로그인 확인 요청을 승인한 뒤 다시 시도하세요."
                ) from None
            self._logged_in = True
            self.save()

    def save(self) -> None:
        if not self.session_file:
            return
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.loader.save_session_to_file(str(self.session_file))
        try:
            os.chmod(self.session_file, 0o600)  # 세션 파일은 비밀번호와 같으므로 본인만 읽게
        except OSError:  # pragma: no cover - Windows 등
            pass

    # ------------------------------------------------------------------ 조회
    def iter_posts(self, target: Target, limit: int) -> Iterator[CrawledPost]:
        """대상의 게시물을 최신순(해시태그는 인기 게시물 먼저)으로 최대 limit 개 돌려준다."""
        il = _instaloader()
        self.login()
        context = self.loader.context
        try:
            if target.kind == "profile":
                profile = il.Profile.from_username(context, target.name)
                if profile.is_private and not profile.followed_by_viewer:
                    raise CrawlerError(f"{target} 은(는) 비공개 계정이라 볼 수 없어요 (팔로우 필요).")
                sources: list[Callable[[], Iterable[Any]]] = [profile.get_posts]
                owner_hint = profile.username
            else:
                hashtag = il.Hashtag.from_name(context, target.name)
                # 인스타그램 쪽 변경으로 일부 방식이 막혀도 나머지로 계속하도록 여러 경로를 차례로 쓴다
                sources = [hashtag.get_top_posts, hashtag.get_posts_resumable, hashtag.get_posts]
                owner_hint = None
        except il.ProfileNotExistsException:
            raise CrawlerError(f"{target} 계정을 찾을 수 없어요.") from None
        except il.LoginRequiredException:
            self._logged_in = False
            raise LoginError(f"인스타그램 로그인이 풀렸어요. {LOGIN_HINT}") from None
        except il.InstaloaderException as exc:
            raise CrawlerError(f"{target} 정보를 가져오지 못했어요: {exc}") from None

        seen: set[str] = set()
        posts = self._chain_sources(target, sources)
        for post in itertools.islice(posts, limit * 2):  # 중복 제거 여유분
            crawled = to_crawled_post(post, owner_hint=owner_hint)
            if crawled is None or crawled.shortcode in seen:
                continue
            seen.add(crawled.shortcode)
            yield crawled
            if len(seen) >= limit:
                return

    def _chain_sources(
        self, target: Target, sources: list[Callable[[], Iterable[Any]]]
    ) -> Iterator[Any]:
        il = _instaloader()
        yielded = 0
        last_error: Exception | None = None
        for source in sources:
            try:
                for post in source():
                    yielded += 1
                    yield post
            except il.LoginRequiredException:
                self._logged_in = False
                raise LoginError(f"인스타그램 로그인이 풀렸어요. {LOGIN_HINT}") from None
            except il.TooManyRequestsException:
                raise CrawlerError(
                    "인스타그램 요청 제한에 걸렸어요. 한참 뒤에 다시 시도하세요."
                ) from None
            except il.InstaloaderException as exc:
                log.info("%s 게시물 조회 경로 실패: %s", target, exc)
                last_error = exc
        if yielded == 0 and last_error is not None:
            raise CrawlerError(f"{target} 게시물을 가져오지 못했어요: {last_error}")

    def post_owner(self, shortcode: str) -> str | None:
        """게시물 shortcode 로 게시자 사용자 이름을 조회한다 (요청 1회)."""
        il = _instaloader()
        with self.lock:
            self.login()
            try:
                return il.Post.from_shortcode(self.loader.context, shortcode).owner_username
            except il.InstaloaderException as exc:
                log.info("게시자 조회 실패 %s: %s", shortcode, exc)
                return None
