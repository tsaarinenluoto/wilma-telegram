import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp

from wilma_telegram.icloud_caldav import add_vevent
from wilma_telegram.telegram import send_telegram_file, send_telegram_message

logger = logging.getLogger(__name__)

XAI_URL = "https://api.x.ai/v1/chat/completions"
MAX_EVENTS = 5
TIMEZONE = "Europe/Helsinki"
HELSINKI = ZoneInfo(TIMEZONE)
VTIMEZONE = [
    "BEGIN:VTIMEZONE",
    f"TZID:{TIMEZONE}",
    f"X-LIC-LOCATION:{TIMEZONE}",
    "BEGIN:DAYLIGHT",
    "TZOFFSETFROM:+0200",
    "TZOFFSETTO:+0300",
    "TZNAME:EEST",
    "DTSTART:19700329T030000",
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU",
    "END:DAYLIGHT",
    "BEGIN:STANDARD",
    "TZOFFSETFROM:+0300",
    "TZOFFSETTO:+0200",
    "TZNAME:EET",
    "DTSTART:19701025T040000",
    "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
    "END:STANDARD",
    "END:VTIMEZONE",
]

EVENT_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "calendar_events",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "maxItems": MAX_EVENTS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "date": {"type": "string"},
                            "start_time": {"type": "string"},
                            "end_time": {"type": "string"},
                            "location": {"type": "string"},
                            "description": {"type": "string"},
                            "all_day": {"type": "boolean"},
                        },
                        "required": [
                            "title",
                            "date",
                            "start_time",
                            "end_time",
                            "location",
                            "description",
                            "all_day",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["events"],
            "additionalProperties": False,
        },
    },
}

SYSTEM_PROMPT = """Extract calendar events from Finnish school or daycare messages.
Return at most 5 events. Use timezone Europe/Helsinki.
Only include events with a concrete date. Skip vague timing like "syksyllä" or "myöhemmin".
Use the message timestamp for year context when the year is missing.
If start_time is missing, set all_day to true.
If end_time is missing for a timed event, leave it empty.
If there are no calendar events, return an empty events list.
Dates must be YYYY-MM-DD. Times must be HH:MM or empty strings.
description must be a short Finnish summary of the event in 1-2 sentences, using only facts from the message. Leave it empty if the title already says everything."""


@dataclass
class CalendarEvent:
    title: str
    date: str
    start_time: str = ""
    end_time: str = ""
    location: str = ""
    description: str = ""
    all_day: bool = False


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    if len(line) <= 75:
        return line
    chunks = [line[:75]]
    rest = line[75:]
    while rest:
        chunks.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(chunks)


def _parse_hm(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def event_to_ics(event: CalendarEvent) -> bytes:
    uid = f"{uuid.uuid4()}@wilma-telegram"
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary = _ics_escape(event.title)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//wilma-telegram//EN",
        "CALSCALE:GREGORIAN",
        *VTIMEZONE,
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now}",
        "STATUS:CONFIRMED",
        "SEQUENCE:0",
        f"SUMMARY:{summary}",
    ]
    if event.description:
        lines.append(f"DESCRIPTION:{_ics_escape(event.description)}")
    if event.location:
        lines.append(f"LOCATION:{_ics_escape(event.location)}")

    date = datetime.strptime(event.date, "%Y-%m-%d")
    start = _parse_hm(event.start_time) if event.start_time else None
    end = _parse_hm(event.end_time) if event.end_time else None

    if event.all_day or start is None:
        start_date = date.strftime("%Y%m%d")
        end_date = (date + timedelta(days=1)).strftime("%Y%m%d")
        lines.append(f"DTSTART;VALUE=DATE:{start_date}")
        lines.append(f"DTEND;VALUE=DATE:{end_date}")
    else:
        start_dt = datetime(
            date.year, date.month, date.day, start[0], start[1], tzinfo=HELSINKI
        )
        if end:
            end_dt = datetime(
                date.year, date.month, date.day, end[0], end[1], tzinfo=HELSINKI
            )
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
        else:
            end_dt = start_dt + timedelta(hours=1)
        lines.append(
            f"DTSTART;TZID={TIMEZONE}:{start_dt.strftime('%Y%m%dT%H%M%S')}"
        )
        lines.append(f"DTEND;TZID={TIMEZONE}:{end_dt.strftime('%Y%m%dT%H%M%S')}")

    lines.extend(["END:VEVENT", "END:VCALENDAR", ""])
    return "\r\n".join(_fold(line) for line in lines).encode("utf-8")


def automaatti_prefix(kid_names: list[str] | None = None) -> str:
    first_names: list[str] = []
    for name in kid_names or []:
        first = name.strip().split()[0] if name.strip() else ""
        if first and first not in first_names:
            first_names.append(first)
    if len(first_names) == 1:
        return f"Automaatti ({first_names[0]}): "
    return "Automaatti: "


