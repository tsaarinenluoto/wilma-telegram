import logging
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

import aiohttp

logger = logging.getLogger(__name__)

NS = {
    "d": "DAV:",
    "c": "urn:ietf:params:xml:ns:caldav",
}
_CALDAV_ROOT = "https://caldav.icloud.com/"
_cached_calendar_url: str | None = None


def _auth(apple_id: str, password: str) -> aiohttp.BasicAuth:
    return aiohttp.BasicAuth(apple_id, password)


def _href(el: ET.Element, path: str) -> str:
    node = el.find(path, NS)
    if node is None:
        return ""
    href = node.find("d:href", NS)
    if href is None or not href.text:
        return ""
    return href.text.strip()


async def _propfind(
    session: aiohttp.ClientSession,
    url: str,
    body: str,
    auth: aiohttp.BasicAuth,
    depth: str,
) -> tuple[str, ET.Element]:
    headers = {
        "Depth": depth,
        "Content-Type": "application/xml; charset=utf-8",
    }
    async with session.request(
        "PROPFIND", url, data=body.encode("utf-8"), headers=headers, auth=auth
    ) as response:
        text = await response.text()
        if response.status not in {207, 200}:
            raise RuntimeError(f"CalDAV PROPFIND {url} failed {response.status}: {text[:300]}")
        return str(response.url), ET.fromstring(text)


async def _discover_calendar(
    session: aiohttp.ClientSession,
    apple_id: str,
    password: str,
    calendar_name: str,
) -> str:
    auth = _auth(apple_id, password)
    principal_body = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop><d:current-user-principal/></d:prop>
</d:propfind>"""
    base, tree = await _propfind(session, _CALDAV_ROOT, principal_body, auth, "0")
    principal = _href(tree, ".//d:current-user-principal")
    if not principal:
        raise RuntimeError("iCloud CalDAV: no current-user-principal")
    principal_url = urljoin(base, principal)

    home_body = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><c:calendar-home-set/></d:prop>
</d:propfind>"""
    _, tree = await _propfind(session, principal_url, home_body, auth, "0")
    home = _href(tree, ".//c:calendar-home-set")
    if not home:
        raise RuntimeError("iCloud CalDAV: no calendar-home-set")
    home_url = urljoin(principal_url, home)

    list_body = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:displayname/>
    <d:resourcetype/>
    <c:supported-calendar-component-set/>
  </d:prop>
</d:propfind>"""
    _, tree = await _propfind(session, home_url, list_body, auth, "1")
    wanted = calendar_name.strip().casefold()
    fallback = ""
    for response in tree.findall("d:response", NS):
        href_el = response.find("d:href", NS)
        if href_el is None or not href_el.text:
            continue
        if response.find(".//c:calendar", NS) is None:
            continue
        comps = [
            (c.get("name") or "").upper()
            for c in response.findall(".//c:comp", NS)
        ]
        if comps and "VEVENT" not in comps:
            continue
        url = urljoin(home_url, href_el.text.strip())
        if url.rstrip("/") == home_url.rstrip("/"):
            continue
        name_el = response.find(".//d:displayname", NS)
        name = (name_el.text or "").strip() if name_el is not None else ""
        if wanted and name.casefold() == wanted:
            return url if url.endswith("/") else url + "/"
        if not fallback:
            fallback = url if url.endswith("/") else url + "/"
    if fallback and not wanted:
        return fallback
    if fallback and wanted:
        raise RuntimeError(f"iCloud calendar {calendar_name!r} not found")
    raise RuntimeError("iCloud CalDAV: no event calendar found")


async def add_vevent(
    session: aiohttp.ClientSession,
    apple_id: str,
    password: str,
    calendar_name: str,
    ics: bytes,
) -> None:
    global _cached_calendar_url
    if not _cached_calendar_url:
        _cached_calendar_url = await _discover_calendar(
            session, apple_id, password, calendar_name
        )
        logger.info("Using iCloud calendar %s", _cached_calendar_url)

    uid = ""
    for line in ics.decode("utf-8").splitlines():
        if line.startswith("UID:"):
            uid = line[4:].strip()
            break
    if not uid:
        raise RuntimeError("ICS has no UID")

    url = urljoin(_cached_calendar_url, f"{uid}.ics")
    headers = {
        "Content-Type": "text/calendar; charset=utf-8",
        "If-None-Match": "*",
    }
    async with session.put(
        url, data=ics, headers=headers, auth=_auth(apple_id, password)
    ) as response:
        if response.status in {200, 201, 204}:
            return
        body = await response.text()
        if response.status in {412, 405}:
            logger.info("iCloud event %s already exists", uid)
            return
        raise RuntimeError(f"iCloud PUT failed {response.status}: {body[:300]}")
