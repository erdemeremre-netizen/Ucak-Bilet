#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YVR -> ESB ucuz ucus tarayici + Telegram bildirimci.

Fiyatlari Google Flights'tan dogrudan okur (fast-flights kutuphanesi) — API
anahtari GEREKMEZ. Sadece Telegram bot token + chat_id yeterli.

Belirtilen tarih araligindaki (varsayilan 17-31 Agustos 2026) tek yon
Vancouver -> Ankara uctan uca taranir, en ucuzlar bulunur ve Telegram'dan
mesaj atilir. Fiyat bir onceki taramaya gore dusunce 📉, hedef fiyatin
altina inince 🎯 isaretiyle uyarir.

Gerekli ortam degiskenleri (.env'den de okunur):
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

Istege bagli:
    ORIGIN (YVR), DESTINATION (ESB)
    START_DATE (2026-08-17), END_DATE (2026-08-31)
    ADULTS (1), SEAT (economy)
    MAX_STOPS (bos=sinirsiz, 0=direkt, 1=en fazla 1 aktarma)
    PRICE_THRESHOLD, NOTIFY_ALWAYS (true), TOP_N (3)
    FETCH_MODE (fallback), STATE_FILE (state.json)
"""

import os
import sys
import re
import json
import time
import html
import datetime as dt

import requests
from fast_flights import FlightData, Passengers, get_flights


# ---------------------------------------------------------------- .env yukle
def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv()


# ---------------------------------------------------------------- Config
def env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT = env("TELEGRAM_CHAT_ID")

ORIGIN = env("ORIGIN", "YVR")
DESTINATION = env("DESTINATION", "ESB")
START_DATE = env("START_DATE", "2026-08-17")
END_DATE = env("END_DATE", "2026-08-31")
ADULTS = int(env("ADULTS", "1"))
SEAT = env("SEAT", "economy")
MAX_STOPS = env("MAX_STOPS")
MAX_STOPS = int(MAX_STOPS) if MAX_STOPS not in (None, "") else None
PRICE_THRESHOLD = env("PRICE_THRESHOLD")
PRICE_THRESHOLD = float(PRICE_THRESHOLD) if PRICE_THRESHOLD else None
NOTIFY_ALWAYS = env("NOTIFY_ALWAYS", "true").lower() == "true"
TOP_N = int(env("TOP_N", "3"))
FETCH_MODE = env("FETCH_MODE", "fallback")
STATE_FILE = env("STATE_FILE", "state.json")


def fail(msg):
    print(f"[HATA] {msg}", file=sys.stderr)
    sys.exit(1)


for _name, _val in [("TELEGRAM_BOT_TOKEN", TG_TOKEN),
                    ("TELEGRAM_CHAT_ID", TG_CHAT)]:
    if not _val:
        fail(f"Ortam degiskeni eksik: {_name}")


# ---------------------------------------------------------------- Fiyat ayikla
def parse_price(s):
    """'C$1,234' / '$540' -> 1234.0 / 540.0 (sayisal). Cikmazsa None."""
    if not s:
        return None
    t = re.sub(r"[^0-9.,]", "", s).replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


# ---------------------------------------------------------------- Arama
def search_cheapest(date):
    """Tek bir tarih icin en ucuz tek yon ucusu (dict) ya da None."""
    try:
        res = get_flights(
            flight_data=[FlightData(date=date,
                                    from_airport=ORIGIN,
                                    to_airport=DESTINATION)],
            trip="one-way",
            seat=SEAT,
            passengers=Passengers(adults=ADULTS, children=0,
                                  infants_in_seat=0, infants_on_lap=0),
            fetch_mode=FETCH_MODE,
        )
    except Exception as e:
        print(f"[uyari] {date}: arama hatasi: {e}")
        return None

    best = None
    for f in getattr(res, "flights", []) or []:
        price_num = parse_price(getattr(f, "price", ""))
        if price_num is None:
            continue
        stops = getattr(f, "stops", 0) or 0
        if MAX_STOPS is not None and stops > MAX_STOPS:
            continue
        cand = {
            "date": date,
            "price_num": price_num,
            "price_str": getattr(f, "price", "?"),
            "stops": stops,
            "name": getattr(f, "name", "?"),
            "duration": getattr(f, "duration", ""),
            "departure": getattr(f, "departure", date),
        }
        if best is None or price_num < best["price_num"]:
            best = cand
    return best


# ---------------------------------------------------------------- Telegram
def send_telegram(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text,
              "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"[uyari] Telegram gonderilemedi ({r.status_code}): {r.text[:200]}")
    else:
        print("Telegram mesaji gonderildi.")


# ---------------------------------------------------------------- Durum
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[uyari] Durum kaydedilemedi: {e}")


def daterange(start, end):
    d0 = dt.date.fromisoformat(start)
    d1 = dt.date.fromisoformat(end)
    today = dt.date.today()
    cur = d0
    while cur <= d1:
        if cur >= today:
            yield cur.isoformat()
        cur += dt.timedelta(days=1)


def esc(s):
    return html.escape(str(s))


# ---------------------------------------------------------------- Ana akis
def main():
    results = []
    for date in daterange(START_DATE, END_DATE):
        best = search_cheapest(date)
        if best:
            results.append(best)
            print(f"{date}: {best['price_str']} "
                  f"({best['stops']} aktarma, {best['name']})")
        else:
            print(f"{date}: sonuc yok")
        time.sleep(1.5)   # Google'a nazik ol

    if not results:
        send_telegram(
            f"⚠️ <b>{esc(ORIGIN)} → {esc(DESTINATION)}</b> icin "
            f"{START_DATE} – {END_DATE} araliginda sonuc alinamadi."
        )
        return

    results.sort(key=lambda x: x["price_num"])
    cheapest = results[0]

    state = load_state()
    key = f"{ORIGIN}-{DESTINATION}-{START_DATE}-{END_DATE}"
    prev = state.get(key, {}).get("price")

    is_drop = prev is not None and cheapest["price_num"] < prev - 0.5
    below_threshold = (PRICE_THRESHOLD is not None
                       and cheapest["price_num"] <= PRICE_THRESHOLD)
    should_send = NOTIFY_ALWAYS or is_drop or below_threshold

    if prev is None or cheapest["price_num"] < prev:
        state[key] = {"price": cheapest["price_num"],
                      "date": cheapest["date"],
                      "updated": dt.datetime.now().isoformat(timespec="minutes")}
        save_state(state)

    if not should_send:
        print("Bildirim kosulu yok — mesaj atlandi.")
        return

    lines = [f"✈️ <b>{esc(ORIGIN)} → {esc(DESTINATION)}</b> · tek yon"]
    if is_drop:
        lines.append(f"📉 <b>Fiyat dustu!</b> Onceki en dusuk: {prev:.0f}")
    if below_threshold:
        lines.append(f"🎯 <b>Hedefin altinda!</b> (hedef: {PRICE_THRESHOLD:.0f})")

    c = cheapest
    lines.append("")
    lines.append(f"🏆 <b>{esc(c['price_str'])}</b> — {esc(c['departure'])}")
    lines.append(f"{c['stops']} aktarma · {esc(c['name'])} · {esc(c['duration'])}")
    lines.append("")
    lines.append("📅 <b>En ucuz secenekler:</b>")
    for r in results[:TOP_N]:
        lines.append(f"• <b>{esc(r['price_str'])}</b> — {esc(r['departure'])} "
                     f"({r['stops']} aktarma, {esc(r['name'])})")
    lines.append("")
    lines.append(f"🔎 {START_DATE} – {END_DATE} tarandi · "
                 f"{dt.datetime.now().strftime('%d.%m %H:%M')}")
    lines.append("Bilet icin tarihi Google Flights'ta aratman yeterli.")

    send_telegram("\n".join(lines))


if __name__ == "__main__":
    main()
