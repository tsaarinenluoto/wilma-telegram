import asyncio
import logging
import os
import re
from html import unescape
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from wilhelmina import WilmaClient
from wilhelmina.utils import html_to_markdown

from wilma_telegram.http_log import create_trace_config
from wilma_telegram.daisy import (
    DaisyClient,
    format_notification as format_daisy_notification,
    is_recent_thread,
    parse_thread_messages,
)
from wilma_telegram.calendar_events import send_calendar_events
from wilma_telegram.state import StateStore
from wilma_telegram.students import Student, parse_students_from_home
from wilma_telegram.telegram import send_telegram_file, send_telegram_message

logger = logging.getLogger(__name__)


def load_config() -> dict:
    load_dotenv()

    required = [
        "WILMA_URL",
        "WILMA_USERNAME",
        "WILMA_PASSWORD",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    return {
        "wilma_url": os.getenv("WILMA_URL", "").rstrip("/"),
        "wilma_username": os.getenv("WILMA_USERNAME", ""),
        "wilma_password": os.getenv("WILMA_PASSWORD", ""),
        "daisy_url": os.getenv("DAISY_URL", "").rstrip("/"),
        "daisy_username": os.getenv("DAISY_USERNAME", ""),
        "daisy_password": os.getenv("DAISY_PASSWORD", ""),
        "telegram_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "poll_interval": int(os.getenv("POLL_INTERVAL", "300")),
        "initial_sync": os.getenv("INITIAL_SYNC", "").strip() in {"1", "true", "yes"},
        "state_path": Path(os.getenv("STATE_PATH", "data/seen.json")),
        "xai_api_key": os.getenv("XAI_API_KEY", ""),
        "xai_model": os.getenv("XAI_MODEL", "grok-4.6"),
        "icloud_apple_id": os.getenv("ICLOUD_APPLE_ID", "").strip(),
        "icloud_app_password": os.getenv("ICLOUD_APP_PASSWORD", "").strip(),
        "icloud_calendar": os.getenv("ICLOUD_CALENDAR", "").strip(),
    }


def daisy_enabled(config: dict) -> bool:
    return bool(config["daisy_username"] and config["daisy_password"])


def daisy_threads_to_fetch(threads: list[dict], fetch_all: bool) -> list[dict]:
    result = []
    for summary in threads:
        thread_id = str(summary.get("ThreadId") or "")
        if not thread_id:
            continue
        if not is_recent_thread(summary):
            continue
        if fetch_all or summary.get("CountNotReadMessages", 0) > 0:
            result.append(summary)
    return result


async def discover_students(client: WilmaClient) -> list[Student]:
    response = await client._authenticated_request("")
    html = await response.text()
    students = parse_students_from_home(html)

    if students:
        return students

    if client.user_id:
        number = client.user_id.lstrip("!")
        return [Student(number=number, name="Wilma", user_id=client.user_id)]

    raise RuntimeError("Could not find any students on the Wilma home page")


def message_body(message) -> str:
    if message.content_html:
        text = html_to_markdown(unescape(message.content_html)).replace("\xa0", " ")
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    content = getattr(message, "content", None)
    if content:
        return str(content).strip()

    return "(no content)"


def format_notification(students: list[Student], message) -> str:
    names = ", ".join(student.name for student in students)
    header = f"Wilma – {names}"
    if len(students) > 1:
        header += " (useita vastaanottajia)"
    lines = [
        header,
        f"Aihe: {message.subject}",
        f"Lähettäjä: {message.sender}",
        f"Aika: {message.timestamp}",
        "",
        message_body(message),
    ]
    return "\n".join(lines)


async def process_daisy(
    client: DaisyClient,
    state: StateStore,
    config: dict,
    http: aiohttp.ClientSession,
    notify: bool,
    threads: list[dict],
) -> int:
    fetch_all = not notify
    threads_to_fetch = daisy_threads_to_fetch(threads, fetch_all)
    logger.info(
        "Checking %s of %s Daisy threads",
        len(threads_to_fetch),
        len(threads),
    )

    sent = 0
    for index, thread_summary in enumerate(threads_to_fetch):
        thread_id = str(thread_summary.get("ThreadId") or "")
        try:
            thread = await client.get_message_thread(thread_id)
        except Exception:
            logger.exception("Failed to fetch Daisy thread %s", thread_id)
            continue

        if not thread.get("ThreadId"):
            thread["ThreadId"] = thread_id

        for message in parse_thread_messages(thread):
            scope = f"daisy:{message.thread_id or thread_id}"
            if state.is_seen(scope, message.id):
                continue

            if notify:
                text = format_daisy_notification(message)
                await send_telegram_message(
                    http,
                    config["telegram_token"],
                    config["telegram_chat_id"],
                    text,
                )
                await send_calendar_events(
                    http,
                    config,
                    message.title,
                    message.content,
                    message.timestamp,
                )
                for attachment in message.attachments:
                    try:
                        data = await client.download_attachment(attachment)
                        await send_telegram_file(
                            http,
                            config["telegram_token"],
                            config["telegram_chat_id"],
                            data,
                            attachment.filename,
                            attachment.mime_type,
                            caption=attachment.filename,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to send Daisy attachment %s for message %s",
                            attachment.filename,
                            message.id,
                        )
                logger.info("Sent Daisy message %s in thread %s", message.id, message.thread_id)
                sent += 1

            state.mark_seen(scope, message.id)

        if fetch_all and index < len(threads_to_fetch) - 1:
            await asyncio.sleep(0.2)

    return sent


async def poll_wilma(
    config: dict,
    state: StateStore,
    http: aiohttp.ClientSession,
    notify: bool,
) -> int:
    grouped: dict[int, list[tuple[Student, any]]] = {}

    async with WilmaClient(config["wilma_url"], session=http) as client:
        await client.login(config["wilma_username"], config["wilma_password"])
        students = await discover_students(client)
        logger.info("Found %s Wilma student(s): %s", len(students), ", ".join(s.name for s in students))

        for student in students:
            client.user_id = student.user_id
            messages = await client.get_messages()
            for message in messages:
                if state.is_seen(student.number, message.id):
                    continue
                grouped.setdefault(message.id, []).append((student, message))

        sent = 0
        items = sorted(
            grouped.items(),
            key=lambda item: item[1][0][1].format_timestamp(),
        )
        for message_id, recipients in items:
            students_for_msg = [student for student, _ in recipients]
            if notify:
                client.user_id = students_for_msg[0].user_id
                full = await client.get_message_content(message_id)
                text = format_notification(students_for_msg, full)
                await send_telegram_message(
                    http,
                    config["telegram_token"],
                    config["telegram_chat_id"],
                    text,
                )
                await send_calendar_events(
                    http,
                    config,
                    full.subject,
                    message_body(full),
                    full.timestamp,
                    kid_names=[student.name for student in students_for_msg],
                )
                names = ", ".join(student.name for student in students_for_msg)
                logger.info("Sent message %s for %s", message_id, names)
                sent += 1

            for student, _ in recipients:
                state.mark_seen(student.number, message_id)

    return sent


async def poll_daisy(
    config: dict,
    state: StateStore,
    http: aiohttp.ClientSession,
    notify: bool,
) -> int:
    client = DaisyClient(config["daisy_url"], http)
    await client.login(config["daisy_username"], config["daisy_password"])
    threads = await client.get_message_list()
    logger.info("Found %s Daisy message thread(s)", len(threads))
    return await process_daisy(client, state, config, http, notify, threads)


def should_notify(source_initialized: bool, initial_sync: bool) -> bool:
    if initial_sync:
        return False
    return source_initialized


async def run_once(config: dict | None = None) -> int:
    config = config or load_config()
    state = StateStore(config["state_path"])
    wilma_notify = should_notify(state.wilma_initialized, config["initial_sync"])
    daisy_notify = should_notify(state.daisy_initialized, config["initial_sync"])

    if config["initial_sync"]:
        logger.info("Initial sync mode: existing messages will not be sent to Telegram")

    total_sent = 0

    async with aiohttp.ClientSession(
        trace_configs=[create_trace_config(config["wilma_url"], config["daisy_url"])],
    ) as http:
        try:
            total_sent += await poll_wilma(config, state, http, wilma_notify)
            state.wilma_initialized = True
        except Exception:
            logger.exception("Wilma poll failed")

        if daisy_enabled(config):
            try:
                if not state.daisy_initialized:
                    logger.info("Daisy initial sync: marking existing messages as seen")
                total_sent += await poll_daisy(config, state, http, daisy_notify)
                state.daisy_initialized = True
            except Exception:
                logger.exception("Daisy poll failed")

    state.save()

    if config["initial_sync"] or (not wilma_notify and not daisy_notify):
        logger.info("Marked existing messages as seen")
    else:
        logger.info("Sent %s new message(s) to Telegram", total_sent)

    return total_sent


async def run_loop(config: dict | None = None) -> None:
    config = config or load_config()
    interval = config["poll_interval"]

    while True:
        try:
            await run_once(config)
        except Exception:
            logger.exception("Poll failed")

        logger.info("Sleeping %s seconds", interval)
        await asyncio.sleep(interval)
