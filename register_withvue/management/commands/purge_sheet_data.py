"""Jadvaldan import qilingan (source="sheet") ma'lumotlarni butunlay o'chiradi.

Menejer qo'lda kiritgan yozuvlarga tegmaydi — faqat `load_sheet_data`
yaratgan o'quvchi, guruh, kurs, ustoz, to'lov, lead va reklamalarni
o'chiradi.

MUHIM: o'chirgandan keyin import versiyasi belgisi (SheetImportMeta)
DATA_VERSION qiymatida qoldiriladi. Belgi o'chib ketsa server keyingi
ko'tarilishida "hali import qilinmagan" deb o'ylab hammasini qaytadan
yuklaydi — aynan shu sabab eski ma'lumot o'chmayotgandek ko'rinardi.

Ishlatish (avval sinov rejimi — hech narsa o'chmaydi):
    python manage.py purge_sheet_data

Rostdan o'chirish:
    python manage.py purge_sheet_data --yes

Render'da (bepul rejada Shell yo'q, shuning uchun deploy orqali):
    1. Environment bo'limida PURGE_SHEET_DATA=1 qo'shing
    2. Deploy qiling — build vaqtida ma'lumot o'chadi
    3. PURGE_SHEET_DATA ni olib tashlang (aks holda har deployda ishlaydi)
"""

import os

from django.core.management.base import BaseCommand
from django.db import transaction

from register_withvue.management.commands.load_sheet_data import (
    DATA_VERSION,
    SOURCE,
)
from register_withvue.models import (
    AdChannel,
    Course,
    Group,
    Lead,
    Payment,
    SheetImportMeta,
    Student,
    Teacher,
)


ENV_FLAG = "PURGE_SHEET_DATA"


class Command(BaseCommand):
    help = "Jadvaldan import qilingan (source='sheet') ma'lumotlarni o'chiradi"

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Ha, rostdan o'chirilsin (busiz faqat sanaydi)",
        )
        parser.add_argument(
            "--env-flag",
            action="store_true",
            help=(
                f"Faqat {ENV_FLAG} muhit o'zgaruvchisi yoqilgan bo'lsa "
                "o'chiradi — Render'ning bepul rejasida Shell yo'q, "
                "shuning uchun deploy orqali bir marta ishga tushirish uchun"
            ),
        )

    def handle(self, *args, **options):
        confirmed = options["yes"]

        if options["env_flag"]:
            enabled = os.environ.get(ENV_FLAG, "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            if not enabled:
                # Har deployda ishlaydi — o'chirilmagan bo'lsa jimgina chiqadi
                return
            confirmed = True
            self.stdout.write(
                self.style.WARNING(
                    f"{ENV_FLAG} yoqilgan — import qilingan ma'lumot o'chirilmoqda.\n"
                    "⚠️ O'chirilgach bu o'zgaruvchini Render'da olib tashlang."
                )
            )

        counts = [
            ("To'lovlar", Payment.objects.filter(source=SOURCE)),
            ("O'quvchilar", Student.objects.filter(source=SOURCE)),
            ("Guruhlar", Group.objects.filter(source=SOURCE)),
            ("Kurslar", Course.objects.filter(source=SOURCE)),
            ("Ustozlar", Teacher.objects.filter(source=SOURCE)),
            ("Leadlar", Lead.objects.filter(source=SOURCE)),
            ("Reklama kanallari", AdChannel.objects.filter(source=SOURCE)),
        ]

        total = 0
        for label, qs in counts:
            n = qs.count()
            total += n
            self.stdout.write(f"  {label:20}: {n}")

        if not total:
            self.stdout.write(
                self.style.SUCCESS("Import qilingan ma'lumot topilmadi.")
            )
        elif not confirmed:
            self.stdout.write(
                self.style.WARNING(
                    f"\nSINOV REJIMI — {total} ta yozuv o'chirilishi kerak, "
                    "lekin hech narsa o'chirilmadi.\n"
                    "Rostdan o'chirish uchun: --yes"
                )
            )
            return

        with transaction.atomic():
            if confirmed:
                Payment.objects.filter(source=SOURCE).delete()
                Student.objects.filter(source=SOURCE).delete()
                Group.objects.filter(source=SOURCE).delete()
                # Group.course — PROTECT. Menejer qo'lda yaratgan guruh
                # sheet kursiga bog'langan bo'lsa, kursni o'chirish
                # ProtectedError berardi — avval bog'lanishni bo'shatamiz.
                Group.objects.filter(course__source=SOURCE).update(course=None)
                Course.objects.filter(source=SOURCE).delete()
                Teacher.objects.filter(source=SOURCE).delete()
                Lead.objects.filter(source=SOURCE).delete()
                AdChannel.objects.filter(source=SOURCE).delete()

            # Belgi shu versiyada qoladi — aks holda qayta import bo'ladi
            SheetImportMeta.objects.update_or_create(
                pk=1, defaults={"version": DATA_VERSION, "last_error": ""}
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n{total} ta import yozuvi o'chirildi. "
                f"Import belgisi v{DATA_VERSION} da qoldirildi — "
                "qayta import qilinmaydi."
            )
        )
