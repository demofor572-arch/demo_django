"""Telegram bot yordamchi funksiyalari.

Bot oqimi:
  1. O'quvchi botga kirib /start bosadi
  2. Bot "telefon raqamni yuborish" tugmasini ko'rsatadi
  3. O'quvchi raqamini yuboradi -> bazadagi Student bilan bog'lanadi
  4. Manager sayt orqali xabar yuborsa, ulangan o'quvchilarga TG orqali boradi
"""

import logging
import re
import threading

import requests
from django.conf import settings

from .models import SentMessage, Student, TelegramSubscriber

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/{method}"

WELCOME_TEXT = (
    "Assalomu alaykum! 👋\n\n"
    "Bu ITLINE o'quv markazining rasmiy xabarlar boti.\n"
    "To'lov eslatmalari va e'lonlarni olish uchun quyidagi tugma orqali "
    "telefon raqamingizni yuboring 👇"
)
LINKED_TEXT = "✅ {name}, siz xabarlarga muvaffaqiyatli ulandingiz!"
NOT_FOUND_TEXT = (
    "❌ Bu raqam bazadan topilmadi.\n"
    "Iltimos, o'quv markazida ro'yxatdan o'tgan raqamingizni yuboring "
    "yoki administratorga murojaat qiling."
)
PENDING_TEXT = (
    "✅ Raqamingiz qabul qilindi!\n\n"
    "Siz hali bazada yo'qsiz. Administrator sizni ro'yxatga qo'shayotganda "
    "shu yerga tasdiqlash kodi keladi — kodni administratorga ayting."
)
CODE_TEXT = (
    "🔐 Ro'yxatdan o'tish kodi: {code}\n\n"
    "Bu kodni administratorga ayting. Kod 10 daqiqa amal qiladi.\n"
    "Agar siz ro'yxatdan o'tishni so'ramagan bo'lsangiz, e'tiborsiz qoldiring."
)


def tg_call(method, payload, timeout=15):
    """Telegram Bot API chaqiruvi."""
    url = API_URL.format(token=settings.TG_BOT_TOKEN, method=method)
    resp = requests.post(url, json=payload, timeout=timeout)
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Telegram API xatosi"))
    return data["result"]


