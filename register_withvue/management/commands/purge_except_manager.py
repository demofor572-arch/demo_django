"""Bazadagi barcha register_withvue ma'lumotlarini o'chiradi, faqat bitta menejerni saqlaydi.

Bu buyruq `Manager` jadvalidagi berilgan telefon raqamga mos keluvchi yagona
menejerni saqlab qoladi va boshqa barcha register_withvue modeli yozuvlarini
o'chiradi.

Ishlatish:
    python manage.py purge_except_manager 900562345

Sinov rejimi:
    python manage.py purge_except_manager 900562345

Rostan o'chirish:
    python manage.py purge_except_manager 900562345 --yes
"""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from register_withvue.models import Manager
from register_withvue.phones import MIN_KEY_LEN as MIN_LEN, phone_key as key


class Command(BaseCommand):
    help = (
        "Berilgan telefon raqamga mos keluvchi yagona menejerni saqlab qolib "
        "register_withvue ma'lumotlarining barchasini o'chiradi"
    )

    def add_arguments(self, parser):
        parser.add_argument("phone", help="Saqlab qolish kerak bo'lgan menejer telefoni")
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Ha, rostdan o'chirishni bajarish",
        )

    def handle(self, *args, **options):
        raw_phone = options["phone"].strip()
        keep_key = key(raw_phone)
        if len(keep_key) < MIN_LEN:
            raise CommandError(f"Telefon raqam juda qisqa: {raw_phone}")

        manager = next(
            (m for m in Manager.objects.all() if key(m.phone) == keep_key),
            None,
        )
        if not manager:
            raise CommandError(
                f"Bazada {raw_phone} raqamli menejer topilmadi."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Saqlanadi: {manager.name} {manager.surname} ({manager.phone})"
            )
        )

        if not options["yes"]:
            self.stdout.write(
                self.style.WARNING(
                    "SINOV REJIMI — hech narsa o'chirilmaydi. "
                    "Rostdan o'chirish uchun --yes qo'shing."
                )
            )

        with transaction.atomic():
            for model in sorted(
                apps.get_models(),
                key=lambda m: m._meta.label_lower,
            ):
                if model._meta.app_label != "register_withvue":
                    continue

                if model is Manager:
                    count = model.objects.exclude(pk=manager.pk).count()
                    self.stdout.write(
                        f"  Manager — {count} ta yozuv o'chiriladi (faqat {manager.phone} saqlanadi)"
                    )
                    if options["yes"] and count:
                        model.objects.exclude(pk=manager.pk).delete()
                    continue

                count = model.objects.count()
                self.stdout.write(f"  {model.__name__} — {count} ta yozuv o'chiriladi")
                if options["yes"] and count:
                    model.objects.all().delete()

            # ⚠️ Yuqoridagi tsikl SheetImportMeta'ni ham o'chiradi. Belgi
            # yo'q bo'lsa server keyingi ko'tarilishida "hali import
            # qilinmagan" deb o'ylab butun jadvalni qaytadan yuklaydi —
            # ya'ni hozir o'chirganimiz bir necha daqiqada qaytib keladi.
            # Shu sabab belgini joriy versiyada tiklab qo'yamiz.
            if options["yes"]:
                from register_withvue.management.commands.load_sheet_data import (
                    DATA_VERSION,
                )
                from register_withvue.models import SheetImportMeta

                SheetImportMeta.objects.update_or_create(
                    pk=1, defaults={"version": DATA_VERSION, "last_error": ""}
                )
                self.stdout.write(
                    f"  SheetImportMeta — v{DATA_VERSION} belgisi tiklandi "
                    "(qayta import bo'lmaydi)"
                )

            if not options["yes"]:
                transaction.set_rollback(True)
