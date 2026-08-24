import base64
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import unescape

import aiohttp
from wilhelmina.utils import html_to_markdown

logger = logging.getLogger(__name__)

ATTACHMENT_TYPE = "ATTACHMENT"
MAX_THREAD_AGE_DAYS = 7


def parse_daisy_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def is_recent_thread(summary: dict, max_age_days: int = MAX_THREAD_AGE_DAYS) -> bool:
    saved = parse_daisy_timestamp(str(summary.get("NewestSaved") or ""))
    if saved is None:
        return True
    return saved >= datetime.now() - timedelta(days=max_age_days)


@dataclass
class DaisyAttachment:
    id: str
    filename: str
    mime_type: str


@dataclass
class DaisyMessage:
    id: str
    thread_id: str
    title: str
    sender: str
    timestamp: str
    content: str
    attachments: list[DaisyAttachment] = field(default_factory=list)


class DaisyClient:
    def __init__(self, base_url: str, session: aiohttp.ClientSession):
        self.api_url = base_url.rstrip("/") + "/api"
        self.session = session
        self.token: str | None = None

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise RuntimeError("Not logged in")
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        }

    async def login(self, username: str, password: str) -> None:
        url = f"{self.api_url}/auth/login"
        async with self.session.post(
            url,
            json={"Username": username, "Password": password},
            headers={"Content-Type": "application/json; charset=utf-8"},
        ) as response:
            body = await response.text()
            if response.status != 200:
                raise RuntimeError(f"Daisy login failed {response.status}: {body}")
            data = await response.json()
            self.token = data.get("Token")
            if not self.token:
                raise RuntimeError("Daisy login response missing Token")

    async def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.api_url}{path}"
        async with self.session.post(url, json=payload, headers=self._headers()) as response:
            body = await response.text()
            if response.status != 200:
                raise RuntimeError(f"Daisy API error {response.status} on {path}: {body}")
            return await response.json()

    async def get_message_list(self) -> list[dict]:
        data = await self._post("/Messages/GetMessageList2/", {})
        return data.get("listMessage") or []

    async def get_message_thread(self, thread_id: str) -> dict:
        return await self._post(
            "/Messages/GetMessageThread2",
            {"ThreadId": thread_id, "MessageId": "", "HideRecipients": True},
        )

    async def download_attachment(self, attachment: DaisyAttachment) -> bytes:
        url = f"{self.api_url}/files/downloadFile"
        payload = {
            "FileId": attachment.id,
            "FileType": ATTACHMENT_TYPE,
            "FileMimeType": attachment.mime_type or "",
        }
        async with self.session.post(url, json=payload, headers=self._headers()) as response:
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(
                    f"Daisy file download failed {response.status}: {body[:200]}"
                )

            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                data = await response.json()
                if isinstance(data, str):
                    if data.startswith("data:") and "," in data:
                        return base64.b64decode(data.split(",", 1)[1])
                    return base64.b64decode(data)
                if isinstance(data, dict):
                    raw = data.get("Data") or data.get("data")
                    if isinstance(raw, str):
                        if raw.startswith("data:") and "," in raw:
                            return base64.b64decode(raw.split(",", 1)[1])
                        return base64.b64decode(raw)
                    if isinstance(raw, bytes):
                        return raw
                raise RuntimeError("Unexpected Daisy file download response")

            return await response.read()


def format_content(content: str) -> str:
    if not content:
        return "(no content)"

    if "<" in content and ">" in content:
        text = html_to_markdown(unescape(content)).replace("\xa0", " ")
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    return content.strip()


def parse_thread_messages(thread: dict) -> list[DaisyMessage]:
    title = thread.get("Title") or "(no subject)"
    thread_id = str(thread.get("ThreadId") or "")
    messages: list[DaisyMessage] = []

    for item in thread.get("listMessageModels") or []:
        message_thread_id = str(item.get("ThreadId") or thread_id)
        sender = (item.get("MessageSender") or {}).get("Name") or "?"
        attachments = [
            DaisyAttachment(
                id=str(att.get("Id") or ""),
                filename=str(att.get("FileName") or "attachment"),
                mime_type=str(att.get("MimeType") or "application/octet-stream"),
            )
            for att in (item.get("ListAttachments") or [])
            if att.get("Id")
        ]
        messages.append(
            DaisyMessage(
                id=str(item.get("Id") or ""),
                thread_id=message_thread_id,
                title=title,
                sender=sender,
                timestamp=str(item.get("Saved") or ""),
                content=format_content(str(item.get("Content") or "")),
                attachments=attachments,
            )
        )

    return messages


def format_notification(message: DaisyMessage) -> str:
    lines = [
        "Daisy Family",
        f"Aihe: {message.title}",
        f"Lähettäjä: {message.sender}",
        f"Aika: {message.timestamp}",
    ]
    if message.attachments:
        lines.append(f"Liitteitä: {len(message.attachments)}")
    lines.extend(["", message.content])
    return "\n".join(lines)
