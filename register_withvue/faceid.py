"""Yuz tanish terminali bilan ishlash (Hikvision DS-K1T3xx).

Uch yo'nalish bor:

1. Terminal → server ("HTTP listening"). Terminal yuzni taniganda
   hodisani shu yerdagi webhook'ga yuboradi, biz davomatni belgilaymiz.
   Bu asosiy yo'l va u lokal tarmoqda ham ishlaydi — terminal
   internetga chiqa olsa bo'ldi.

2. Server → terminal (ISAPI). O'quvchi va uning rasmini terminalga
   yuborish. Bu faqat terminalga tashqaridan kirish mumkin bo'lganda
   ishlaydi (statik IP / port forwarding), shuning uchun ixtiyoriy.

3. Terminal yonidagi agent → server. Terminal NAT ortida bo'lsa (odatiy
   holat) serverdan unga kirib bo'lmaydi. O'shanda lokal tarmoqdagi
   kichik skript navbatni serverdan o'zi olib, terminalga yozadi —
   `pending_for_device` / `mark_synced` shu uchun.

O'quvchining yuz rasmi Telegram bot orqali keladi va bazada base64
JPEG bo'lib saqlanadi. Har o'quvchiga takrorlanmas raqam beriladi —
Hikvision uni `employeeNo` deb ataydi.
"""

import base64
import io
import json
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    Attendance,
    FaceDevice,
    FaceEvent,
    FaceSync,
    Lesson,
    Student,
)

log = logging.getLogger(__name__)

# Dars boshlanganidan keyin shu daqiqagacha kelgani "Keldi" hisoblanadi
DEFAULT_GRACE_MINUTES = 15

ODD_DAYS = {0, 2, 4}
EVEN_DAYS = {1, 3, 5}

# ── Yuz rasmi talablari (Hikvision DS-K1T3xx hujjatidan) ──
# Terminal 200 KB dan katta rasmni qabul qilmaydi va juda katta
# o'lchamni ham rad etadi. Rasm shu chegaralarga o'zi moslashtiriladi,
# ya'ni o'quvchi telefonidan chiqqan 3 MB lik surat ham yaraydi.
MAX_PHOTO_BYTES = 200 * 1024
MAX_PHOTO_SIDE = 1024
MIN_PHOTO_SIDE = 240

# Tomonlar nisbati 4:3 (yoki portret holatda 3:4) bo'lishi so'raladi.
# Aniq 1.333 ni talab qilsak telefon suratlari deyarli hech qachon
# o'tmasdi, shuning uchun atrofidagi oraliq qabul qilinadi. 16:9 (1.78)
# va kvadrat (1.0) bundan tashqarida qoladi — ular yuz uchun yomon
# kadr: birinchisida yuz juda kichik, ikkinchisida odatda kesilgan.
MIN_ASPECT = 1.15
MAX_ASPECT = 1.60

# Terminal raqamlari shu sondan boshlanadi. O'quvchi ID siga qo'shiladi,
# ya'ni raqam hech qachon takrorlanmaydi: ID lar qayta ishlatilmaydi.
PERSON_ID_BASE = 10000


# ─────────────────────────────────────────
# TERMINAL → SERVER
# ─────────────────────────────────────────


def parse_event(request):
    """Hikvision yuborgan hodisadan kerakli maydonlarni ajratadi.

    Terminal proshivkasiga qarab ikki xil yuboradi: toza JSON yoki
    `multipart/form-data` (ichida `event_log` nomli JSON qism va
    ixtiyoriy rasm). Ikkalasi ham qo'llab-quvvatlanadi.

    Qaytaradi: (ma'lumot_dict, xato_matni). Xato bo'lsa birinchisi None.
    """
    raw = None

    # multipart — qism nomi proshivkada har xil bo'lishi mumkin
    if request.content_type and "multipart" in request.content_type:
        for key in ("event_log", "Event_log", "eventLog"):
            if key in request.POST:
                raw = request.POST[key]
                break
        if raw is None:
            for key, f in request.FILES.items():
                if "log" in key.lower() or "json" in key.lower():
                    raw = f.read().decode("utf-8", "ignore")
                    break
    else:
        raw = (request.body or b"").decode("utf-8", "ignore")

    if not raw:
        return None, "Hodisa ma'lumoti topilmadi"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, "JSON o'qib bo'lmadi"

    ev = data.get("AccessControllerEvent") or {}

    # Shaxs raqami — proshivkada nomi turlicha
    person_id = (
        ev.get("employeeNoString")
        or ev.get("employeeNo")
        or data.get("employeeNoString")
        or ""
    )
    person_id = str(person_id).strip()

    return {
        "person_id": person_id,
        "person_name": str(ev.get("name") or "").strip()[:200],
        "serial": str(ev.get("serialNo") or data.get("macAddress") or "").strip()[:64],
        "device_name": str(ev.get("deviceName") or "").strip(),
        "happened_at": _parse_time(data.get("dateTime")),
        "major": ev.get("majorEventType"),
        "minor": ev.get("subEventType"),
        "verify_mode": str(ev.get("currentVerifyMode") or "").strip(),
    }, None


