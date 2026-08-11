"""Telefon raqamini bazadan tozalash.

Raqam bitta jadvalda emas, bir nechtasida yotadi: o'quvchi/ustoz/
menejer/lead — egasi, TelegramSubscriber — bot ulanishi,
PhoneVerification — tasdiqlash kodi, LoginDevice — kirgan qurilma.

O'quvchi o'chirilganda qolganlari o'z holicha qolardi (FK'lar
`SET_NULL`), ya'ni raqam bazada yashirin qolib ketardi. Bu yerdagi
`forget_phone` o'sha qoldiqni tozalaydi.

⚠️ Muhim shart: raqamni hali kimdir ishlatayotgan bo'lsa tegilmaydi.
Aka-uka ota-onasining raqamini ulashadi — birini o'chirganda
ikkinchisining bot ulanishi uzilib qolmasligi kerak.
"""

import re

MIN_KEY_LEN = 7


def phone_key(phone):
    """Solishtirish uchun oxirgi 9 raqam ('+998 90 123 45 67' → '901234567')."""
    digits = re.sub(r"\D", "", str(phone or ""))
    return digits[-9:] if len(digits) >= 9 else digits


def owners(key, exclude_student_ids=()):
    """Shu raqamga egalik qilayotganlar — (model nomi, obyekt) ro'yxati."""
    from .models import Lead, Manager, Student, Teacher

    found = []
    for student in Student.objects.exclude(id__in=list(exclude_student_ids)):
        if key in (phone_key(student.phone), phone_key(student.phone2)):
            found.append(("Student", student))
    for teacher in Teacher.objects.all():
        if phone_key(teacher.phone) == key:
            found.append(("Teacher", teacher))
    for manager in Manager.objects.all():
        if phone_key(manager.phone) == key:
            found.append(("Manager", manager))
    for lead in Lead.objects.all():
        if key in (phone_key(lead.phone), phone_key(lead.phone2)):
            found.append(("Lead", lead))
    return found


def forget_phone(phone, exclude_student_ids=()):
    """Egasi qolmagan raqamning qoldiqlarini o'chiradi.

    `exclude_student_ids` — endigina o'chirilgan (yoki o'chirilayotgan)
    o'quvchilar: ular hali bazada turgan bo'lsa ham "egasi" deb
    hisoblanmaydi.

    Natija: {'tg': n, 'code': n, 'device': n} yoki egasi qolgan bo'lsa
    {'skipped': "..."}.
    """
    from .models import LoginDevice, PhoneVerification, TelegramSubscriber

    key = phone_key(phone)
    if len(key) < MIN_KEY_LEN:
        return {}

    still_used = owners(key, exclude_student_ids)
    if still_used:
        label, obj = still_used[0]
        return {"skipped": f"{label} #{obj.pk} hali shu raqamni ishlatyapti"}

    result = {}
    for model, name in (
        (TelegramSubscriber, "tg"),
        (PhoneVerification, "code"),
        (LoginDevice, "device"),
    ):
        ids = [
            obj.pk for obj in model.objects.all() if phone_key(obj.phone) == key
        ]
        if ids:
            model.objects.filter(pk__in=ids).delete()
            result[name] = len(ids)
    return result


def forget_student_phones(student_ids, phones):
    """O'chirilgan o'quvchilarning raqamlarini tozalaydi.

    `phones` — o'chirishdan OLDIN yig'ilgan raqamlar (o'chirilgandan
    keyin ularni obyektdan o'qib bo'lmaydi).
    """
    summary = {}
    for phone in {p for p in phones if p}:
        outcome = forget_phone(phone, exclude_student_ids=student_ids)
        for key, value in outcome.items():
            if isinstance(value, int):
                summary[key] = summary.get(key, 0) + value
    return summary
