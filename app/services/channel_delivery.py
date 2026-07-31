from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import httpx

from app.models import models

CHANNEL_PROVIDER_ENUM = {"dingtalk", "feishu", "wechat", "slack", "telegram"}
CHANNEL_STATUS_ENUM = {"active", "inactive"}
_DINGTALK_SECURITY_MODE_ENUM = {"keyword", "sign", "ip"}
_DINGTALK_MESSAGE_TYPE_ENUM = {"text", "markdown", "actionCard", "feedCard"}
_TELEGRAM_PARSE_MODE_ENUM = {"Markdown", "HTML", ""}


@dataclass
class ChannelDeliveryError(Exception):
    code: str
    message: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


def normalize_channel_provider(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in CHANNEL_PROVIDER_ENUM:
        raise ChannelDeliveryError(
            code="invalid_payload",
            message=f"provider must be one of: {', '.join(sorted(CHANNEL_PROVIDER_ENUM))}",
            details={"provider": value},
        )
    return normalized


def normalize_channel_status(value: Any, *, default: str = "active") -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in CHANNEL_STATUS_ENUM:
        raise ChannelDeliveryError(
            code="invalid_payload",
            message=f"status must be one of: {', '.join(sorted(CHANNEL_STATUS_ENUM))}",
            details={"status": value},
        )
    return normalized


def normalize_channel_config(*, provider: str, config: dict[str, Any] | None) -> dict[str, Any]:
    raw = config if isinstance(config, dict) else {}
    if provider == "slack":
        return _normalize_slack_config(raw)
    if provider == "telegram":
        return _normalize_telegram_config(raw)
    if provider != "dingtalk":
        return dict(raw)

    webhook_url = str(raw.get("webhook_url") or "").strip()
    parsed = urlparse(webhook_url)
    if not webhook_url or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ChannelDeliveryError(
            code="invalid_payload",
            message="dingtalk channel requires valid config.webhook_url",
        )

    raw_security = raw.get("security")
    security = raw_security if isinstance(raw_security, dict) else {}
    security_mode = str(security.get("mode") or "sign").strip().lower()
    if security_mode not in _DINGTALK_SECURITY_MODE_ENUM:
        raise ChannelDeliveryError(
            code="invalid_payload",
            message=(
                "dingtalk security.mode must be one of: "
                + ", ".join(sorted(_DINGTALK_SECURITY_MODE_ENUM))
            ),
        )
    keyword = str(security.get("keyword") or "").strip() or None
    secret = str(security.get("secret") or "").strip() or None
    ip_whitelist = security.get("ip_whitelist")
    normalized_ip_whitelist = (
        [str(item).strip() for item in ip_whitelist if str(item).strip()]
        if isinstance(ip_whitelist, list)
        else []
    )
    if security_mode == "keyword" and not keyword:
        raise ChannelDeliveryError(
            code="invalid_payload",
            message="dingtalk keyword mode requires security.keyword",
        )
    if security_mode == "sign" and not secret:
        raise ChannelDeliveryError(
            code="invalid_payload",
            message="dingtalk sign mode requires security.secret",
        )

    raw_template = raw.get("template")
    template = raw_template if isinstance(raw_template, dict) else {}
    message_type = str(template.get("type") or "markdown").strip()
    if message_type not in _DINGTALK_MESSAGE_TYPE_ENUM:
        raise ChannelDeliveryError(
            code="invalid_payload",
            message=(
                "dingtalk template.type must be one of: "
                + ", ".join(sorted(_DINGTALK_MESSAGE_TYPE_ENUM))
            ),
        )
    title = str(template.get("title") or "Praxis Notification").strip()
    body = str(template.get("body") or "Praxis test message").strip()
    raw_at_user_ids = template.get("at_user_ids")
    at_user_ids = (
        [str(item).strip() for item in raw_at_user_ids if str(item).strip()]
        if isinstance(raw_at_user_ids, list)
        else []
    )
    links = template.get("links")
    normalized_links = links if isinstance(links, list) else []

    return {
        "webhook_url": webhook_url,
        "security": {
            "mode": security_mode,
            "keyword": keyword,
            "secret": secret,
            "ip_whitelist": normalized_ip_whitelist,
        },
        "template": {
            "type": message_type,
            "title": title,
            "body": body,
            "at_all": bool(template.get("at_all", False)),
            "at_user_ids": at_user_ids,
            "links": normalized_links,
        },
    }


def _normalize_slack_config(raw: dict[str, Any]) -> dict[str, Any]:
    webhook_url = str(raw.get("webhook_url") or "").strip()
    parsed = urlparse(webhook_url)
    if not webhook_url or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ChannelDeliveryError(
            code="invalid_payload",
            message="slack channel requires valid config.webhook_url",
        )

    raw_template = raw.get("template")
    template = raw_template if isinstance(raw_template, dict) else {}
    username = str(template.get("username") or "").strip() or None
    icon_emoji = str(template.get("icon_emoji") or "").strip() or None
    channel = str(template.get("channel") or "").strip() or None

    return {
        "webhook_url": webhook_url,
        "template": {
            "username": username,
            "icon_emoji": icon_emoji,
            "channel": channel,
        },
    }


def _normalize_telegram_config(raw: dict[str, Any]) -> dict[str, Any]:
    bot_token = str(raw.get("bot_token") or "").strip()
    if not bot_token:
        raise ChannelDeliveryError(
            code="invalid_payload",
            message="telegram channel requires config.bot_token",
        )
    chat_id = str(raw.get("chat_id") or "").strip()
    if not chat_id:
        raise ChannelDeliveryError(
            code="invalid_payload",
            message="telegram channel requires config.chat_id",
        )

    raw_template = raw.get("template")
    template = raw_template if isinstance(raw_template, dict) else {}
    parse_mode = str(template.get("parse_mode") or "Markdown").strip()
    if parse_mode not in _TELEGRAM_PARSE_MODE_ENUM:
        raise ChannelDeliveryError(
            code="invalid_payload",
            message=f"telegram template.parse_mode must be one of: {', '.join(sorted(p for p in _TELEGRAM_PARSE_MODE_ENUM if p))}",
        )
    disable_notification = bool(template.get("disable_notification", False))

    return {
        "bot_token": bot_token,
        "chat_id": chat_id,
        "template": {
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        },
    }


def build_slack_message(
    template: dict[str, Any], *, title: str | None = None, body: str | None = None
) -> dict[str, Any]:
    text = body or str(template.get("body") or "Praxis test message")
    msg_title = title or str(template.get("title") or "")
    if msg_title:
        text = f"*{msg_title}*\n{text}"
    payload: dict[str, Any] = {"text": text}
    username = template.get("username")
    if username:
        payload["username"] = str(username)
    icon_emoji = template.get("icon_emoji")
    if icon_emoji:
        payload["icon_emoji"] = str(icon_emoji)
    channel = template.get("channel")
    if channel:
        payload["channel"] = str(channel)
    return payload


def build_telegram_message(
    template: dict[str, Any],
    *,
    chat_id: str,
    title: str | None = None,
    body: str | None = None,
) -> dict[str, Any]:
    text = body or str(template.get("body") or "Praxis test message")
    msg_title = title or str(template.get("title") or "")
    parse_mode = str(template.get("parse_mode") or "Markdown")
    if msg_title:
        if parse_mode == "Markdown":
            text = f"*{msg_title}*\n{text}"
        elif parse_mode == "HTML":
            text = f"<b>{msg_title}</b>\n{text}"
        else:
            text = f"{msg_title}\n{text}"
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if template.get("disable_notification"):
        payload["disable_notification"] = True
    return payload


def build_dingtalk_message(template: dict[str, Any]) -> dict[str, Any]:
    message_type = str(template.get("type") or "markdown")
    title = str(template.get("title") or "Praxis Notification")
    body = str(template.get("body") or "Praxis test message")
    at_payload = {
        "atUserIds": template.get("at_user_ids")
        if isinstance(template.get("at_user_ids"), list)
        else [],
        "isAtAll": bool(template.get("at_all", False)),
    }

    if message_type == "text":
        return {"msgtype": "text", "text": {"content": body}, "at": at_payload}
    if message_type == "markdown":
        return {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": body},
            "at": at_payload,
        }
    if message_type == "actionCard":
        return {
            "msgtype": "actionCard",
            "actionCard": {
                "title": title,
                "text": body,
                "btnOrientation": "0",
                "singleTitle": "View Details",
                "singleURL": "https://www.dingtalk.com/",
            },
        }
    links = template.get("links") if isinstance(template.get("links"), list) else []
    normalized_links = [
        {
            "title": str(item.get("title") or title),
            "messageURL": str(item.get("messageURL") or "https://www.dingtalk.com/"),
            "picURL": str(item.get("picURL") or ""),
        }
        for item in links
        if isinstance(item, dict)
    ]
    if not normalized_links:
        normalized_links = [
            {
                "title": title,
                "messageURL": "https://www.dingtalk.com/",
                "picURL": "",
            }
        ]
    return {"msgtype": "feedCard", "feedCard": {"links": normalized_links}}


def _collect_message_text(payload: Any) -> str:
    if isinstance(payload, dict):
        return " ".join(_collect_message_text(value) for value in payload.values())
    if isinstance(payload, list):
        return " ".join(_collect_message_text(item) for item in payload)
    if isinstance(payload, str):
        return payload
    return ""


def _build_dingtalk_signed_url(webhook_url: str, secret: str) -> tuple[str, int]:
    timestamp = int(time.time() * 1000)
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).digest()
    sign = quote(base64.b64encode(digest).decode("utf-8"), safe="")
    parsed = urlparse(webhook_url)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    query_items.append(("timestamp", str(timestamp)))
    query_items.append(("sign", sign))
    next_query = urlencode(query_items)
    return urlunparse(parsed._replace(query=next_query)), timestamp


