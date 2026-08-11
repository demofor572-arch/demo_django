"""Menejer vakolatlari, supermenejer tekshiruvi va qurilma hisobi.

Loyihada sessiya/token autentifikatsiyasi yo'q — chaqiruvchi
'X-User-Phone' sarlavhasi orqali aniqlanadi (views.py'dagi
`_require_staff` bilan bir xil yondashuv). Qurilma esa brauzerda bir
marta yaratilib localStorage'da saqlanadigan 'X-Device-Id' bilan.

⚠️ Bu sarlavhalarni soxtalashtirish mumkin. Maqsad — vakolatlarni
ajratish va supermenejerga kirishlarni ko'rsatish, kriptografik himoya
emas. Haqiqiy himoya uchun token/sessiya alohida qo'shilishi kerak.
"""

import re

from django.http import JsonResponse
from django.utils import timezone

from .models import ActivityLog, LoginDevice, Manager

MIN_PHONE_KEY_LEN = 7


def phone_key(phone):
    """Telefonni solishtirish uchun normal ko'rinishga keltiradi."""
    d = re.sub(r"\D", "", str(phone or ""))
    if len(d) > 9 and d.startswith("998"):
        d = d[3:]
    return d[-9:] if len(d) >= 9 else d


def find_manager_by_phone(phone, active_only=True):
    """Menejerni telefon bo'yicha topadi — format farqiga qaramasdan."""
    qs = Manager.objects.filter(is_active=True) if active_only else Manager.objects.all()
    exact = qs.filter(phone=phone).first()
    if exact:
        return exact
    target = phone_key(phone)
    if len(target) < MIN_PHONE_KEY_LEN:
        return None
    for m in qs:
        if phone_key(m.phone) == target:
            return m
    return None


# ─────────────────────────────────────────
# VAKOLATLAR KATALOGI
# ─────────────────────────────────────────
#
# Har bir yozuv: (kalit, sarlavha, bo'lim). Supermenejer menejer
# qo'shayotganda shu ro'yxatni ko'radi va kerakligini belgilaydi.
# Yangi funksiya qo'shilsa — shu yerga ham qo'shiladi, panelda
# avtomatik chiqadi.

PERMISSIONS = [
    # To'lovlar
    ("payments.view", "To'lovlarni ko'rish", "To'lovlar"),
    ("payments.edit", "To'lov summasi va holatini o'zgartirish", "To'lovlar"),
    ("payments.generate", "Oylik to'lovlarni yaratish", "To'lovlar"),
    ("payments.discount", "Chegirma berish", "To'lovlar"),
    ("payments.requests", "To'lov so'rovlarini qabul/rad qilish", "To'lovlar"),
    ("payments.settings", "To'lov kartasini o'zgartirish", "To'lovlar"),
    # Kassa — kunlik smena (kassir)
    ("cash.view", "O'z kunlik kassasini ko'rish", "Kassa"),
    ("cash.close", "Kunlik kassani topshirish", "Kassa"),
    # O'quvchilar
    ("students.view", "O'quvchilar ro'yxatini ko'rish", "O'quvchilar"),
    ("students.add", "O'quvchi qo'shish", "O'quvchilar"),
    ("students.edit", "O'quvchi ma'lumotini tahrirlash", "O'quvchilar"),
    ("students.delete", "O'quvchini o'chirish", "O'quvchilar"),
    ("students.transfer", "O'quvchini boshqa ustozga ko'chirish", "O'quvchilar"),
    # Ustozlar
    ("teachers.view", "Ustozlar ro'yxatini ko'rish", "Ustozlar"),
    ("teachers.add", "Ustoz qo'shish", "Ustozlar"),
    ("teachers.edit", "Ustoz ma'lumotini tahrirlash", "Ustozlar"),
    ("teachers.delete", "Ustozni o'chirish", "Ustozlar"),
    # Menejerlar — yangi menejer yaratish faqat supermenejerda,
    # bu yerdagilari mavjud yozuvlarni ko'rish/tahrirlash uchun
    ("managers.view", "Menejerlar ro'yxatini ko'rish", "Menejerlar"),
    ("managers.edit", "Menejer raqamini tahrirlash va o'chirish", "Menejerlar"),
    # Guruhlar va darslar
    ("groups.view", "Guruhlarni ko'rish", "Guruhlar"),
    ("groups.edit", "Guruh yaratish va tahrirlash", "Guruhlar"),
    ("groups.delete", "Guruhni o'chirish", "Guruhlar"),
    ("attendance.view", "Davomatni ko'rish", "Guruhlar"),
    ("attendance.edit", "Davomat belgilash", "Guruhlar"),
    # Kurslar
    ("courses.view", "Kurslarni ko'rish", "Kurslar"),
    ("courses.edit", "Kurs va narxlarni o'zgartirish", "Kurslar"),
    # Do'kon va coinlar
    ("shop.products", "Mahsulotlarni boshqarish", "Do'kon"),
    ("shop.orders", "Buyurtmalarni tasdiqlash", "Do'kon"),
    ("coins.settings", "Coin sozlamalarini o'zgartirish", "Do'kon"),
    ("coins.give", "Qo'lda coin berish", "Do'kon"),
    # Aloqa
    ("messages.send", "O'quvchilarga telegram xabar yuborish", "Aloqa"),
    ("messages.teachers", "Ustozlarga xabar yuborish", "Aloqa"),
    ("messages.leads", "Leadlarga reklama yuborish", "Aloqa"),
    ("news.manage", "Yangiliklarni boshqarish", "Aloqa"),
    ("receipt.settings", "To'lov cheki matnini sozlash", "Aloqa"),
    # Hisobot
    ("history.view", "To'lovlar tarixini ko'rish", "Hisobot"),
    ("database.view", "Baza (leadlar, bitiruvchilar) ko'rish", "Hisobot"),
]

