"""Send a one-line test message via the Campus Coach networking bot."""

from __future__ import annotations

import os
import sys

from networking_coach import get_networking_telegram_credentials, send_networking_telegram

BOT_NAME = os.environ.get("NETWORKING_BOT_NAME") or "Campus Coach"
MESSAGE = os.environ.get("TEST_MESSAGE") or (
    f"Hi — {BOT_NAME} here. Telegram notifications are working."
)


def main() -> None:
    token, chat_id = get_networking_telegram_credentials()
    if not token:
        sys.exit(
            "error: NETWORKING_TELEGRAM_BOT_TOKEN is not set. "
            "Add it under Settings → Secrets → Actions."
        )
    if not chat_id:
        sys.exit(
            "error: NETWORKING_TELEGRAM_CHAT_ID is not set. "
            "Get your id from @userinfobot on Telegram."
        )
    print(f"Sending test message to chat {chat_id}...")
    send_networking_telegram(MESSAGE)
    print("Done.")


if __name__ == "__main__":
    main()