def mask_webhook_url(webhook_url: str) -> str:
    parsed = urlparse(str(webhook_url or ""))
    if not parsed.scheme or not parsed.netloc:
        return ""
    sanitized: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in {"access_token", "sign"}:
            masked = value[:3] + "***" + value[-3:] if len(value) > 6 else "***"
            sanitized.append((key, masked))
            continue
        sanitized.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(sanitized)))


class ChannelDeliveryService:
    async def send(
        self,
        *,
        channel: models.Channel,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        provider = normalize_channel_provider(channel.provider)
        if normalize_channel_status(channel.status) != "active":
            raise ChannelDeliveryError(code="invalid_state", message="channel is inactive")
        config = normalize_channel_config(provider=provider, config=channel.config)
        if provider == "dingtalk":
            return await self._send_dingtalk(channel=channel, config=config, payload=payload or {})
        if provider == "slack":
            return await self._send_slack(channel=channel, config=config, payload=payload or {})
        if provider == "telegram":
            return await self._send_telegram(channel=channel, config=config, payload=payload or {})
        raise ChannelDeliveryError(
            code="not_supported",
            message=f"provider '{provider}' send is not implemented yet",
        )

    async def _send_dingtalk(
        self,
        *,
        channel: models.Channel,
        config: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        raw_message = payload.get("message")
        message: dict[str, Any]
        if isinstance(raw_message, dict) and raw_message:
            message = raw_message
        else:
            template = dict(config.get("template") or {})
            override = payload.get("template")
            if isinstance(override, dict):
                template.update(override)
            if "title" in payload:
                template["title"] = str(payload.get("title") or "")
            if "content" in payload:
                template["body"] = str(payload.get("content") or "")
            if "message_type" in payload:
                template["type"] = str(payload.get("message_type") or "")
            message = build_dingtalk_message(template)

        security = config.get("security") if isinstance(config.get("security"), dict) else {}
        mode = str(security.get("mode") or "sign")
        keyword = str(security.get("keyword") or "").strip()
        secret = str(security.get("secret") or "").strip()
        if mode == "keyword" and keyword and keyword not in _collect_message_text(message):
            raise ChannelDeliveryError(
                code="invalid_payload",
                message="dingtalk keyword mode requires keyword in message content",
                details={"keyword": keyword},
            )

        webhook_url = str(config.get("webhook_url") or "")
        timestamp: int | None = None
        if mode == "sign":
            webhook_url, timestamp = _build_dingtalk_signed_url(webhook_url, secret)

        dry_run = bool(payload.get("dry_run"))
        if dry_run:
            return {
                "provider": "dingtalk",
                "channel_id": channel.id,
                "dry_run": True,
                "request": {
                    "webhook_url": mask_webhook_url(webhook_url),
                    "message": message,
                    "timestamp": timestamp,
                },
            }

        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            response = await client.post(webhook_url, json=message)
        data: dict[str, Any]
        try:
            parsed = response.json()
            data = parsed if isinstance(parsed, dict) else {"raw": parsed}
        except ValueError:
            data = {"raw": response.text}

        errcode = data.get("errcode")
        if response.status_code >= 400:
            raise ChannelDeliveryError(
                code="provider_http_error",
                message=f"dingtalk webhook returned HTTP {response.status_code}",
                details={"status_code": response.status_code, "response": data},
            )
        if errcode not in {0, "0", None}:
            raise ChannelDeliveryError(
                code="provider_error",
                message=f"dingtalk webhook error: {data.get('errmsg') or data}",
                details={"response": data},
            )

        return {
            "provider": "dingtalk",
            "channel_id": channel.id,
            "dry_run": False,
            "request": {
                "webhook_url": mask_webhook_url(webhook_url),
                "message": message,
                "timestamp": timestamp,
            },
            "response": {
                "status_code": response.status_code,
                "body": data,
            },
        }

    async def _send_slack(
        self,
        *,
        channel: models.Channel,
        config: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        template = dict(config.get("template") or {})
        override = payload.get("template")
        if isinstance(override, dict):
            template.update(override)
        title = str(payload.get("title") or "").strip() or None
        body = str(payload.get("content") or "").strip() or None
        message = build_slack_message(template, title=title, body=body)

        webhook_url = str(config.get("webhook_url") or "")
        dry_run = bool(payload.get("dry_run"))
        if dry_run:
            return {
                "provider": "slack",
                "channel_id": channel.id,
                "dry_run": True,
                "request": {"webhook_url": mask_webhook_url(webhook_url), "message": message},
            }

        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            response = await client.post(webhook_url, json=message)

        if response.status_code != 200 or response.text != "ok":
            raise ChannelDeliveryError(
                code="provider_http_error",
                message=f"slack webhook returned HTTP {response.status_code}: {response.text[:200]}",
                details={"status_code": response.status_code, "response": response.text[:500]},
            )

        return {
            "provider": "slack",
            "channel_id": channel.id,
            "dry_run": False,
            "request": {"webhook_url": mask_webhook_url(webhook_url), "message": message},
            "response": {"status_code": response.status_code, "body": "ok"},
        }

    async def _send_telegram(
        self,
        *,
        channel: models.Channel,
        config: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        bot_token = str(config.get("bot_token") or "")
        chat_id = str(config.get("chat_id") or "")
        template = dict(config.get("template") or {})
        override = payload.get("template")
        if isinstance(override, dict):
            template.update(override)
        title = str(payload.get("title") or "").strip() or None
        body = str(payload.get("content") or "").strip() or None
        message = build_telegram_message(template, chat_id=chat_id, title=title, body=body)

        api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        dry_run = bool(payload.get("dry_run"))
        if dry_run:
            masked_token = bot_token[:4] + "***" + bot_token[-4:] if len(bot_token) > 8 else "***"
            return {
                "provider": "telegram",
                "channel_id": channel.id,
                "dry_run": True,
                "request": {
                    "api_url": f"https://api.telegram.org/bot{masked_token}/sendMessage",
                    "message": message,
                },
            }

        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            response = await client.post(api_url, json=message)
        data: dict[str, Any]
        try:
            parsed = response.json()
            data = parsed if isinstance(parsed, dict) else {"raw": parsed}
        except ValueError:
            data = {"raw": response.text}

        if response.status_code >= 400 or not data.get("ok"):
            raise ChannelDeliveryError(
                code="provider_error",
                message=f"telegram API error: {data.get('description') or data}",
                details={"status_code": response.status_code, "response": data},
            )

        return {
            "provider": "telegram",
            "channel_id": channel.id,
            "dry_run": False,
            "request": {"chat_id": chat_id, "message": message},
            "response": {"status_code": response.status_code, "body": data},
        }