PERMISSION_KEYS = {key for key, _label, _section in PERMISSIONS}

# Yangi menejer uchun standart to'plam — kundalik ish uchun yetarli,
# o'chirish/ko'chirish kabi qaytarib bo'lmaydigan amallar kirmaydi.
DEFAULT_PERMISSIONS = [
    "payments.view",
    "payments.edit",
    "payments.requests",
    "cash.view",
    "cash.close",
    "students.view",
    "teachers.view",
    "groups.view",
    "attendance.view",
    "attendance.edit",
    "courses.view",
    "history.view",
]


def permission_catalog():
    """Frontend uchun bo'limlarga ajratilgan vakolatlar ro'yxati."""
    sections = {}
    for key, label, section in PERMISSIONS:
        sections.setdefault(section, []).append({"key": key, "label": label})
    return [
        {"section": name, "items": items} for name, items in sections.items()
    ]


def clean_permissions(value):
    """Kelgan ro'yxatdan faqat mavjud kalitlarni qoldiradi."""
    if not isinstance(value, list):
        return []
    seen, result = set(), []
    for key in value:
        if key in PERMISSION_KEYS and key not in seen:
            seen.add(key)
            result.append(key)
    return result


# ─────────────────────────────────────────
# TEKSHIRUVLAR
# ─────────────────────────────────────────


def caller_phone(request):
    return (request.headers.get("X-User-Phone") or "").strip()


def caller_manager(request):
    """Chaqiruvchi menejer bo'lsa — o'sha obyekt, aks holda None."""
    phone = caller_phone(request)
    if not phone:
        return None
    return find_manager_by_phone(phone)


def require_super(request):
    """Faqat supermenejerga ruxsat. Mos kelsa None, aks holda 403."""
    manager = caller_manager(request)
    if manager and manager.is_super:
        return None
    return JsonResponse(
        {"error": "Bu bo'lim faqat supermenejer uchun"}, status=403
    )


def require_permission(request, key):
    """Menejerda shu vakolat bormi. Supermenejerda hammasi bor.

    Menejer bo'lmagan chaqiruvchilar (ustoz/admin o'quvchi) bu
    tekshiruvdan o'tadi — ularning cheklovi alohida `_require_staff`
    bilan hal qilinadi, vakolatlar tizimi menejerlarga tegishli.
    """
    manager = caller_manager(request)
    if manager is None:
        return None
    if manager.has_perm(key):
        return None
    return JsonResponse(
        {"error": "Bu amal uchun vakolatingiz yo'q"}, status=403
    )


# ─────────────────────────────────────────
# QURILMALAR
# ─────────────────────────────────────────


