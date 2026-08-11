"""Bitiruvchilarning telefon raqamlarini o'chiradi.

Bitiruvchidan faqat ism-familiya qoladi. Raqam bazada turgani uchun
o'sha odam qaytib kelganda "raqam allaqachon band" bo'lib ro'yxatdan
o'ta olmasdi — menejer paneli esa bitiruvchini yashirgani uchun sababi
ko'rinmasdi.

Raqam bilan birga uning qoldiqlari (bot ulanishi, tasdiqlash kodi,
qurilma) ham ketadi — lekin faqat o'sha raqamni boshqa hech kim
ishlatmayotgan bo'lsa.

Orqaga qaytarib bo'lmaydi: o'chirilgan raqamni tiklashning imkoni yo'q.
"""

from django.db import migrations


def clear(apps, schema_editor):
    Student = apps.get_model("register_withvue", "Student")
    TelegramSubscriber = apps.get_model("register_withvue", "TelegramSubscriber")
    PhoneVerification = apps.get_model("register_withvue", "PhoneVerification")
    LoginDevice = apps.get_model("register_withvue", "LoginDevice")

    import re

    def key(phone):
        digits = re.sub(r"\D", "", str(phone or ""))
        return digits[-9:] if len(digits) >= 9 else digits

    graduates = list(Student.objects.filter(is_graduate=True))
    if not graduates:
        return

    freed = {key(g.phone) for g in graduates} | {key(g.phone2) for g in graduates}
    freed.discard("")

    for grad in graduates:
        grad.phone = None
        grad.phone2 = ""
        grad.save(update_fields=["phone", "phone2"])

    # Raqam boshqa birov (o'quvchi/ustoz/menejer/lead) tomonidan hali
    # ishlatilayotgan bo'lsa qoldiqlariga tegmaymiz
    Teacher = apps.get_model("register_withvue", "Teacher")
    Manager = apps.get_model("register_withvue", "Manager")
    Lead = apps.get_model("register_withvue", "Lead")

    still_used = set()
    for s in Student.objects.filter(is_graduate=False):
        still_used.update({key(s.phone), key(s.phone2)})
    for t in Teacher.objects.all():
        still_used.add(key(t.phone))
    for m in Manager.objects.all():
        still_used.add(key(m.phone))
    for lead in Lead.objects.all():
        still_used.update({key(lead.phone), key(lead.phone2)})

    orphaned = freed - still_used
    if not orphaned:
        return

    for model in (TelegramSubscriber, PhoneVerification, LoginDevice):
        ids = [o.pk for o in model.objects.all() if key(o.phone) in orphaned]
        if ids:
            model.objects.filter(pk__in=ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("register_withvue", "0023_alter_student_phone"),
    ]

    operations = [
        # Qaytarish yo'q — o'chirilgan raqamni tiklab bo'lmaydi
        migrations.RunPython(clear, migrations.RunPython.noop),
    ]
