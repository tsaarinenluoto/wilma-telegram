import aiohttp

TELEGRAM_MAX_LENGTH = 4096


async def send_telegram_message(
    session: aiohttp.ClientSession,
    token: str,
    chat_id: str,
    text: str,
) -> None:
    if len(text) > TELEGRAM_MAX_LENGTH:
        text = text[: TELEGRAM_MAX_LENGTH - 3] + "..."

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with session.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
    ) as response:
        if response.status != 200:
            body = await response.text()
            raise RuntimeError(f"Telegram API error {response.status}: {body}")


async def send_telegram_file(
    session: aiohttp.ClientSession,
    token: str,
    chat_id: str,
    data: bytes,
    filename: str,
    mime_type: str,
    caption: str | None = None,
    disable_content_type_detection: bool = False,
) -> None:
    if mime_type.startswith("image/"):
        method = "sendPhoto"
        field = "photo"
    else:
        method = "sendDocument"
        field = "document"

    url = f"https://api.telegram.org/bot{token}/{method}"
    form = aiohttp.FormData()
    form.add_field("chat_id", chat_id)
    if caption:
        form.add_field("caption", caption[:TELEGRAM_MAX_LENGTH])
    if disable_content_type_detection and method == "sendDocument":
        form.add_field("disable_content_type_detection", "true")
    form.add_field(
        field,
        data,
        filename=filename,
        content_type=mime_type or "application/octet-stream",
    )

    async with session.post(url, data=form) as response:
        if response.status != 200:
            body = await response.text()
            raise RuntimeError(f"Telegram API error {response.status}: {body}")
