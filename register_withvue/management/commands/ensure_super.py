"""Deploy paytida supermenejerni env o'zgaruvchilaridan tayyorlaydi.

Render'ning bepul planida Shell yo'q, shuning uchun `make_super` ni
qo'lda ishga tushirib bo'lmaydi. Bu buyruq esa `buildCommand` ichida
har deployda o'zi ishlaydi va env'da ko'rsatilgan raqamni
supermenejer qilib qo'yadi.

Env o'zgaruvchilari (Render → Environment):

    SUPER_MANAGER_PHONE      +998901234567   (majburiy)
    SUPER_MANAGER_PASSWORD   ...             (faqat menejer hali yo'q bo'lsa)
    SUPER_MANAGER_NAME       Ism             (ixtiyoriy)
    SUPER_MANAGER_SURNAME    Familiya        (ixtiyoriy)

Buyruq idempotent — bir necha marta ishlasa ham zarari yo'q:
menejer allaqachon supermenejer bo'lsa hech narsa o'zgartirmaydi va
parolni qayta yozmaydi.

⚠️ Hech qachon xato bilan tugamaydi: bu yerdagi muammo tufayli butun
deploy yiqilib qolmasligi kerak — ogohlantirish chiqarib, 0 bilan
tugaydi.
"""

import os

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand

from register_withvue.models import Manager


def _key(phone):
    return "".join(ch for ch in str(phone or "") if ch.isdigit())[-9:]


class Command(BaseCommand):
    help = "SUPER_MANAGER_PHONE env'idagi raqamni supermenejer qiladi"

    def handle(self, *args, **options):
        try:
            self._run()
        except Exception as exc:  # deploy yiqilmasin
            self.stderr.write(
                self.style.WARNING(f"ensure_super o'tkazib yuborildi: {exc}")
            )

    def _run(self):
        phone = (os.environ.get("SUPER_MANAGER_PHONE") or "").strip()
        if not phone:
            self.stdout.write("SUPER_MANAGER_PHONE berilmagan — o'tkazib yuborildi")
            return

        target = _key(phone)
        if len(target) < 7:
            self.stderr.write(
                self.style.WARNING(f"SUPER_MANAGER_PHONE juda qisqa: {phone}")
            )
            return

        manager = next(
            (m for m in Manager.objects.all() if _key(m.phone) == target), None
        )

        if manager:
            if manager.is_super and manager.is_active:
                self.stdout.write(
                    f"{manager.name} allaqachon supermenejer — o'zgarish yo'q"
                )
                return
            manager.is_super = True
            manager.is_active = True
            manager.save(update_fields=["is_super", "is_active"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"{manager.name} {manager.surname} ({manager.phone}) — "
                    "supermenejer qilindi (paroli o'zgarmadi)"
                )
            )
            return

        # Bazada bunday menejer yo'q — parol berilgan bo'lsa yaratamiz
        password = os.environ.get("SUPER_MANAGER_PASSWORD") or ""
        if not password:
            self.stderr.write(
                self.style.WARNING(
                    f"{phone} — bunday menejer yo'q va SUPER_MANAGER_PASSWORD "
                    "berilmagan, shuning uchun yaratilmadi"
                )
            )
            return

        manager = Manager.objects.create(
            name=(os.environ.get("SUPER_MANAGER_NAME") or "Super").strip(),
            surname=(os.environ.get("SUPER_MANAGER_SURNAME") or "").strip(),
            phone=phone,
            password=make_password(password),
            is_super=True,
            # Supermenejerda vakolatlar tekshirilmaydi — unda hammasi bor
            permissions=[],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"{manager.name} ({manager.phone}) — supermenejer yaratildi. "
                "Endi SUPER_MANAGER_PASSWORD env'ini o'chirib tashlang."
            )
        )
