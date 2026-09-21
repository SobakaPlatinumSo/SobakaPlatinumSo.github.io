#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проверяет почтовый ящик (IMAP) на новые письма об отзывах
и присылает их в Telegram.

Запускается в GitHub Actions (см. .github/workflows/review-notify.yml).

Обязательные переменные окружения (GitHub Secrets):
  IMAP_HOST  - IMAP-сервер (например, imap.yandex.ru)
  IMAP_USER  - адрес почты
  IMAP_PASS  - пароль или «пароль приложения»
  TG_TOKEN   - токен Telegram-бота (от @BotFather)
  TG_CHAT_ID - ваш Telegram chat_id

Необязательные:
  SUBJECT_FILTER - ключевое слово в теме письма (по умолчанию «отзыв»).
                   Пустое значение = присылать все письма.
"""
import email
import imaplib
import json
import os
import re
import urllib.request
from email.header import decode_header

IMAP_HOST = os.environ["IMAP_HOST"]
IMAP_USER = os.environ["IMAP_USER"]
IMAP_PASS = os.environ["IMAP_PASS"]
TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]
SUBJECT_FILTER = os.environ.get("SUBJECT_FILTER", "").strip() or "отзыв"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "last_uid.txt")
MAX_PER_RUN = 20


def decode_header_value(value):
    if not value:
        return ""
    parts = []
    for text, enc in decode_header(value):
        if isinstance(text, bytes):
            parts.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts)


def get_text(msg):
    """Достаёт текстовое содержимое письма."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    html_text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    return re.sub(r"<[^>]+>", " ", html_text)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def send_tg(text):
    url = "https://api.telegram.org/bot{}/sendMessage".format(TG_TOKEN)
    data = json.dumps({
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError("Telegram API error: {}".format(result))


def read_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content.isdigit():
                return int(content)
    return None


def write_state(uid):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(str(uid))


def main():
    last_uid = read_state()
    first_run = last_uid is None

    mail = imaplib.IMAP4_SSL(IMAP_HOST, 993)
    try:
        mail.login(IMAP_USER, IMAP_PASS)
        mail.select("INBOX")
        typ, data = mail.uid("SEARCH", None, "ALL")
        all_uids = [u.decode() for u in (data[0].split() if data[0] else [])]
        max_uid = int(all_uids[-1]) if all_uids else 0

        # Первый запуск: просто запоминаем текущее состояние,
        # чтобы не слать в Telegram всю старую почту.
        if first_run:
            write_state(max_uid)
            print("Первый запуск: состояние инициализировано (UID {}), письма не отправлялись".format(max_uid))
            return

        new_uids = [u for u in all_uids if int(u) > last_uid][:MAX_PER_RUN]
        if not new_uids:
            print("Новых писем нет")
            return

        sent = 0
        for uid in new_uids:
            typ, msg_data = mail.uid("FETCH", uid, "(RFC822)")
            raw = None
            for part in msg_data:
                if isinstance(part, tuple):
                    raw = part[1]
                    break
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            subject = decode_header_value(msg.get("Subject"))
            sender = decode_header_value(msg.get("From"))

            if SUBJECT_FILTER and SUBJECT_FILTER.lower() not in subject.lower():
                continue

            body = " ".join(get_text(msg).split())
            if len(body) > 300:
                body = body[:300] + "…"

            text = "📩 <b>Новое письмо</b>\n\n<b>От:</b> {}\n<b>Тема:</b> {}\n\n{}".format(
                sender, subject, body
            )
            try:
                send_tg(text)
                sent += 1
            except Exception as e:
                print("Не удалось отправить в Telegram: {}".format(e))

        write_state(max_uid)
        print("Проверено новых писем: {}, отправлено в Telegram: {}".format(len(new_uids), sent))
    finally:
        try:
            mail.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
