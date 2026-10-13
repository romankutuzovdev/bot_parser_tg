#!/usr/bin/env python3
"""Парсер Telegram: ищет сообщения по ключевым словам и пересылает/сохраняет."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Чтобы работало с Windows embeddable Python (игнорирует PYTHONPATH)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message
from telethon.tl.types import Channel, Chat, User

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("parser")

# Уже обработанные (chat_id, msg_id) — чтобы не дублировать при истории + live
_seen: set[tuple[int, int]] = set()
_resolved_target_chats: list[str] = []


def keyword_patterns(keyword: str) -> list[str]:
    """Базовое слово + основы/падежные формы для русского поиска."""
    kw = keyword.lower().strip()
    forms = [kw]
    if kw.endswith("ция"):
        # сертификация/декларация → сертификации, сертификацию, сертификацией...
        stem = kw[:-2]  # ...ац
        forms.extend(
            [
                stem,
                stem + "ии",
                stem + "ию",
                stem + "ией",
                stem + "иею",
                stem + "ий",
                stem + "иям",
                stem + "иями",
                stem + "иях",
            ]
        )
    elif kw.endswith(("ат", "ет", "ит", "от", "ут")):
        # сертификат → сертификата, сертификату, сертификатом...
        forms.extend(
            [
                kw + "а",
                kw + "у",
                kw + "ом",
                kw + "е",
                kw + "ы",
                kw + "ов",
                kw + "ам",
                kw + "ами",
                kw + "ах",
            ]
        )
    # уникальные, длинные раньше — чтобы ловить точнее
    return sorted(dict.fromkeys(forms), key=len, reverse=True)


_KEYWORD_FORMS = [(kw, keyword_patterns(kw)) for kw in config.KEYWORDS]


def matches_keywords(text: str | None) -> list[str]:
    if not text:
        return []
    lowered = text.lower()
    found: list[str] = []
    for base, forms in _KEYWORD_FORMS:
        if any(form in lowered for form in forms):
            found.append(base)
    return found


def chat_title(entity) -> str:
    if isinstance(entity, User):
        parts = [entity.first_name or "", entity.last_name or ""]
        name = " ".join(p for p in parts if p).strip()
        return name or (f"@{entity.username}" if entity.username else str(entity.id))
    if isinstance(entity, (Channel, Chat)):
        return entity.title or str(entity.id)
    return str(getattr(entity, "id", entity))


def message_link(entity, msg_id: int) -> str | None:
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    # Приватные супергруппы/каналы: t.me/c/<id без -100>/<msg_id>
    chat_id = getattr(entity, "id", None)
    if chat_id is None:
        return None
    raw = str(chat_id)
    if raw.startswith("-100"):
        raw = raw[4:]
    return f"https://t.me/c/{raw}/{msg_id}"


async def save_post(payload: dict) -> None:
    if not config.SAVE_TO_FILE:
        return
    with config.OUTPUT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _bot_api(method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}"
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"ok": False, "description": body or str(e)}


def _target_chat_candidates(raw: str) -> list[str]:
    raw = raw.strip()
    candidates = [raw]
    if raw.lstrip("-").isdigit():
        num = int(raw)
        abs_num = abs(num)
        for value in (str(abs_num), f"-{abs_num}", f"-100{abs_num}"):
            if value not in candidates:
                candidates.append(value)
    return candidates


async def resolve_bot_targets() -> list[str]:
    global _resolved_target_chats
    _resolved_target_chats = []
    if not config.BOT_TOKEN or not config.TARGET_CHATS:
        return []

    me = await asyncio.to_thread(_bot_api, "getMe", {})
    if not me.get("ok"):
        raise SystemExit(f"Бот не отвечает: {me.get('description')}")
    bot_username = me["result"].get("username")
    log.info("Бот: @%s", bot_username)

    failed: list[str] = []
    for raw in config.TARGET_CHATS:
        last_error = "unknown"
        resolved = None
        for chat_id in _target_chat_candidates(raw):
            result = await asyncio.to_thread(_bot_api, "getChat", {"chat_id": chat_id})
            if result.get("ok"):
                title = result["result"].get("title") or result["result"].get("username") or chat_id
                resolved = chat_id
                log.info("Цель бота: %s (chat_id=%s)", title, chat_id)
                break
            last_error = result.get("description", last_error)
            log.warning("chat_id %s недоступен боту: %s", chat_id, last_error)
        if resolved:
            _resolved_target_chats.append(resolved)
        else:
            failed.append(f"{raw}: {last_error}")

    if not _resolved_target_chats:
        raise SystemExit(
            "Бот не видит ни один TARGET_CHAT. Добавь бота в чаты и дай право писать. "
            + "; ".join(failed)
        )
    if failed:
        log.error("Часть целей недоступна: %s", "; ".join(failed))
    return _resolved_target_chats


async def send_to_target(client: TelegramClient, message: Message, matched: list[str], source_title: str) -> None:
    if not config.BOT_TOKEN or not _resolved_target_chats:
        return

    link = message_link(await message.get_chat(), message.id)
    body = message.text or message.message or "(медиа без текста)"
    text = (
        f"🔎 Найдено по: {', '.join(matched)}\n"
        f"📍 Чат: {source_title}\n"
        f"🔗 {link or 'ссылка недоступна'}\n"
        f"———\n"
        f"{body}"
    )
    # Telegram limit ~4096
    if len(text) > 4000:
        text = text[:3990] + "…"

    for chat_id in _resolved_target_chats:
        result = await asyncio.to_thread(
            _bot_api,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            },
        )
        if not result.get("ok"):
            log.error("Бот не отправил в %s: %s", chat_id, result.get("description"))
        else:
            log.info("Отправлено ботом в %s", chat_id)


async def handle_message(client: TelegramClient, message: Message, source_entity=None) -> None:
    if not message or not message.id:
        return

    chat = source_entity or await message.get_chat()
    chat_id = getattr(chat, "id", 0)
    key = (chat_id, message.id)
    if key in _seen:
        return

    text = message.text or message.message
    matched = matches_keywords(text)
    if not matched:
        return

    _seen.add(key)
    title = chat_title(chat)
    link = message_link(chat, message.id)
    date = message.date
    if date and date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)

    payload = {
        "chat_id": chat_id,
        "chat_title": title,
        "message_id": message.id,
        "date": date.isoformat() if date else None,
        "keywords": matched,
        "text": text,
        "link": link,
        "found_at": datetime.now(timezone.utc).isoformat(),
    }

    log.info("HIT [%s] %s — %s", ", ".join(matched), title, (text or "")[:80])
    await save_post(payload)
    await send_to_target(client, message, matched, title)


async def resolve_sources(client: TelegramClient) -> list:
    from telethon import utils as tg_utils
    from telethon.tl.functions.channels import GetFullChannelRequest, JoinChannelRequest
    from telethon.tl.types import Channel

    if not config.SOURCE_CHATS:
        dialogs = []
        async for d in client.iter_dialogs():
            dialogs.append(d.entity)
        log.info("SOURCE_CHATS пуст — мониторим все диалоги (%d)", len(dialogs))
        return dialogs

    entities = []
    seen_ids: set[int] = set()

    async def add_entity(entity, label: str) -> None:
        pid = tg_utils.get_peer_id(entity)
        if pid in seen_ids:
            return
        seen_ids.add(pid)
        entities.append(entity)
        log.info("Источник (%s): %s [peer=%s]", label, chat_title(entity), pid)

    for ref in config.SOURCE_CHATS:
        try:
            entity = await client.get_entity(ref)
            if isinstance(entity, Channel):
                try:
                    await client(JoinChannelRequest(entity))
                    log.info("Подписка/вход: %s", chat_title(entity))
                except Exception as e:
                    log.warning("Join %s: %s", ref, e)
            await add_entity(entity, ref)

            # Если это канал с группой обсуждения — слушаем и её (туда пишут люди)
            if isinstance(entity, Channel):
                try:
                    full = await client(GetFullChannelRequest(entity))
                    linked_id = getattr(full.full_chat, "linked_chat_id", None)
                    if linked_id:
                        linked = await client.get_entity(linked_id)
                        await add_entity(linked, f"discussion:{ref}")
                except Exception as e:
                    log.warning("linked chat для %s: %s", ref, e)
        except Exception as e:
            log.error("Не удалось открыть %s: %s", ref, e)
    return entities


async def poll_new_messages(client: TelegramClient, sources: list) -> None:
    """Надёжный polling: events иногда не приходят на user-session."""
    from telethon import utils as tg_utils

    last_ids: dict[int, int] = {}
    for entity in sources:
        pid = tg_utils.get_peer_id(entity)
        try:
            async for msg in client.iter_messages(entity, limit=1):
                last_ids[pid] = msg.id
                break
            else:
                last_ids[pid] = 0
        except Exception as e:
            log.warning("poll init %s: %s", chat_title(entity), e)
            last_ids[pid] = 0

    log.info("Polling каждые 5 сек по %d чатам", len(sources))
    while True:
        for entity in sources:
            pid = tg_utils.get_peer_id(entity)
            min_id = last_ids.get(pid, 0)
            try:
                new_msgs = []
                async for msg in client.iter_messages(entity, min_id=min_id, reverse=True):
                    if msg.id <= min_id:
                        continue
                    new_msgs.append(msg)
                for msg in new_msgs:
                    last_ids[pid] = max(last_ids.get(pid, 0), msg.id)
                    text = msg.text or msg.message or ""
                    log.info(
                        "POLL %s | id=%s | %s",
                        chat_title(entity),
                        msg.id,
                        (text or "(без текста)")[:120],
                    )
                    await handle_message(client, msg, source_entity=entity)
            except Exception as e:
                log.warning("poll %s: %s", chat_title(entity), e)
        await asyncio.sleep(5)


async def scan_history(client: TelegramClient, sources: list) -> None:
    if config.HISTORY_LIMIT <= 0:
        log.info("HISTORY_LIMIT=0 — историю пропускаем")
        return

    log.info("Сканирую историю (до %d сообщ. на чат)...", config.HISTORY_LIMIT)
    for entity in sources:
        title = chat_title(entity)
        found = 0
        try:
            async for message in client.iter_messages(entity, limit=config.HISTORY_LIMIT):
                before = len(_seen)
                await handle_message(client, message, source_entity=entity)
                if len(_seen) > before:
                    found += 1
        except Exception as e:
            log.error("Ошибка в чате %s: %s", title, e)
            continue
        log.info("  %s — новых совпадений: %d", title, found)


async def main() -> None:
    config.validate()
    await resolve_bot_targets()

    client = TelegramClient(
        config.SESSION_NAME,
        config.API_ID,
        config.API_HASH,
        device_model=config.DEVICE_MODEL,
        system_version=config.SYSTEM_VERSION,
        app_version=config.APP_VERSION,
        lang_code=config.LANG_CODE,
        system_lang_code=config.SYSTEM_LANG_CODE,
    )
    await client.start()
    me = await client.get_me()
    log.info("Вошли как: %s (id=%s)", me.username or me.first_name, me.id)
    log.info("Ключевые слова: %s", ", ".join(config.KEYWORDS))

    sources = await resolve_sources(client)
    if not sources:
        raise SystemExit("Нет доступных чатов для мониторинга")

    await scan_history(client, sources)

    if not config.LISTEN_NEW:
        log.info("LISTEN_NEW=false — завершаю работу")
        await client.disconnect()
        return

    from telethon import utils as tg_utils

    source_ids = {tg_utils.get_peer_id(e) for e in sources}
    log.info("ID источников для live: %s", ", ".join(str(i) for i in source_ids) or "все")

    @client.on(events.NewMessage)
    async def on_new_message(event: events.NewMessage.Event):
        chat = await event.get_chat()
        peer_id = tg_utils.get_peer_id(chat)
        if source_ids and peer_id not in source_ids:
            return
        text = event.message.text or event.message.message or ""
        log.info(
            "EVENT %s | peer=%s | %s",
            chat_title(chat),
            peer_id,
            (text or "(без текста)")[:120],
        )
        await handle_message(client, event.message, source_entity=chat)

    log.info("Слушаю новые сообщения (events + polling)... Ctrl+C выход")
    log.info("Пиши тест в @certification_rus или в группе обсуждения канала")
    await asyncio.gather(
        poll_new_messages(client, sources),
        client.run_until_disconnected(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Остановлено")
