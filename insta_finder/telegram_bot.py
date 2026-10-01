"""텔레그램 봇: 사진을 보내면 인스타그램 게시물과 올린 사용자를 찾아 답장한다.

    python -m insta_finder.telegram_bot

두 가지 검색 방식:
  - 크롤링: 사진 설명(캡션)에 @계정 이나 #해시태그 를 적으면, 로그인한 인스타그램 세션으로
            그 게시물들을 직접 내려받아 비교한다. (INSTAGRAM_USERNAME 필요)
  - Google Lens: 캡션 없이 보내면 Google Lens 로 인스타그램 전체에서 찾는다. (SERPAPI_API_KEY 필요)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import LinkPreviewOptions, Message, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import Settings
from .crawl_finder import CrawlFinder
from .crawler import CrawlerError, InstagramSession, LoginError, Target, parse_targets
from .finder import PhotoFinder
from .formatting import format_report
from .image_utils import InvalidImageError
from .models import SearchReport
from .serpapi_client import SerpApiClient, SerpApiError

log = logging.getLogger(__name__)

TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024  # Bot API 파일 다운로드 한도
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

TARGET_EXAMPLE = "예) 사진 설명에  @gildong #제주도  처럼 적어서 보내기"


def help_text(lens: bool, crawl: bool) -> str:
    lines = ["📷 사진을 보내주시면 인스타그램에서 그 사진이 올라간 게시물과 올린 사용자를 찾아드려요.\n"]
    if crawl:
        lines.append(
            "• 사진 설명(캡션)에 뒤질 계정(@아이디)이나 해시태그(#태그)를 적으면\n"
            "  그 게시물들을 하나씩 직접 비교해서 찾아요.\n"
            f"  {TARGET_EXAMPLE}"
        )
    if lens:
        lines.append("• 캡션 없이 보내면 Google Lens 로 인스타그램 전체에서 찾아요.")
    elif crawl:
        lines.append("• 인스타그램에는 사진 검색 기능이 없어서, 뒤질 계정/해시태그를 꼭 적어야 해요.")
    lines.append(
        "\n• 화질을 유지하려면 사진을 '파일'로 보내도 돼요.\n"
        "• 잘리거나 필터를 씌운 사진보다 원본에 가까운 사진이 잘 찾아져요.\n\n"
        "/help - 도움말"
    )
    return "\n".join(lines)


def split_message(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """텔레그램 글자 수 제한에 맞춰 문단 단위로 나눈다."""
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(block) > limit:
            chunks.append(block[:limit])
            block = block[limit:]
        current = block
    if current:
        chunks.append(current)
    return chunks


class InstaFinderBot:
    progress_interval = 8.0  # 크롤링 진행 상황 메시지 갱신 간격(초)

    def __init__(
        self,
        settings: Settings,
        *,
        lens_finder: Any = None,
        crawl_finder: Any = None,
    ) -> None:
        self.settings = settings
        self.instagram: InstagramSession | None = None
        if crawl_finder is None and settings.crawl_enabled:
            self.instagram = InstagramSession(
                settings.instagram_username,
                settings.instagram_password,
                settings.instagram_session_file,
            )
            crawl_finder = CrawlFinder(self.instagram, settings.crawl)
        if lens_finder is None and settings.lens_enabled:
            client = SerpApiClient(
                settings.serpapi_api_key,
                language=settings.lens_language,
                country=settings.lens_country,
            )
            lens_finder = PhotoFinder(
                client,
                settings.finder,
                owner_lookup=self.instagram.post_owner if self.instagram else None,
            )
        self.lens_finder = lens_finder
        self.crawl_finder = crawl_finder
        # 인스타그램 요청이 몰리지 않도록 크롤링 검색은 한 번에 하나만
        self._crawl_lock = asyncio.Lock()

    # ------------------------------------------------------------------ 권한
    def is_allowed(self, update: Update) -> bool:
        allowed = self.settings.allowed_user_ids
        user = update.effective_user
        return not allowed or (user is not None and user.id in allowed)

    # ------------------------------------------------------------------ 핸들러
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None:
            return
        if not self.is_allowed(update):
            await message.reply_text(self._denied_text(update))
            return
        await message.reply_text(
            help_text(lens=self.lens_finder is not None, crawl=self.crawl_finder is not None)
        )

    async def not_an_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is not None and self.is_allowed(update):
            await message.reply_text("사진을 보내주세요. 📷\n/help 로 사용법을 볼 수 있어요.")

    async def handle_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None:
            return
        if not self.is_allowed(update):
            await message.reply_text(self._denied_text(update))
            return

        if message.photo:
            attachment = message.photo[-1]  # 가장 큰 해상도
        elif message.document and (message.document.mime_type or "").startswith("image/"):
            attachment = message.document
        else:
            await self.not_an_image(update, context)
            return
        if attachment.file_size and attachment.file_size > TELEGRAM_DOWNLOAD_LIMIT:
            await message.reply_text("20MB 이하의 사진만 검색할 수 있어요.")
            return

        targets = parse_targets(getattr(message, "caption", None))
        if targets and self.crawl_finder is None:
            await message.reply_text(
                "계정/해시태그 검색(크롤링)이 설정되지 않았어요. .env 에 INSTAGRAM_USERNAME 을 설정하세요."
            )
            return
        if not targets and self.lens_finder is None:
            await message.reply_text(
                "어디서 찾을지 알려주세요. 인스타그램에는 사진 검색 기능이 없어서\n"
                f"뒤질 계정(@아이디)이나 해시태그(#태그)가 필요해요.\n{TARGET_EXAMPLE}"
            )
            return

        user_data = context.user_data if context.user_data is not None else {}
        if user_data.get("busy"):
            await message.reply_text("이전 사진을 검색하는 중이에요. 끝나면 다시 보내주세요. ⏳")
            return
        user_data["busy"] = True

        if targets:
            shown = ", ".join(str(t) for t in targets[: self.settings.crawl.max_targets])
            status = await message.reply_text(f"🔎 {shown} 의 게시물과 비교하는 중이에요...")
        else:
            status = await message.reply_text("🔎 인스타그램에서 찾는 중이에요... (보통 10~30초)")
        try:
            await context.bot.send_chat_action(message.chat_id, ChatAction.TYPING)
            file = await attachment.get_file()
            data = bytes(await file.download_as_bytearray())
            if targets:
                report = await self._crawl(status, data, targets)
            else:
                report = await asyncio.to_thread(self.lens_finder.find, data)
            text = format_report(report)
        except InvalidImageError:
            text = "이미지를 읽을 수 없어요. JPG/PNG/WebP 사진을 보내주세요."
        except LoginError as exc:
            log.warning("인스타그램 로그인 문제: %s", exc)
            text = f"인스타그램 로그인 문제로 검색하지 못했어요.\n{exc}"
        except CrawlerError as exc:
            log.warning("크롤링 오류: %s", exc)
            text = f"인스타그램에서 게시물을 가져오지 못했어요.\n{exc}"
        except SerpApiError as exc:
            log.warning("SerpApi 오류: %s", exc)
            text = f"검색 서비스 오류로 찾지 못했어요. 잠시 후 다시 시도해 주세요.\n({exc})"
        except Exception:
            log.exception("검색 중 예기치 못한 오류")
            text = "알 수 없는 오류가 발생했어요. 잠시 후 다시 시도해 주세요."
        finally:
            user_data["busy"] = False

        await self._reply_long(status, text)

    # ------------------------------------------------------------------ 크롤링
    async def _crawl(self, status: Message, data: bytes, targets: list[Target]) -> SearchReport:
        if self._crawl_lock.locked():
            await self._edit_quietly(status, "다른 검색이 진행 중이에요. 끝나면 바로 시작할게요. ⏳")
        async with self._crawl_lock:
            progress = {"count": 0, "target": ""}

            def on_progress(count: int, target: str) -> None:  # 작업 스레드에서 호출됨
                progress["count"], progress["target"] = count, target

            task = asyncio.ensure_future(
                asyncio.to_thread(self.crawl_finder.find, data, targets, on_progress)
            )
            last_text = ""
            while True:
                done, _ = await asyncio.wait({task}, timeout=self.progress_interval)
                if done:
                    return task.result()
                if progress["target"]:
                    text = (
                        f"🔎 {progress['target']} 확인 중... "
                        f"지금까지 게시물 {progress['count']}개 비교했어요."
                    )
                    if text != last_text:
                        last_text = text
                        await self._edit_quietly(status, text)

    # ------------------------------------------------------------------ 보조
    @staticmethod
    def _denied_text(update: Update) -> str:
        user_id = update.effective_user.id if update.effective_user else "?"
        return f"이 봇을 사용할 권한이 없어요. (내 사용자 ID: {user_id})"

    @staticmethod
    async def _edit_quietly(status: Message, text: str) -> None:
        try:
            await status.edit_text(text)
        except TelegramError as exc:  # 진행 상황 갱신 실패는 무시
            log.debug("상태 메시지 갱신 실패: %s", exc)

    @staticmethod
    async def _reply_long(status: Message, text: str) -> None:
        chunks = split_message(text)
        await status.edit_text(chunks[0], link_preview_options=NO_PREVIEW)
        for chunk in chunks[1:]:
            await status.reply_text(chunk, link_preview_options=NO_PREVIEW)

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("텔레그램 처리 오류", exc_info=context.error)

    async def _on_shutdown(self, application: Application) -> None:
        for finder in (self.lens_finder, self.crawl_finder):
            close = getattr(finder, "close", None)
            if close:
                close()
        client = getattr(self.lens_finder, "client", None)
        if client is not None and hasattr(client, "close"):
            client.close()

    def build_application(self) -> Application:
        app = (
            ApplicationBuilder()
            .token(self.settings.telegram_bot_token)
            .concurrent_updates(True)
            .post_shutdown(self._on_shutdown)
            .build()
        )
        app.add_handler(CommandHandler(["start", "help"], self.start))
        app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, self.handle_image))
        app.add_handler(
            MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, self.not_an_image)
        )
        app.add_error_handler(self._on_error)
        return app


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
    )
    # httpx 요청 로그에는 봇 토큰/API 키가 담긴 URL 이 찍히므로 숨긴다
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    settings = Settings.from_env()
    if not settings.telegram_bot_token:
        raise SystemExit("환경 변수(.env)를 설정하세요: TELEGRAM_BOT_TOKEN")
    if not settings.lens_enabled and not settings.crawl_enabled:
        raise SystemExit(
            "검색 방식을 하나 이상 설정하세요: INSTAGRAM_USERNAME(크롤링) 또는 SERPAPI_API_KEY(Google Lens)"
        )
    if not settings.allowed_user_ids:
        log.warning("ALLOWED_USER_IDS 가 비어 있어 누구나 봇을 사용할 수 있습니다.")

    bot = InstaFinderBot(settings)
    if bot.instagram is not None:
        try:
            bot.instagram.login()  # 세션이 유효한지 시작할 때 확인
        except LoginError as exc:
            raise SystemExit(str(exc)) from None
        log.info("인스타그램 @%s 세션으로 로그인했습니다.", bot.instagram.username)

    log.info("봇을 시작합니다. 텔레그램에서 사진을 보내보세요.")
    bot.build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":  # pragma: no cover
    main()