def _parse_time(value):
    """Terminal vaqtini o'qiydi; o'qib bo'lmasa hozirgi vaqt."""
    if not value:
        return timezone.now()
    from django.utils.dateparse import parse_datetime

    try:
        dt = parse_datetime(str(value))
    except (ValueError, TypeError):
        dt = None
    if dt is None:
        return timezone.now()
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def is_access_granted(info):
    """Bu hodisa "yuz tanildi va ruxsat berildi" degani mi.

    Hikvision'da majorEventType=5 — "Event" turkumi, subEventType=75
    "yuz bo'yicha tasdiqlandi". Eshik ochilmadi / notanish yuz kabi
    hodisalar boshqa kodlar bilan keladi va davomatga tegmaydi.

    Proshivkalar orasida kodlar farq qilishi mumkin, shuning uchun
    shaxs raqami bor bo'lsa ham tan olamiz — raqamsiz hodisa
    baribir hech kimga bog'lanmaydi.
    """
    if not info.get("person_id"):
        return False
    major, minor = info.get("major"), info.get("minor")
    if major == 5 and minor in (1, 38, 75, 76):
        return True
    # Kodlar noma'lum bo'lsa ham raqam bor — o'tkazamiz
    return major is None or minor is None


# ─────────────────────────────────────────
# DAVOMAT BELGILASH
# ─────────────────────────────────────────


def group_has_lesson_today(group, day):
    """Guruh jadvali bo'yicha bugun dars bormi."""
    weekday = day.weekday()
    if group.schedule == "daily":
        return weekday != 6  # yakshanba dam
    if group.schedule == "odd":
        return weekday in ODD_DAYS
    if group.schedule == "even":
        return weekday in EVEN_DAYS
    return False


def student_group_for_today(student, day):
    """O'quvchining bugun darsi bor guruhi.

    Bir nechta guruhda bo'lsa bugun darsi borini tanlaymiz — aks
    holda ikkinchi guruhning davomati birinchisiga yozilib ketardi.
    """
    groups = list(student.groups.all())
    if not groups:
        return None
    today = [g for g in groups if group_has_lesson_today(g, day)]
    if not today:
        return None
    # Bir nechta bo'lsa — darsi eng erta boshlanadigani
    return min(today, key=lambda g: (g.lesson_time or timezone.now().time(), g.id))


def decide_status(group, when, grace_minutes=DEFAULT_GRACE_MINUTES):
    """Kelgan vaqtga qarab "Keldi" yoki "Kech keldi"."""
    lesson_time = group.lesson_time
    if not lesson_time:
        return "present"

    local = timezone.localtime(when)
    arrived = local.hour * 60 + local.minute
    starts = lesson_time.hour * 60 + lesson_time.minute
    return "present" if arrived <= starts + grace_minutes else "late"