def device_id(request):
    return (request.headers.get("X-Device-Id") or "").strip()[:64]


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def is_device_blocked(request, phone):
    """Shu qurilma+foydalanuvchi juftligi bloklanganmi."""
    did = device_id(request)
    if not did:
        return False
    return LoginDevice.objects.filter(
        device_id=did, phone=phone, is_blocked=True
    ).exists()


# ─────────────────────────────────────────
# HARAKATLAR JURNALI
# ─────────────────────────────────────────
#
# Kalit → o'zbekcha nom. Frontend filtrida shu ro'yxat ko'rsatiladi.

ACTIONS = {
    "payment.confirm": "To'lov tasdiqlandi",
    "payment.update": "To'lov summasi o'zgartirildi",
    "payment.discount": "Chegirma berildi",
    "payment.generate": "Oylik to'lovlar yaratildi",
    "payment.request_accept": "To'lov so'rovi qabul qilindi",
    "payment.request_reject": "To'lov so'rovi rad etildi",
    "payment.settings": "To'lov kartasi o'zgartirildi",
    "student.create": "O'quvchi qo'shildi",
    "student.update": "O'quvchi tahrirlandi",
    "student.delete": "O'quvchi o'chirildi",
    "student.transfer": "O'quvchi ko'chirildi",
    "teacher.create": "Ustoz qo'shildi",
    "teacher.update": "Ustoz tahrirlandi",
    "teacher.delete": "Ustoz o'chirildi",
    "group.create": "Guruh yaratildi",
    "group.update": "Guruh tahrirlandi",
    "group.delete": "Guruh o'chirildi",
    "course.create": "Kurs yaratildi",
    "course.update": "Kurs tahrirlandi",
    "course.delete": "Kurs o'chirildi",
    "attendance.update": "Davomat belgilandi",
    "coins.give": "Coin berildi",
    "message.send": "Telegram xabar yuborildi",
    "news.create": "Yangilik joylandi",
    "news.update": "Yangilik tahrirlandi",
    "news.delete": "Yangilik o'chirildi",
    "order.resolve": "Buyurtma hal qilindi",
    "expense.create": "Xarajat qo'shildi",
    "expense.delete": "Xarajat o'chirildi",
    "manager.create": "Menejer yaratildi",
    "manager.update": "Menejer tahrirlandi",
    "manager.delete": "Menejer o'chirildi",
    "manager.permissions": "Vakolatlar o'zgartirildi",
    "manager.password": "Menejer paroli almashtirildi",
    "salary.pay": "Ustoz oyligi to'landi",
    "salary.unpay": "Oylik to'lovi bekor qilindi",
    "salary.advance": "Ustozga avans berildi",
    "salary.settings": "Oylik sozlamasi o'zgartirildi",
    "device.block": "Qurilma bloklandi",
    "faceid.device": "Yuz tanish terminali o'zgartirildi",
    "faceid.link": "O'quvchi terminalga bog'landi",
    "faceid.push": "O'quvchi terminalga yuborildi",
    "shop.product": "Mahsulot qo'shildi",
    "message.leads": "Leadlarga reklama yuborildi",
    "message.teachers": "Ustozlarga xabar yuborildi",
    "receipt.settings": "Chek matni o'zgartirildi",
    "lead.delete": "Lead o'chirildi",
}


def action_catalog():
    return [{"key": key, "label": label} for key, label in ACTIONS.items()]


def describe_caller(request):
    """Chaqiruvchi kimligini aniqlaydi: (ism, rol, manager|None).

    Menejer bo'lmasa Teacher/Student jadvallaridan qidiriladi. Topilmasa
    telefon raqamning o'zi ism o'rnida ishlatiladi — jurnal baribir
    kimdir nimadir qilganini ko'rsatishi kerak.
    """
    manager = caller_manager(request)
    if manager:
        role = "super" if manager.is_super else "manager"
        return (f"{manager.name} {manager.surname}".strip(), role, manager)

    phone = caller_phone(request)
    if not phone:
        return ("", "", None)

    # Aylanma import bo'lmasligi uchun shu yerda
    from .models import Student, Teacher

    target = phone_key(phone)
    if len(target) >= MIN_PHONE_KEY_LEN:
        for t in Teacher.objects.only("id", "name", "phone"):
            if phone_key(t.phone) == target:
                return (t.name, "teacher", None)
        for s in Student.objects.only("id", "name", "surname", "phone", "is_admin"):
            if phone_key(s.phone) == target:
                role = "teacher" if s.is_admin else "student"
                return (f"{s.name} {s.surname}".strip(), role, None)
    return (phone, "", None)


