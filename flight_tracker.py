#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YVR -> ESB ucuz ucus tarayici + Telegram bildirimci.

Fiyatlari Google Flights'tan dogrudan okur (fast-flights) — API anahtari
GEREKMEZ. Sadece Telegram bot token + chat_id yeterli.

Belirtilen tarih araligindaki (varsayilan 17-31 Agustos 2026) tek yon
Vancouver -> Ankara taranir, en ucuzlar bulunur ve Telegram'dan mesaj atilir.
Fiyat onceki taramaya gore dusunce 📉, hedef altina inince 🎯 isaretlenir.

Gerekli: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Istege bagli: ORIGIN(YVR) DESTINATION(ESB) START_DATE END_DATE CURRENCY(CAD)
              ADULTS(1) SEAT(economy) MAX_STOPS PRICE_THRESHOLD
              NOTIFY_ALWAYS(true) TOP_N(3) STATE_FILE(state.json)
"""

import os
import sys
import json
import time
import html
import datetime as dt

import requests
from fast_flights import FlightQuery, Passengers, create_filter, get_flights


# ------------------------------------------------------------- .env yukle
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


# ------------------------------------------------------------- Config
def env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT = env("TELEGRAM_CHAT_ID")

ORIGIN = env("ORIGIN", "YVR")
DESTINATION = env("DESTINATION", "ESB")
START_DATE = env("START_DATE", "2026-08-17")
END_DATE = env("END_DATE", "2026-08-31")
CURRENCY = env("CURRENCY", "CAD")
ADULTS = int(env("ADULTS", "1"))
SEAT = env("SEAT", "economy")
MAX_STOPS = env("MAX_STOPS")
MAX_STOPS = int(MAX_STOPS) if MAX_STOPS not in (None, "") else None
PRICE_THRESHOLD = env("PRICE_THRESHOLD")
PRICE_THRESHOLD = float(PRICE_THRESHOLD) if PRICE_THRESHOLD else None
NOTIFY_ALWAYS = env("NOTIFY_ALWAYS", "true").lower() == "true"
TOP_N = int(env("TOP_N", "3"))
STATE_FILE = env("STATE_FILE", "state.json")

AYLAR = ["Oca", "Sub", "Mar", "Nis", "May", "Haz",
         "Tem", "Agu", "Eyl", "Eki", "Kas", "Ara"]


def fail(msg):
    print(f"[HATA] {msg}", file=sys.stderr)
    sys.exit(1)


for _n, _v in [("TELEGRAM_BOT_TOKEN", TG_TOKEN), ("TELEGRAM_CHAT_ID", TG_CHAT)]:
    if not _v:
        fail(f"Ortam degiskeni eksik: {_n}")


# ------------------------------------------------------------- Tarih/sure
def sdt_to_dt(sdt):
    """fast-flights SimpleDatetime -> datetime (ya da None)."""
    try:
        y, m, d = sdt.date
        hh, mm = sdt.time
        return dt.datetime(y, m, d, hh, mm)
    except Exception:
        return None


def fmt_sdt(sdt):
    x = sdt_to_dt(sdt)
    if not x:
        return "?"
    return f"{x.day} {AYLAR[x.month - 1]} {x.strftime('%H:%M')}"


def fmt_dur(minutes):
    if minutes is None:
        return ""
    return f"{minutes // 60}sa {minutes % 60}dk"


# ------------------------------------------------------------- Arama
def search_cheapest(date):
    """Tek bir tarih icin en ucuz tek yon ucusu (dict) ya da None."""
    try:
        flt = create_filter(
            flights=[FlightQuery(date=date,
                                 from_airport=ORIGIN,
                                 to_airport=DESTINATION,
                                 max_stops=MAX_STOPS)],
            trip="one-way",
            seat=SEAT,
            passengers=Passengers(adults=ADULTS, children=0,
                                  infants_in_seat=0, infants_on_lap=0),
            currency=CURRENCY,
            max_stops=MAX_STOPS,
        )
        result = get_flights(flt)
    except Exception as e:
        print(f"[uyari] {date}: arama hatasi: {e}")
        return None

    best = None
    for f in result or []:
        try:
            price = int(getattr(f, "price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        legs = getattr(f, "flights", []) or []
        stops = max(len(legs) - 1, 0)
        if MAX_STOPS is not None and stops > MAX_STOPS:
            continue

        dep = legs[0].departure if legs else None
        dep_dt = sdt_to_dt(dep) if legs else None
        arr_dt = sdt_to_dt(legs[-1].arrival) if legs else None
        dur = int((arr_dt - dep_dt).total_seconds() // 60) \
            if (dep_dt and arr_dt) else None
        airlines = getattr(f, "airlines", None) or []
        cand = {
            "date": date,
            "price": price,
            "stops": stops,
            "airlines": ", ".join(airlines) if airlines else "?",
            "dep": fmt_sdt(dep) if dep else date,
            "dur": fmt_dur(dur),
        }
        if best is None or price < best["price"]:
            best = cand
    return best


# ------------------------------------------------------------- Telegram
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


# ------------------------------------------------------------- Durum
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


# ------------------------------------------------------------- Ana akis
def main():
    results = []
    for date in daterange(START_DATE, END_DATE):
        best = search_cheapest(date)
        if best:
            results.append(best)
            print(f"{date}: {best['price']} {CURRENCY} "
                  f"({best['stops']} aktarma, {best['airlines']})")
        else:
            print(f"{date}: sonuc yok")
        time.sleep(2)

    if not results:
        send_telegram(
            f"⚠️ <b>{esc(ORIGIN)} → {esc(DESTINATION)}</b> icin "
            f"{START_DATE} – {END_DATE} araliginda sonuc alinamadi.\n"
            f"(Google gecici engellemis olabilir; bir sonraki taramada tekrar denenecek.)"
        )
        return

    results.sort(key=lambda x: x["price"])
    cheapest = results[0]

    state = load_state()
    key = f"{ORIGIN}-{DESTINATION}-{START_DATE}-{END_DATE}"
    prev = state.get(key, {}).get("price")

    is_drop = prev is not None and cheapest["price"] < prev
    below_threshold = (PRICE_THRESHOLD is not None
                       and cheapest["price"] <= PRICE_THRESHOLD)
    should_send = NOTIFY_ALWAYS or is_drop or below_threshold

    if prev is None or cheapest["price"] < prev:
        state[key] = {"price": cheapest["price"], "date": cheapest["date"],
                      "updated": dt.datetime.now().isoformat(timespec="minutes")}
        save_state(state)

    if not should_send:
        print("Bildirim kosulu yok — mesaj atlandi.")
        return

    lines = [f"✈️ <b>{esc(ORIGIN)} → {esc(DESTINATION)}</b> · tek yon · {CURRENCY}"]
    if is_drop:
        lines.append(f"📉 <b>Fiyat dustu!</b> Onceki en dusuk: {prev} {CURRENCY}")
    if below_threshold:
        lines.append(f"🎯 <b>Hedefin altinda!</b> (hedef: {PRICE_THRESHOLD:.0f})")

    c = cheapest
    lines.append("")
    lines.append(f"🏆 <b>{c['price']} {CURRENCY}</b> — {esc(c['dep'])} kalkis")
    lines.append(f"{c['stops']} aktarma · {esc(c['airlines'])} · {esc(c['dur'])}")
    lines.append("")
    lines.append("📅 <b>En ucuz tarihler:</b>")
    for r in results[:TOP_N]:
        lines.append(f"• <b>{r['price']} {CURRENCY}</b> — {esc(r['dep'])} "
                     f"({r['stops']} aktarma, {esc(r['airlines'])})")
    lines.append("")
    lines.append(f"🔎 {START_DATE} – {END_DATE} tarandi · "
                 f"{dt.datetime.now().strftime('%d.%m %H:%M')}")
    lines.append("Bilet icin tarihi Google Flights'ta aratman yeterli.")

    send_telegram("\n".join(lines))


if __name__ == "__main__":
    main()
