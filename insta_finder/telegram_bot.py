"""텔레그램 봇: 사진을 보내면 인스타그램 게시물과 올린 사용자를 찾아 답장한다.

    python -m insta_finder.telegram_bot
"""

from __future__ import annotations

import asyncio
import logging

from telegram import LinkPreviewOptions, Message, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import Settings
from .finder import PhotoFinder
from .formatting import format_report
from .image_utils import InvalidImageError
from .serpapi_client import SerpApiClient, SerpApiError

log = logging.getLogger(__name__)

TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024  # Bot API 파일 다운로드 한도
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

HELP_TEXT = (
    "📷 사진을 보내주시면 인스타그램에서 그 사진이 올라간 게시물과 올린 사용자를 찾아드려요.\n\n"
    "• 그냥 사진으로 보내도 되고, 화질을 유지하려면 '파일'로 보내도 돼요.\n"
    "• 잘리거나 필터를 씌운 사진보다 원본에 가까운 사진이 잘 찾아져요.\n"
    "• 공개 계정 중 Google 에 노출된 게시물만 찾을 수 있어요.\n\n"
    "/help - 도움말"
)


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
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = SerpApiClient(
            settings.serpapi_api_key,
            language=settings.lens_language,
            country=settings.lens_country,
        )
        self.finder = PhotoFinder(self.client, settings.finder)

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
        await message.reply_text(HELP_TEXT)

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

        user_data = context.user_data if context.user_data is not None else {}
        if user_data.get("busy"):
            await message.reply_text("이전 사진을 검색하는 중이에요. 끝나면 다시 보내주세요. ⏳")
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

        user_data["busy"] = True
        status = await message.reply_text("🔎 인스타그램에서 찾는 중이에요... (보통 10~30초)")
        try:
            await context.bot.send_chat_action(message.chat_id, ChatAction.TYPING)
            file = await attachment.get_file()
            data = bytes(await file.download_as_bytearray())
            report = await asyncio.to_thread(self.finder.find, data)
            text = format_report(report)
        except InvalidImageError:
            text = "이미지를 읽을 수 없어요. JPG/PNG/WebP 사진을 보내주세요."
        except SerpApiError as exc:
            log.warning("SerpApi 오류: %s", exc)
            text = f"검색 서비스 오류로 찾지 못했어요. 잠시 후 다시 시도해 주세요.\n({exc})"
        except Exception:
            log.exception("검색 중 예기치 못한 오류")
            text = "알 수 없는 오류가 발생했어요. 잠시 후 다시 시도해 주세요."
        finally:
            user_data["busy"] = False

        await self._reply_long(status, text)

    # ------------------------------------------------------------------ 보조
    @staticmethod
    def _denied_text(update: Update) -> str:
        user_id = update.effective_user.id if update.effective_user else "?"
        return f"이 봇을 사용할 권한이 없어요. (내 사용자 ID: {user_id})"

    @staticmethod
    async def _reply_long(status: Message, text: str) -> None:
        chunks = split_message(text)
        await status.edit_text(chunks[0], link_preview_options=NO_PREVIEW)
        for chunk in chunks[1:]:
            await status.reply_text(chunk, link_preview_options=NO_PREVIEW)

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("텔레그램 처리 오류", exc_info=context.error)

    async def _on_shutdown(self, application: Application) -> None:
        self.finder.close()
        self.client.close()

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
    missing = [
        name
        for name, value in (
            ("SERPAPI_API_KEY", settings.serpapi_api_key),
            ("TELEGRAM_BOT_TOKEN", settings.telegram_bot_token),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"환경 변수(.env)를 설정하세요: {', '.join(missing)}")
    if not settings.allowed_user_ids:
        log.warning("ALLOWED_USER_IDS 가 비어 있어 누구나 봇을 사용할 수 있습니다 (API 크레딧 주의).")

    bot = InstaFinderBot(settings)
    log.info("봇을 시작합니다. 텔레그램에서 사진을 보내보세요.")
    bot.build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":  # pragma: no cover
    main()
