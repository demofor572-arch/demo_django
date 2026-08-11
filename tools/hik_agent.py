#!/usr/bin/env python3
"""Hikvision terminaliga yuzlarni yozib turadigan lokal agent.

NIMA UCHUN KERAK
────────────────
Terminal odatda o'quv markazining ichki tarmog'ida turadi va unga
internetdan kirib bo'lmaydi. Ya'ni bulutdagi server terminalga o'zi
ulanolmaydi. Shuning uchun sinxronlash teskari yo'nalishda bo'ladi:
terminal bilan bir tarmoqdagi kompyuterda shu skript ishlaydi, u
serverdan navbatni oladi va terminalga o'zi yozadi.

Terminalga tashqaridan kirish ochilgan (statik IP / port forwarding)
bo'lsa bu skript kerak emas — panelning o'zi yozadi.

ISHGA TUSHIRISH
───────────────
    pip install requests
    python hik_agent.py \
        --sync-url https://SERVER/api/faceid/sync/KALIT/ \
        --host http://192.168.1.64 \
        --user admin \
        --password TERMINAL_PAROLI

`--sync-url` ni supermenejer panelidagi «Yuz tanish» bo'limidan,
terminal kartochkasidan nusxa olasiz.

Skript har 60 soniyada bir tekshiradi va terminal yonidagi
kompyuterda doim ochiq turishi kerak. Bir martalik yozish uchun
`--once` qo'shing.
"""

import argparse
import base64
import json
import sys
import time

try:
    import requests
    from requests.auth import HTTPDigestAuth
except ImportError:
    sys.exit("Avval kutubxonani o'rnating:  pip install requests")


# Terminal xatolarining odam tushunadigan tarjimasi
ERRORS = {
    "employeeNoAlreadyExist": "Bu raqam terminalda allaqachon bor",
    "lowFaceQuality": "Yuz sifati past",
    "faceQualityLow": "Yuz sifati past",
    "noFaceDetected": "Rasmda yuz topilmadi",
    "detectNoFace": "Rasmda yuz topilmadi",
    "faceDetectFailed": "Rasmda yuz aniqlanmadi",
    "imageSizeExceedLimit": "Rasm hajmi katta",
    "badAuthorization": "Terminal logini yoki paroli noto'g'ri",
}


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def isapi_result(response):
    """ISAPI javobini o'qiydi. Qaytaradi: (muvaffaqiyat, xato_matni, kod).

    Hikvision xatoni ko'pincha HTTP 200 bilan, javob tanasida qaytaradi —
    faqat status kodga qarash yetmaydi. Uchinchi qiymat — terminalning
    o'z kodi: xabar o'zbekchaga o'girilgani uchun uni matndan qidirib
    bo'lmaydi.
    """
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}

    sub = str(body.get("subStatusCode") or "")
    status = body.get("statusCode")
    if response.status_code < 400 and status in (None, 1, "1"):
        return True, "", sub

    if sub:
        return False, ERRORS.get(sub, sub), sub
    text = (body.get("statusString") or response.text or "").strip()
    return False, f"HTTP {response.status_code}: {text[:120]}", ""


def is_already_exists(code):
    """Terminal «bu raqam allaqachon bor» deyaptimi."""
    lowered = str(code).lower()
    return "alreadyexist" in lowered or "exist" in lowered


def write_user(session, host, auth, person_id, name):
    """O'quvchini terminal foydalanuvchilariga yozadi."""
    payload = {
        "UserInfo": {
            "employeeNo": person_id,
            "name": name[:32],
            "userType": "normal",
            "Valid": {
                "enable": True,
                "beginTime": "2020-01-01T00:00:00",
                "endTime": "2035-12-31T23:59:59",
                "timeType": "local",
            },
        }
    }

    res = session.post(
        f"{host}/ISAPI/AccessControl/UserInfo/Record?format=json",
        auth=auth,
        json=payload,
        timeout=15,
    )
    ok, message, code = isapi_result(res)
    if ok:
        return True, ""
    if not is_already_exists(code):
        return False, message

    # Allaqachon bor — ismi o'zgargan bo'lishi mumkin, yangilaymiz
    res = session.put(
        f"{host}/ISAPI/AccessControl/UserInfo/Modify?format=json",
        auth=auth,
        json=payload,
        timeout=15,
    )
    ok, message, _code = isapi_result(res)
    return ok, message