def ics_filename(event: CalendarEvent) -> str:
    slug = event.title.lower()
    for src, dst in (("ä", "a"), ("ö", "o"), ("å", "a")):
        slug = slug.replace(src, dst)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")[:40] or "event"
    return f"{slug}.ics"


def _event_summary(event: CalendarEvent) -> str:
    when = event.date
    if event.all_day or not event.start_time:
        when += " (all day)"
    elif event.end_time:
        when += f" {event.start_time}–{event.end_time}"
    else:
        when += f" {event.start_time}"
    if event.location:
        when += f" @ {event.location}"
    return f"{event.title} ({when})"


def parse_events(data: dict) -> list[CalendarEvent]:
    events = []
    raw_events = data.get("events") or []
    logger.info("Grok returned %s calendar event candidate(s)", len(raw_events))
    for item in raw_events:
        title = str(item.get("title") or "").strip()
        date = str(item.get("date") or "").strip()
        if not title or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            logger.info("Skipping invalid calendar event: title=%r date=%r", title, date)
            continue
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            logger.info("Skipping calendar event with bad date: title=%r date=%r", title, date)
            continue
        events.append(
            CalendarEvent(
                title=title,
                date=date,
                start_time=str(item.get("start_time") or "").strip(),
                end_time=str(item.get("end_time") or "").strip(),
                location=str(item.get("location") or "").strip(),
                description=str(item.get("description") or "").strip(),
                all_day=bool(item.get("all_day")),
            )
        )
        if len(events) >= MAX_EVENTS:
            logger.info("Reached max of %s calendar events", MAX_EVENTS)
            break
    return events


async def extract_events(
    session: aiohttp.ClientSession,
    api_key: str,
    model: str,
    subject: str,
    body: str,
    timestamp: str,
) -> list[CalendarEvent]:
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Message timestamp: {timestamp}\n"
                    f"Subject: {subject}\n\n"
                    f"{body}"
                ),
            },
        ],
        "response_format": EVENT_SCHEMA,
    }
    logger.info(
        "Asking Grok (%s) for calendar events in %r (%s chars)",
        model,
        subject,
        len(body),
    )
    async with session.post(
        XAI_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    ) as response:
        raw = await response.text()
        if response.status != 200:
            raise RuntimeError(f"xAI API error {response.status}: {raw[:300]}")
        data = json.loads(raw)

    usage = data.get("usage") or {}
    logger.info(
        "Grok calendar analysis finished for %r (prompt=%s completion=%s tokens)",
        subject,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
    )

    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    logger.info("Grok calendar response: %s", content[:1000])
    parsed = json.loads(content)
    return parse_events(parsed)


async def send_calendar_events(
    session: aiohttp.ClientSession,
    config: dict,
    subject: str,
    body: str,
    timestamp: str,
    kid_names: list[str] | None = None,
) -> None:
    api_key = config.get("xai_api_key") or ""
    if not api_key:
        logger.info("Skipping calendar analysis for %r: XAI_API_KEY is not set", subject)
        return

    logger.info("Starting calendar analysis for %r (timestamp %s)", subject, timestamp)
    try:
        events = await extract_events(
            session,
            api_key,
            config.get("xai_model") or "grok-4.6",
            subject,
            body,
            timestamp,
        )
    except Exception:
        logger.exception("Calendar event extraction failed for %r", subject)
        return

    if not events:
        logger.info("No calendar events in %r", subject)
        return

    logger.info("Found %s calendar event(s) in %r", len(events), subject)
    prefix = automaatti_prefix(kid_names)
    for event in events:
        event.title = f"{prefix}{event.title}"
        logger.info("Calendar event: %s", _event_summary(event))
        filename = ics_filename(event)
        ics = event_to_ics(event)
        try:
            await send_telegram_file(
                session,
                config["telegram_token"],
                config["telegram_chat_id"],
                ics,
                filename,
                "application/octet-stream",
                caption=event.title,
                disable_content_type_detection=True,
            )
            logger.info("Sent calendar attachment %s", filename)
        except Exception:
            logger.exception("Failed to send calendar event %s", event.title)
            continue

        if not config.get("icloud_apple_id") or not config.get("icloud_app_password"):
            continue
        try:
            await add_vevent(
                session,
                config["icloud_apple_id"],
                config["icloud_app_password"],
                config.get("icloud_calendar") or "",
                ics,
            )
        except Exception:
            logger.exception("Could not add %s to iCloud calendar", event.title)
            continue
        try:
            await send_telegram_message(
                session,
                config["telegram_token"],
                config["telegram_chat_id"],
                f"Lisätty Apple-kalenteriin: {event.title}\n{_event_summary(event)}",
            )
            logger.info("Added %s to iCloud calendar", event.title)
        except Exception:
            logger.exception("Failed to send iCloud calendar confirmation for %s", event.title)