def send_text(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return tg_call("sendMessage", payload)


def last9(phone):
    """Telefonning oxirgi 9 raqami (solishtirish uchun)."""
    d = re.sub(r"\D", "", str(phone or ""))
    return d[-9:] if len(d) >= 9 else ""


def find_student_by_phone(phone):
    """Telefon bo'yicha o'quvchini topadi (phone yoki phone2, faollarga ustunlik)."""
    target = last9(phone)
    if not target:
        return None
    match, grad_match = None, None
    qs = Student.objects.filter(is_admin=False, is_excellence=False).values(
        "id", "phone", "phone2", "is_graduate"
    )
    for s in qs:
        if last9(s["phone"]) == target or last9(s["phone2"]) == target:
            if s["is_graduate"]:
                grad_match = grad_match or s["id"]
            else:
                match = match or s["id"]
                break
    sid = match or grad_match
    return Student.objects.filter(id=sid).first() if sid else None


def student_login_info(student):
    """Saytga kirish ma'lumotlari matni: login (telefon) va parol.

    Import qilingan (paroli o'rnatilmagan) o'quvchining paroli — ism va
    familiyasi (login_student shu bilan tekshiradi). O'zi maxsus parol
    o'rnatgan bo'lsa — u hash ko'rinishida saqlanadi va ochib bo'lmaydi.
    """
    phone = student.phone or "—"
    if student.password:
        parol_qatori = (
            "🔒 Parol: siz o'rnatgan maxsus parol — xavfsizlik uchun uni "
            "ko'rsatib bo'lmaydi. Unutgan bo'lsangiz, administratorga murojaat qiling."
        )
    else:
        parol = f"{student.name} {student.surname}".strip()
        parol_qatori = f"🔒 Parol: {parol}  (ism va familiyangiz)"
    return (
        "🔑 Saytga kirish ma'lumotlaringiz:\n"
        f"📱 Login (telefon): {phone}\n"
        f"{parol_qatori}"
    )


def handle_update(update):
    """Webhook'dan kelgan update'ni qayta ishlaydi."""
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    contact = msg.get("contact")
    tg_name = " ".join(
        filter(None, [msg["chat"].get("first_name"), msg["chat"].get("last_name")])
    )

    try:
        if text.startswith("/start"):
            send_text(
                chat_id,
                WELCOME_TEXT,
                reply_markup={
                    "keyboard": [
                        [{"text": "📱 Telefon raqamni yuborish", "request_contact": True}]
                    ],
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                },
            )
            return

        phone = None
        # Faqat tugma orqali ulashilgan (Telegram tasdiqlagan) o'z raqami
        # bo'lsagina parolni ko'rsatamiz — qo'lda yozilgan begona raqam
        # orqali birovning parolini olishning oldini oladi.
        verified_own = False
        if contact:
            phone = contact.get("phone_number")
            sender_id = (msg.get("from") or {}).get("id")
            verified_own = (
                sender_id is not None and contact.get("user_id") == sender_id
            )
        elif re.sub(r"\D", "", text) and len(re.sub(r"\D", "", text)) >= 9:
            phone = text  # raqamni qo'lda yozgan bo'lsa ham qabul qilamiz

        if phone:
            student = find_student_by_phone(phone)
            # Bazada yo'q bo'lsa ham saqlaymiz — yangi o'quvchi qo'shilganda
            # tasdiqlash kodi shu chat'ga yuboriladi va avtomatik bog'lanadi
            TelegramSubscriber.objects.update_or_create(
                chat_id=chat_id,
                defaults={
                    "student": student,
                    "phone": last9(phone),
                    "tg_name": tg_name[:200],
                },
            )
            if student:
                linked_msg = LINKED_TEXT.format(
                    name=f"{student.name} {student.surname}".strip()
                )
                if verified_own:
                    linked_msg += "\n\n" + student_login_info(student)
                else:
                    linked_msg += (
                        "\n\nSaytga kirish ma'lumotlaringizni ko'rish uchun "
                        "'📱 Telefon raqamni yuborish' tugmasi orqali raqamingizni "
                        "yuboring."
                    )
                send_text(
                    chat_id,
                    linked_msg,
                    reply_markup={"remove_keyboard": True},
                )
            else:
                send_text(
                    chat_id,
                    PENDING_TEXT,
                    reply_markup={"remove_keyboard": True},
                )
            return

        # boshqa har qanday xabar
        send_text(
            chat_id,
            "Xabaringiz qabul qilindi. Ulanish uchun /start ni bosing.",
        )
    except Exception:
        logger.exception("TG update qayta ishlashda xato (chat_id=%s)", chat_id)


def _personalize(text, student, month=""):
    return (
        text.replace("{ism}", f"{student.name} {student.surname}".strip())
        .replace("{oy}", month or "")
    )


def send_to_students(students, text, kind, month=""):
    """O'quvchilar ro'yxatiga xabar yuboradi. Natija: (sent, failed, no_chat)."""
    students = list(students)
    subs = TelegramSubscriber.objects.filter(student__in=students)
    subs_by_student = {}
    for sub in subs:
        subs_by_student.setdefault(sub.student_id, []).append(sub)

    # Telefon bo'yicha ham qidiramiz: aka-uka bir xil ota-ona raqamini
    # ishlatganda ikkinchisining chat'i student'ga bog'lanmagan bo'ladi —
    # xabar baribir o'sha chat'ga yetib borishi kerak
    need_phone_lookup = [s for s in students if s.id not in subs_by_student]
    if need_phone_lookup:
        wanted = {}
        for s in need_phone_lookup:
            for p in (s.phone, s.phone2):
                key = last9(p)
                if key:
                    wanted.setdefault(key, []).append(s.id)
        if wanted:
            for sub in TelegramSubscriber.objects.filter(phone__in=wanted.keys()):
                for sid in wanted.get(sub.phone, []):
                    subs_by_student.setdefault(sid, []).append(sub)

    sent = failed = no_chat = 0
    logs = []
    for student in students:
        student_subs = subs_by_student.get(student.id)
        if not student_subs:
            no_chat += 1
            logs.append(
                SentMessage(
                    student=student, kind=kind, text=text, status="no_chat"
                )
            )
            continue
        body = _personalize(text, student, month)
        for sub in student_subs:
            try:
                send_text(sub.chat_id, body)
                sent += 1
                logs.append(
                    SentMessage(
                        student=student,
                        chat_id=sub.chat_id,
                        kind=kind,
                        text=body,
                        status="sent",
                    )
                )
            except Exception as e:
                failed += 1
                logs.append(
                    SentMessage(
                        student=student,
                        chat_id=sub.chat_id,
                        kind=kind,
                        text=body,
                        status="failed",
                        error=str(e)[:300],
                    )
                )
    SentMessage.objects.bulk_create(logs, batch_size=200)
    return sent, failed, no_chat


def send_to_students_async(students, text, kind, month=""):
    """Katta ro'yxat uchun fon oqimida yuborish."""
    students = list(students)
    t = threading.Thread(
        target=send_to_students, args=(students, text, kind, month), daemon=True
    )
    t.start()