def write_face(session, host, auth, person_id, photo_b64):
    """Yuz rasmini terminal kutubxonasiga yozadi."""
    try:
        image = base64.b64decode(photo_b64)
    except (ValueError, TypeError):
        return False, "Rasm formati noto'g'ri"

    res = session.post(
        f"{host}/ISAPI/Intelligent/FDLib/FDSetUp?format=json",
        auth=auth,
        timeout=30,
        files={
            "FaceDataRecord": (
                None,
                json.dumps(
                    {"faceLibType": "blackFD", "FDID": "1", "FPID": person_id}
                ),
                "application/json",
            ),
            "img": ("face.jpg", image, "image/jpeg"),
        },
    )
    return isapi_result(res)


def sync_once(args, session):
    """Navbatni oladi, terminalga yozadi, natijani qaytaradi."""
    try:
        res = session.get(args.sync_url, timeout=30)
        res.raise_for_status()
        queue = res.json()
    except requests.RequestException as exc:
        log(f"Serverga ulanib bo'lmadi: {exc}")
        return
    except ValueError:
        log("Server tushunarsiz javob qaytardi — manzilni tekshiring")
        return

    students = queue.get("students") or []
    if not students:
        return

    total = queue.get("total", len(students))
    log(f"Navbatda {total} ta yuz — {len(students)} tasi yozilmoqda")

    auth = HTTPDigestAuth(args.user, args.password)
    host = args.host.rstrip("/")
    results = []

    for student in students:
        person_id = student.get("person_id")
        name = student.get("name") or person_id
        photo = student.get("photo") or ""

        ok, message = write_user(session, host, auth, person_id, name)
        if ok and photo:
            ok, message = write_face(session, host, auth, person_id, photo)

        results.append({"person_id": person_id, "ok": ok, "error": message})
        log(f"  {name} (#{person_id}): " + ("✓ yozildi" if ok else f"✗ {message}"))

    try:
        res = session.post(args.sync_url, json={"results": results}, timeout=30)
        res.raise_for_status()
    except requests.RequestException as exc:
        # Natija yetib bormasa server ularni navbatda deb biladi va
        # keyingi aylanishda qayta beradi — yo'qolgan narsa yo'q
        log(f"Natijani serverga yuborib bo'lmadi: {exc}")


def main():
    parser = argparse.ArgumentParser(
        description="Hikvision terminaliga yuzlarni yozadi"
    )
    parser.add_argument(
        "--sync-url",
        required=True,
        help="Paneldagi terminal kartochkasidan olingan sinxronlash manzili",
    )
    parser.add_argument(
        "--host", required=True, help="Terminal manzili, masalan http://192.168.1.64"
    )
    parser.add_argument("--user", default="admin", help="Terminal logini")
    parser.add_argument("--password", required=True, help="Terminal paroli")
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Necha soniyada bir tekshirilsin (standart 60)",
    )
    parser.add_argument(
        "--once", action="store_true", help="Bir marta yozib chiqib ketsin"
    )
    args = parser.parse_args()

    if not args.host.startswith(("http://", "https://")):
        args.host = "http://" + args.host

    log(f"Agent ishga tushdi — terminal {args.host}")
    session = requests.Session()

    if args.once:
        sync_once(args, session)
        return

    while True:
        try:
            sync_once(args, session)
        except Exception as exc:  # noqa: BLE001 — agent to'xtab qolmasin
            log(f"Kutilmagan xato: {exc}")
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("To'xtatildi")