def mark_attendance(student, info):
    """Yuz tanilgan o'quvchiga davomat qo'yadi.

    Qaytaradi: (status_kaliti, izoh, attendance_yoki_None).

    Coin berish mavjud `update_attendance` mantig'idan foydalanadi —
    ikki joyda ikki xil hisoblanib qolmasligi uchun.
    """
    # Aylanma importni oldini olish uchun shu yerda
    from .views import ATTENDANCE_REASON, apply_coin_transaction, get_attendance_coins_map

    when = info["happened_at"]
    day = timezone.localtime(when).date()

    group = student_group_for_today(student, day)
    if group is None:
        if not student.groups.exists():
            return "no_group", "O'quvchi guruhga biriktirilmagan", None
        return "no_lesson", "Bu guruhda bugun dars yo'q", None

    lesson, _ = Lesson.objects.get_or_create(
        group=group,
        date=day,
        defaults={"title": group.name, "teacher": group.teacher},
    )

    attendance, created = Attendance.objects.get_or_create(
        student=student, lesson=lesson, defaults={"status": "absent"}
    )

    new_status = decide_status(group, when)

    # Ustoz allaqachon qo'lda belgilagan bo'lsa ustidan yozmaymiz —
    # odam ko'rgani terminaldan ishonchliroq
    if not created and attendance.status != "absent":
        return (
            "already",
            f"Allaqachon belgilangan: {attendance.get_status_display()}",
            attendance,
        )

    coins = get_attendance_coins_map()
    old_status = attendance.status

    if old_status in coins:
        apply_coin_transaction(
            student,
            -coins[old_status],
            ATTENDANCE_REASON.get(old_status, "manual"),
            note=f"'{old_status}' bekor qilindi (yuz tanish)",
            attendance=attendance,
        )
    if new_status in coins:
        apply_coin_transaction(
            student,
            coins[new_status],
            ATTENDANCE_REASON.get(new_status, "manual"),
            note=f"Yuz tanish: {new_status}",
            attendance=attendance,
        )

    attendance.status = new_status
    attendance.save(update_fields=["status"])

    label = "Keldi" if new_status == "present" else "Kech keldi"
    return "marked", f"{group.name} — {label}", attendance


def handle_event(device, info):
    """Hodisani qayta ishlaydi va FaceEvent yozuvini qaytaradi."""
    student = None
    status, note, attendance = "unknown", "Bu raqam hech kimga bog'lanmagan", None

    if info["person_id"]:
        student = Student.objects.filter(
            face_person_id=info["person_id"]
        ).first()

    if student:
        try:
            status, note, attendance = mark_attendance(student, info)
        except Exception as exc:  # noqa: BLE001 — hodisa baribir yozilsin
            log.exception("Yuz tanish davomati belgilanmadi")
            status, note = "ignored", f"Xato: {exc}"[:255]

    event = FaceEvent.objects.create(
        device=device,
        person_id=info["person_id"][:32],
        person_name=info["person_name"],
        student=student,
        attendance=attendance,
        status=status,
        note=note[:255],
        happened_at=info["happened_at"],
    )

    if device:
        FaceDevice.objects.filter(pk=device.pk).update(last_event_at=timezone.now())

    return event


# ─────────────────────────────────────────
# YUZ RO'YXATGA OLISH (bot orqali)
# ─────────────────────────────────────────


def allocate_person_id(student):
    """O'quvchiga takrorlanmas terminal raqamini beradi.

    Raqami bo'lsa o'sha qaytariladi — bir marta berilgan raqam
    o'zgarmaydi, aks holda terminalda ikkita yozuv paydo bo'lardi.

    Asos sifatida o'quvchi ID si olinadi: ID lar avtomatik o'sadi va
    o'chirilgandan keyin ham qayta ishlatilmaydi, ya'ni raqam hech
    qachon ikkinchi odamga tushmaydi. Yagona to'qnashuv ehtimoli —
    o'sha raqam ilgari panelda qo'lda kiritilgan bo'lsa; o'shanda
    bo'shini topguncha yuqoriga suriladi.
    """
    if student.face_person_id:
        return student.face_person_id

    taken = set(
        Student.objects.exclude(face_person_id="")
        .exclude(id=student.id)
        .values_list("face_person_id", flat=True)
    )

    candidate = PERSON_ID_BASE + student.id
    while str(candidate) in taken:
        candidate += 1

    person_id = str(candidate)
    student.face_person_id = person_id
    try:
        with transaction.atomic():
            student.save(update_fields=["face_person_id"])
    except IntegrityError:
        # Ikki so'rov bir vaqtda kelib bir xil raqamni tanlagan. Bazadagi
        # unikal cheklov ushlab qoldi — qayta o'qib, bo'shidan davom
        # etamiz. `transaction.atomic` bo'lmasa Postgres'da tranzaksiya
        # buzilib, keyingi so'rovlar ham yiqilardi.
        student.refresh_from_db(fields=["face_person_id"])
        if student.face_person_id:
            return student.face_person_id
        return allocate_person_id(student)

    return person_id


