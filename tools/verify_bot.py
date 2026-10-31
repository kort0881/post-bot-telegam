#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка бота: getMe + инфо о канале. Токен только из окружения, не печатается."""
import json
import os
import sys
import urllib.parse
import urllib.request


def api(token: str, method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=25) as response:
        return json.loads(response.read().decode())


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_POST_BOT_TOKEN") or ""
    if not token:
        print("нет TELEGRAM_BOT_TOKEN в окружении")
        return 2
    try:
        me = api(token, "getMe")["result"]
    except Exception as e:
        print(f"getMe не прошёл: {e}")
        return 1
    print(f"bot: id={me.get('id')} @{me.get('username')} name={me.get('first_name')!r}")

    chat = (os.getenv("CHANNEL_ID") or "").strip() or (os.getenv("CHANNEL_USERNAME") or "").strip()
    if chat:
        try:
            info = api(token, "getChat", chat_id=chat)["result"]
            print(f"channel: id={info.get('id')} @{info.get('username')} "
                  f"title={info.get('title')!r} type={info.get('type')}")
            try:
                member = api(token, "getChatMember", chat_id=chat,
                             user_id=me["id"])["result"]
                print(f"rights: статус бота в канале = {member.get('status')}, "
                      f"can_post={member.get('can_post_messages')}")
            except Exception as e:
                print(f"права бота в канале неизвестны (не участник или нет прав): {e}")
        except Exception as e:
            print(f"getChat({chat}) не прошёл: {e}")
    else:
        print("CHANNEL_ID/CHANNEL_USERNAME не заданы — инфо о канале пропущено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
