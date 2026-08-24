import logging
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)


def create_trace_config(*base_urls: str) -> aiohttp.TraceConfig:
    hosts = {urlparse(url).netloc for url in base_urls if url}

    async def on_request_start(_session, _ctx, params):
        if params.url.host in hosts:
            logger.info("HTTP -> %s %s", params.method, params.url)

    async def on_request_end(_session, _ctx, params):
        if params.response.url.host in hosts:
            logger.info("HTTP <- %s %s", params.response.status, params.response.url)

    trace = aiohttp.TraceConfig()
    trace.on_request_start.append(on_request_start)
    trace.on_request_end.append(on_request_end)
    return trace