def normalize_photo(raw):
    """Yuz rasmini terminal qabul qiladigan ko'rinishga keltiradi.

    Qaytaradi: (base64_jpeg, xato_matni). Xato bo'lsa birinchisi None
    va matn to'g'ridan-to'g'ri o'quvchiga yuboriladi — shuning uchun u
    tushunarli va nima qilish kerakligini aytadigan bo'lishi kerak.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover — pillow requirements'da bor
        return None, "Serverda rasm kutubxonasi yo'q — administratorga ayting"

    try:
        image = Image.open(io.BytesIO(raw))
        # Telefon suratida haqiqiy burilish EXIF ichida bo'ladi. Uni
        # qo'llamasak yonboshlagan yuz terminalga yotgan holda tushardi.
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
    except Exception:  # noqa: BLE001 — buzuq fayl ham shu yerga tushadi
        return None, "Rasmni o'qib bo'lmadi. Oddiy JPG rasm yuboring."

    width, height = image.size
    if min(width, height) < MIN_PHOTO_SIDE:
        return None, (
            f"Rasm juda kichik ({width}×{height}). Kamida "
            f"{MIN_PHOTO_SIDE} nuqta bo'lsin — yaqinroqdan qayta suratga oling."
        )

    aspect = max(width, height) / min(width, height)
    if not (MIN_ASPECT <= aspect <= MAX_ASPECT):
        return None, (
            f"Rasm o'lchami 4:3 emas ({width}×{height}).\n\n"
            "Telefon kamerasida o'lchamni «4:3» qilib qo'ying yoki rasmni "
            "kesib (crop) 4:3 ga keltiring. Kvadrat va cho'zinchoq (16:9) "
            "rasmlar yaramaydi — yuz juda kichik chiqadi."
        )

    # Kattasini kichraytiramiz: terminal katta rasmni rad etadi, kichigi
    # esa tanishga baribir yetarli
    if max(width, height) > MAX_PHOTO_SIDE:
        image.thumbnail((MAX_PHOTO_SIDE, MAX_PHOTO_SIDE), Image.LANCZOS)

    # 200 KB ga sig'guncha sifatni pasaytiramiz. Sifat 55 dan pastga
    # tushmaydi — undan keyin yuz "loyqa"lashib, terminal tanimay
    # qo'yadi; o'shanda o'lchamni kichraytirgan ma'qul.
    for quality in (88, 78, 68, 58):
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= MAX_PHOTO_BYTES:
            return base64.b64encode(data).decode(), None

    image.thumbnail((640, 640), Image.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=75, optimize=True)
    data = buf.getvalue()
    if len(data) > MAX_PHOTO_BYTES:
        return None, "Rasm juda og'ir — boshqa rasm yuboring."
    return base64.b64encode(data).decode(), None


def save_face_photo(student, raw):
    """Botdan kelgan rasmni o'quvchiga biriktiradi.

    Qaytaradi: (person_id, xato_matni). Xato bo'lsa birinchisi None.
    """
    photo_b64, error = normalize_photo(raw)
    if error:
        return None, error

    person_id = allocate_person_id(student)

    student.face_photo = photo_b64
    student.face_status = "pending"
    student.face_note = ""
    student.face_updated_at = timezone.now()
    student.save(
        update_fields=["face_photo", "face_status", "face_note", "face_updated_at"]
    )
    return person_id, None


def pending_students(device=None, with_photo=False):
    """Terminalga yozilishi kerak bo'lgan o'quvchilar.

    Rasmi bor va rad etilmagan har bir o'quvchi shu ro'yxatga tushadi;
    `device` berilsa faqat o'sha terminalga hali yozilmaganlari yoki
    yozilganidan keyin rasmini almashtirganlari qoladi.

    Rasmning o'zi so'ralmaguncha yuklanmaydi: har biri ~200 KB va
    ro'yxat ko'pincha faqat sanash uchun kerak bo'ladi — hammasini
    o'qish yuzlab o'quvchida serverni xotiraga cho'ktirardi.
    """
    qs = (
        Student.objects.exclude(face_photo="")
        .exclude(face_person_id="")
        .exclude(face_status="rejected")
        .order_by("id")
    )
    if not with_photo:
        qs = qs.defer("face_photo")
    if device is None:
        return list(qs)

    synced = {
        row["student_id"]: row["photo_at"]
        for row in FaceSync.objects.filter(device=device, ok=True).values(
            "student_id", "photo_at"
        )
    }

    pending = []
    for student in qs:
        was = synced.get(student.id)
        # Hech qachon yozilmagan, yoki yozilganidan keyin yangi rasm kelgan
        if was is None or (student.face_updated_at and student.face_updated_at > was):
            pending.append(student)
    return pending


def mark_synced(device, student, ok=True, error=""):
    """Terminalga yozilgani (yoki yozilmagani) qayd etiladi.

    Qaytaradi: xato o'quvchi uchun yangimi. Agent navbatni har necha
    daqiqada qayta oladi va o'sha xato takrorlanaveradi — shu bayroq
    bo'lmasa o'quvchiga bir xil ogohlantirish soatlab kelib turardi.
    """
    error = error[:255]
    FaceSync.objects.update_or_create(
        device=device,
        student=student,
        defaults={"photo_at": student.face_updated_at, "ok": ok, "error": error},
    )

    if ok:
        if student.face_status != "synced":
            student.face_status = "synced"
            student.face_note = ""
            student.save(update_fields=["face_status", "face_note"])
        return False

    if error and student.face_note != error:
        student.face_note = error
        student.save(update_fields=["face_note"])
        return True
    return False


def sync_device(device, students=None, notify=True):
    """Kutayotgan yuzlarni terminalga yozadi (ISAPI orqali).

    Faqat terminal manzili sozlangan bo'lsa ishlaydi. Qaytaradi:
    (yozildi, xato_bo'ldi, xabarlar_ro'yxati).
    """
    if not device.can_push:
        return 0, 0, ["Terminal manzili sozlanmagan"]

    if students is None:
        students = pending_students(device, with_photo=True)

    done = failed = 0
    notes = []
    for student in students:
        ok, message = push_student(device, student, student.face_photo)
        is_new_error = mark_synced(device, student, ok, "" if ok else message)
        if ok:
            done += 1
        else:
            failed += 1
            notes.append(f"{student}: {message}")
            if notify and is_new_error:
                notify_photo_rejected(student, message)
    return done, failed, notes


def notify_photo_rejected(student, reason):
    """Terminal rasmni qabul qilmasa o'quvchiga aytamiz.

    Aks holda u rasm yuborgan-u, davomat esa ishlamay turgan bo'lardi —
    va buni faqat coini kamayganda bilib qolardi.
    """
    from . import telegram as tg
    from .models import TelegramSubscriber

    text = (
        "⚠️ <b>Face ID rasmi qabul qilinmadi</b>\n\n"
        f"Sabab: {reason}\n\n"
        "Iltimos, yangi rasm yuboring: yuzingiz to'liq va yorug' ko'rinsin, "
        "ko'zoynak va bosh kiyimsiz, 4:3 o'lchamda."
    )
    for sub in TelegramSubscriber.objects.filter(student=student):
        try:
            tg.send_text(sub.chat_id, text)
        except Exception:  # noqa: BLE001 — xabar ketmasa ham sinx to'xtamasin
            log.exception("Yuz rad javobi ketmadi (chat=%s)", sub.chat_id)


# ─────────────────────────────────────────
# SERVER → TERMINAL (ISAPI)
# ─────────────────────────────────────────


# Hikvision xatolarining odam tushunadigan tarjimasi. Terminal
# `subStatusCode` da sababni aytadi — o'quvchiga «statusCode 6» emas,
# nima qilish kerakligi ko'rinsin.
ISAPI_ERRORS = {
    "employeeNoAlreadyExist": "Bu raqam terminalda allaqachon bor",
    "lowFaceQuality": "Yuz sifati past — yorug'roq joyda qayta suratga oling",
    "faceQualityLow": "Yuz sifati past — yorug'roq joyda qayta suratga oling",
    "noFaceDetected": "Rasmda yuz topilmadi",
    "detectNoFace": "Rasmda yuz topilmadi",
    "faceDetectFailed": "Rasmda yuz aniqlanmadi — yuzingiz to'liq ko'rinsin",
    "imageSizeExceedLimit": "Rasm hajmi terminal chegarasidan katta",
    "notSupport": "Terminal bu amalni qo'llab-quvvatlamaydi",
    "riskPassword": "Terminal parolini almashtirish talab qilinmoqda",
    "badAuthorization": "Login yoki parol noto'g'ri",
    "invalidContent": "Terminal so'rovni tushunmadi",
}


def _isapi_result(res):
    """ISAPI javobini o'qiydi. Qaytaradi: (muvaffaqiyat, xato_matni, kod).

    Hikvision xatoni ko'pincha HTTP 200 bilan, javob tanasida qaytaradi
    (`statusCode` 1 dan boshqa bo'ladi). Faqat `status_code` ga
    qarasak, «yuz topilmadi» degan javob ham "yozildi" bo'lib
    ko'rinardi — o'quvchi terminalda yo'q holda davomat kutib yurardi.

    Uchinchi qiymat — terminalning o'z kodi (`subStatusCode`). Xabar
    o'zbekchaga o'girilgani uchun uni matndan qidirib bo'lmaydi.
    """
    body = {}
    try:
        body = res.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}

    sub = str(body.get("subStatusCode") or "")
    status = body.get("statusCode")

    if res.status_code < 400 and (status in (None, 1) or status == "1"):
        return True, "", sub

    if sub:
        return False, ISAPI_ERRORS.get(sub, sub), sub

    text = (body.get("statusString") or res.text or "").strip()
    return False, f"Terminal rad etdi ({res.status_code}): {text[:120]}", ""


def _is_already_exists(code):
    """Terminal «bu raqam allaqachon bor» deyaptimi."""
    lowered = str(code).lower()
    return "alreadyexist" in lowered or "exist" in lowered


def push_student(device, student, photo_b64=""):
    """O'quvchini (va rasmi bo'lsa yuzini) terminalga yozadi.

    Faqat terminal manzili sozlangan bo'lsa ishlaydi. Qaytaradi:
    (muvaffaqiyat, xabar).
    """
    if not device.can_push:
        return False, "Terminal manzili sozlanmagan — yuzni terminalning o'zida yozing"
    if not student.face_person_id:
        return False, "O'quvchiga terminal raqami berilmagan"

    try:
        import requests
        from requests.auth import HTTPDigestAuth
    except ImportError:  # pragma: no cover
        return False, "requests kutubxonasi yo'q"

    auth = HTTPDigestAuth(device.username, device.password)
    base = device.host.rstrip("/")
    name = f"{student.name} {student.surname}".strip()[:32]

    user_info = {
        "UserInfo": {
            "employeeNo": student.face_person_id,
            "name": name,
            "userType": "normal",
            "Valid": {
                "enable": True,
                "beginTime": "2020-01-01T00:00:00",
                "endTime": "2035-12-31T23:59:59",
                "timeType": "local",
            },
        }
    }

    try:
        res = requests.post(
            f"{base}/ISAPI/AccessControl/UserInfo/Record?format=json",
            auth=auth,
            timeout=15,
            json=user_info,
        )
        ok, message, code = _isapi_result(res)
        if not ok:
            if not _is_already_exists(code):
                return False, message
            # Allaqachon bor — ismi o'zgargan bo'lishi mumkin, yangilaymiz.
            # `Record` ni takrorlash foydasiz: u har safar shu xatoni
            # qaytaraveradi va o'quvchi eski ism bilan qolib ketardi.
            res = requests.put(
                f"{base}/ISAPI/AccessControl/UserInfo/Modify?format=json",
                auth=auth,
                timeout=15,
                json=user_info,
            )
            ok, message, _code = _isapi_result(res)
            if not ok:
                return False, message
    except Exception as exc:  # noqa: BLE001
        return False, f"Terminalga ulanib bo'lmadi: {exc}"

    if not photo_b64:
        return True, "O'quvchi terminalga yozildi (rasm yuborilmadi)"

    try:
        image = base64.b64decode(photo_b64.split(",")[-1])
    except (ValueError, TypeError):
        return False, "Rasm formati noto'g'ri"

    try:
        res = requests.post(
            f"{base}/ISAPI/Intelligent/FDLib/FDSetUp?format=json",
            auth=auth,
            timeout=30,
            files={
                "FaceDataRecord": (
                    None,
                    json.dumps(
                        {
                            "faceLibType": "blackFD",
                            "FDID": "1",
                            "FPID": student.face_person_id,
                        }
                    ),
                    "application/json",
                ),
                "img": ("face.jpg", image, "image/jpeg"),
            },
        )
        ok, message, _code = _isapi_result(res)
        if not ok:
            return False, message
    except Exception as exc:  # noqa: BLE001
        return False, f"Yuz yuborilmadi: {exc}"

    return True, "O'quvchi va yuzi terminalga yozildi"
