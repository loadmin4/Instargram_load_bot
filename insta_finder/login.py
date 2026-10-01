"""인스타그램에 한 번 로그인해 세션 파일을 만든다 (2단계 인증 지원).

    python -m insta_finder.login

봇과 CLI 는 이 세션 파일을 재사용하므로 비밀번호를 .env 에 저장하지 않아도 된다.
"""

from __future__ import annotations

import getpass
import sys

from .config import Settings
from .crawler import InstagramSession, LoginError


def main() -> int:
    settings = Settings.from_env()
    username = settings.instagram_username or input("인스타그램 아이디: ").strip().lstrip("@")
    if not username:
        print("아이디를 입력하세요.", file=sys.stderr)
        return 2
    password = settings.instagram_password or getpass.getpass("비밀번호 (화면에 표시되지 않음): ")

    session = InstagramSession(username, session_file=settings.instagram_session_file)
    print("로그인 중...")
    try:
        session.password_login(
            password, two_factor_code=lambda: input("2단계 인증 코드: ").strip()
        )
    except LoginError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"로그인 성공! 세션을 {session.session_file} 에 저장했어요.")
    if not settings.instagram_username:
        print(f".env 에 INSTAGRAM_USERNAME={username} 을 추가하세요.")
    print("이 파일은 비밀번호처럼 다루세요 (다른 사람과 공유하거나 git 에 올리지 마세요).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