def log_action(
    request,
    action,
    description,
    *,
    target_type="",
    target_id=None,
    target_name="",
    **meta,
):
    """Amalni jurnalga yozadi.

    ⚠️ Hech qachon xato otmaydi — jurnal yozilmagani uchun asosiy amal
    buzilib qolmasligi kerak.
    """
    try:
        name, role, manager = describe_caller(request)
        ActivityLog.objects.create(
            actor_phone=caller_phone(request)[:20],
            actor_name=name[:200],
            actor_role=role,
            manager=manager,
            action=action[:50],
            description=str(description)[:300],
            target_type=str(target_type)[:30],
            target_id=target_id,
            target_name=str(target_name)[:200],
            meta=meta or {},
            ip=client_ip(request),
        )
    except Exception:  # noqa: BLE001 — jurnal asosiy amalni to'smasin
        import logging

        logging.exception("ActivityLog yozilmadi: %s", action)


def log_attendance(request, *, lesson_id, group_name, date_label):
    """Davomat belgilashni jurnalga yozadi — dars bo'yicha bitta yozuv.

    Davomat har o'quvchi uchun alohida so'rov bilan belgilanadi. Har
    bosishni alohida yozsak, jurnal 100+ bir xil qatordan iborat bo'lib
    qolardi. Shuning uchun bitta dars uchun bitta yozuv ochiladi va
    keyingi belgilashlar o'sha yozuvning hisoblagichini oshiradi.
    """
    try:
        name, role, manager = describe_caller(request)
        phone = caller_phone(request)[:20]

        existing = ActivityLog.objects.filter(
            action="attendance.update",
            target_type="lesson",
            target_id=lesson_id,
            actor_phone=phone,
        ).first()

        if existing:
            count = int(existing.meta.get("count", 1)) + 1
            existing.meta = {**existing.meta, "count": count}
            existing.description = (
                f"«{group_name}» — {date_label} davomati belgilandi "
                f"({count} o'quvchi)"
            )
            existing.save(update_fields=["meta", "description"])
            return

        ActivityLog.objects.create(
            actor_phone=phone,
            actor_name=name[:200],
            actor_role=role,
            manager=manager,
            action="attendance.update",
            description=(
                f"«{group_name}» — {date_label} davomati belgilandi (1 o'quvchi)"
            ),
            target_type="lesson",
            target_id=lesson_id,
            target_name=group_name[:200],
            meta={"count": 1},
            ip=client_ip(request),
        )
    except Exception:  # noqa: BLE001
        import logging

        logging.exception("Davomat jurnaliga yozilmadi")


def touch_presence(request):
    """Foydalanuvchi hali saytda ekanini belgilaydi.

    Frontend vaqti-vaqti bilan chaqiradi. Yangi yozuv yaratmaydi —
    faqat login paytida yaratilgan qurilmaning `last_seen` vaqtini
    surib qo'yadi. Shu sababli "onlayn" ro'yxatiga faqat haqiqatan
    kirgan odamlar tushadi.
    """
    did, phone = device_id(request), caller_phone(request)
    if not did or not phone:
        return False
    updated = LoginDevice.objects.filter(
        device_id=did, phone=phone, is_blocked=False
    ).update(last_seen=timezone.now())
    return bool(updated)


def record_login(request, *, phone, role, user_name="", manager=None):
    """Muvaffaqiyatli loginni yozib qo'yadi.

    Qurilma ID bo'lmasa (eski frontend yoki bot) hech narsa yozilmaydi —
    aks holda barcha kirishlar bitta bo'sh ID ostida qo'shilib ketardi.
    """
    did = device_id(request)
    if not did or not phone:
        return None

    device, _created = LoginDevice.objects.get_or_create(
        device_id=did,
        phone=phone,
        defaults={"role": role, "manager": manager},
    )
    device.role = role
    device.manager = manager
    device.user_name = (user_name or "")[:200]
    device.user_agent = (request.headers.get("User-Agent") or "")[:400]
    device.ip = client_ip(request)
    device.login_count = (device.login_count or 0) + 1
    device.last_seen = timezone.now()
    device.save()
    return device
