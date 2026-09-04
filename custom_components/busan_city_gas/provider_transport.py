"""Bounded JSON transport and strict optional fields for provider adapters."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date

import aiohttp

from .model import GasError, decimal
from .portal import AuthenticationError, ConnectionError


def text(row, *keys):
    if not isinstance(row, dict):
        raise GasError("provider_schema_changed")
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() not in ("", "null"):
            if isinstance(value, (dict, list, bool)):
                raise GasError("provider_schema_changed")
            return str(value).strip()
    return None


def required(row, key):
    value = text(row, key)
    if value is None:
        raise GasError("provider_schema_changed")
    return value


def number(row, *keys):
    value = text(row, *keys)
    if value is None:
        return None
    if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", value):
        raise GasError("provider_number_invalid")
    result = decimal(value)
    if result > 99999999:
        raise GasError("provider_number_invalid")
    return str(result)


def day(row, *keys):
    value = text(row, *keys)
    if value is None:
        return None
    try:
        if re.fullmatch(r"\d{8}", value):
            value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        raise GasError("provider_date_invalid") from None


def month(value):
    value = value.replace("-", "")
    if not re.fullmatch(r"\d{6}", value):
        raise GasError("provider_date_invalid")
    day({"date": value + "01"}, "date")
    return value


def flag(value):
    if value in (None, "", False, "N", "false", "False", "0", 0):
        return False
    if value in (True, "Y", "true", "True", "1", 1, "X"):
        return True
    raise GasError("provider_schema_changed")


def unwrap(value):
    if isinstance(value, dict):
        if value.get("success") is False or value.get("result") is False:
            raise GasError("provider_request_rejected")
        return value.get("data", value)
    return value


def rows(value, key):
    value = unwrap(value)
    if value is None:
        return []
    if isinstance(value, dict):
        value = value.get(key, [value])
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise GasError("provider_schema_changed")
    return value


async def request(session, method, url, *, headers=None, params=None, body=None, stage="query"):
    """No redirects or application retries; errors contain only stage/status."""
    try:
        async with session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=body,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if response.status in (401, 403, 418):
                raise AuthenticationError("reauth_required")
            if not 200 <= response.status < 300:
                raise GasError(f"provider_{stage}_http_{response.status}")
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > 4_000_000:
                    raise GasError("provider_response_too_large")
            try:
                return json.loads(data) if data else None
            except (ValueError, UnicodeError):
                raise GasError(f"provider_{stage}_schema_changed") from None
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise ConnectionError(f"provider_{stage}_connection_failed") from None
