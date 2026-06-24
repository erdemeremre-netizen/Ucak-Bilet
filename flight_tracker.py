#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YVR -> ESB ucuz uçuş tarayıcı + Telegram bildirimci.

Belirtilen tarih aralığındaki (varsayılan: 17–31 Ağustos 2026) tek yön
Vancouver -> Ankara uçuşlarını Amadeus API ile tarar, en ucuzları bulur ve
Telegram'dan mesaj atar. Fiyat bir önceki taramaya göre düştüğünde özel olarak
işaretler (📉). Bir hedef fiyat verirsen, onun altına inince ayrıca uyarır (🎯).

Çalıştırma:
    python flight_tracker.py

Gerekli ortam değişkenleri (.env dosyasından da okunur):
    AMADEUS_CLIENT_ID
    AMADEUS_CLIENT_SECRET
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

İsteğe bağlı:
    ORIGIN (varsayılan YVR), DESTINATION (varsayılan ESB)
    START_DATE (YYYY-MM-DD, varsayılan 2026-08-17)
    END_DATE   (YYYY-MM-DD, varsayılan 2026-08-31)
    CURRENCY (varsayılan CAD), ADULTS (varsayılan 1)
    PRICE_THRESHOLD  (sayı; bu fiyatın altına düşünce mutlaka bildir)
    NOTIFY_ALWAYS    (true/false; her çalışmada özet at — varsayılan true)
    MAX_STOPS        (boş = sınırsız; 0 = sadece direkt; 1 = en fazla 1 aktarma)
    TOP_N            (mesajda kaç tarih gösterilsin — varsayılan 3)
    STATE_FILE       (varsayılan state.json)
    AMADEUS_BASE     (varsayılan https://api.amadeus.com ; test: https://test.api.amadeus.com)
"""

import os
import sys
import json
import time
import datetime as dt
import requests


# ---------------------------------------------------------------- .env yükle
def load_dotenv(path=".env"):
    """Ekstra paket gerektirmeden basit .env okuyucu."""
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


AMADEUS_BASE = env("AMADEUS_BASE", "https://api.amadeus.com")
CLIENT_ID = env("AMADEUS_CLIENT_ID")
CLIENT_SECRET = env("AMADEUS_CLIENT_SECRET")
TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT = env("TELEGRAM_CHAT_ID")

ORIGIN = env("ORIGIN", "YVR")
DESTINATION = env("DESTINATION", "ESB")
START_DATE = env("START_DATE", "2026-08-17")
END_DATE = env("END_DATE", "2026-08-31")
CURRENCY = env("CURRENCY", "CAD")
ADULTS = int(env("ADULTS", "1"))
PRICE_THRESHOLD = env("PRICE_THRESHOLD")
PRICE_THRESHOLD = float(PRICE_THRESHOLD) if PRICE_THRESHOLD else None
NOTIFY_ALWAYS = env("NOTIFY_ALWAYS", "true").lower() == "true"
MAX_STOPS = env("MAX_STOPS")
MAX_STOPS = int(MAX_STOPS) if MAX_STOPS not in (None, "") else None
TOP_N = int(env("TOP_N", "3"))
STATE_FILE = env("STATE_FILE", "state.json")

AYLAR = ["Oca", "Şub", "Mar", "Nis", "May", "Haz",
         "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]


def fail(msg):
    print(f"[HATA] {msg}", file=sys.stderr)
    sys.exit(1)


for _name, _val in [("AMADEUS_CLIENT_ID", CLIENT_ID),
                    ("AMADEUS_CLIENT_SECRET", CLIENT_SECRET),
                    ("TELEGRAM_BOT_TOKEN", TG_TOKEN),
                    ("TELEGRAM_CHAT_ID", TG_CHAT)]:
    if not _val:
        fail(f"Ortam değişkeni eksik: {_name} (.env dosyasını doldurdun mu?)")


# ---------------------------------------------------------------- Amadeus
def get_token():
    r = requests.post(
        f"{AMADEUS_BASE}/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if r.status_code != 200:
        fail(f"Amadeus token alınamadı ({r.status_code}): {r.text[:300]}")
    return r.json()["access_token"]


def search_cheapest(token, date, _retry=0):
    """Tek bir tarih için en ucuz tek yön teklifi (dict) ya da None döndürür."""
    params = {
        "originLocationCode": ORIGIN,
        "destinationLocationCode": DESTINATION,
        "departureDate": date,
        "adults": ADULTS,
        "currencyCode": CURRENCY,
        "max": 15,
    }
    if MAX_STOPS == 0:
        params["nonStop"] = "true"

    r = requests.get(
        f"{AMADEUS_BASE}/v2/shopping/flight-offers",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )

    if r.status_code == 429 and _retry < 3:
        time.sleep(2 * (_retry + 1))
        return search_cheapest(token, date, _retry + 1)
    if r.status_code != 200:
        print(f"[uyarı] {date}: arama hatası {r.status_code}: {r.text[:160]}")
        return None

    best = None
    for o in r.json().get("data", []):
        try:
            price = float(o["price"]["grandTotal"])
            segs = o["itineraries"][0]["segments"]
        except (KeyError, IndexError, ValueError):
            continue
        stops = len(segs) - 1
        if MAX_STOPS is not None and stops > MAX_STOPS:
            continue
        cand = {
            "date": date,
            "price": price,
            "stops": stops,
            "carriers": "+".join(sorted({s["carrierCode"] for s in segs})),
            "dep": segs[0]["departure"]["at"],
            "arr": segs[-1]["arrival"]["at"],
        }
        if best is None or price < best["price"]:
            best = cand
    return best


# ---------------------------------------------------------------- Telegram
def send_telegram(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={
            "chat_id": TG_CHAT,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if r.status_code != 200:
        print(f"[uyarı] Telegram gönderilemedi ({r.status_code}): {r.text[:200]}")
    else:
        print("Telegram mesajı gönderildi.")


# ---------------------------------------------------------------- Durum (state)
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
        print(f"[uyarı] Durum kaydedilemedi: {e}")


# ---------------------------------------------------------------- Yardımcılar
def daterange(start, end):
    d0 = dt.date.fromisoformat(start)
    d1 = dt.date.fromisoformat(end)
    today = dt.date.today()
    cur = d0
    while cur <= d1:
        if cur >= today:          # geçmiş tarihleri atla
            yield cur.isoformat()
        cur += dt.timedelta(days=1)


def fmt_dt(s):
    try:
        x = dt.datetime.fromisoformat(s)
        return f"{x.day} {AYLAR[x.month - 1]} {x.strftime('%H:%M')}"
    except Exception:
        return s


# ---------------------------------------------------------------- Ana akış
def main():
    token = get_token()

    results = []
    for date in daterange(START_DATE, END_DATE):
        best = search_cheapest(token, date)
        if best:
            results.append(best)
            print(f"{date}: {best['price']:.0f} {CURRENCY} "
                  f"({best['stops']} aktarma, {best['carriers']})")
        else:
            print(f"{date}: sonuç yok")
        time.sleep(0.4)           # API'ye nazik ol

    if not results:
        send_telegram(
            f"⚠️ <b>{ORIGIN} → {DESTINATION}</b> için "
            f"{START_DATE} – {END_DATE} aralığında uçuş bulunamadı."
        )
        return

    results.sort(key=lambda x: x["price"])
    cheapest = results[0]

    state = load_state()
    key = f"{ORIGIN}-{DESTINATION}-{START_DATE}-{END_DATE}"
    prev = state.get(key, {}).get("price")

    is_drop = prev is not None and cheapest["price"] < prev - 0.5
    below_threshold = (PRICE_THRESHOLD is not None
                       and cheapest["price"] <= PRICE_THRESHOLD)
    should_send = NOTIFY_ALWAYS or is_drop or below_threshold

    # her zaman görülen en düşüğü sakla
    if prev is None or cheapest["price"] < prev:
        state[key] = {
            "price": cheapest["price"],
            "date": cheapest["date"],
            "updated": dt.datetime.now().isoformat(timespec="minutes"),
        }
        save_state(state)

    if not should_send:
        print("Bildirim koşulu yok (fiyat düşmedi, hedef yok) — mesaj atlandı.")
        return

    lines = [f"✈️ <b>{ORIGIN} → {DESTINATION}</b> · tek yön · {CURRENCY}"]
    if is_drop:
        lines.append(f"📉 <b>Fiyat düştü!</b> Önceki en düşük: {prev:.0f} {CURRENCY}")
    if below_threshold:
        lines.append(f"🎯 <b>Hedefin altında!</b> (hedef: {PRICE_THRESHOLD:.0f})")

    c = cheapest
    lines.append("")
    lines.append(f"🏆 <b>{c['price']:.0f} {CURRENCY}</b> — {fmt_dt(c['dep'])} kalkış · "
                 f"{c['stops']} aktarma · {c['carriers']}")
    lines.append("")
    lines.append("📅 <b>En ucuz tarihler:</b>")
    for r in results[:TOP_N]:
        lines.append(f"• {fmt_dt(r['dep'])} — <b>{r['price']:.0f} {CURRENCY}</b> "
                     f"({r['stops']} aktarma, {r['carriers']})")
    lines.append("")
    lines.append(f"🔎 {START_DATE} – {END_DATE} tarandı · "
                 f"{dt.datetime.now().strftime('%d.%m %H:%M')}")
    lines.append("Bilet almak için tarihi Google Flights / havayolu sitesinde aratman yeterli.")

    send_telegram("\n".join(lines))


if __name__ == "__main__":
    main()
