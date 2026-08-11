"""Telefon raqamini bazadan butunlay olib tashlaydi.

Test raqamlarini tozalash uchun. Raqam sakkizta jadvalda uchraydi
(o'quvchi, ustoz, menejer, lead, bot obunasi, tasdiqlash kodi,
qurilma, harakatlar tarixi) — qo'lda birma-bir o'chirish esdan
chiqishi oson.

Raqam formati muhim emas: +998 95 588 23 45 ham, 955882345 ham bir xil
deb qaraladi (oxirgi 9 raqami solishtiriladi).

Ikki xil ish qiladi:

  * raqam yozuvning ASOSIY telefoni bo'lsa — yozuv o'chiriladi
    (o'quvchi bilan birga uning to'lovlari, davomati, coinlari ham
    ketadi — Django FK'lar bo'yicha o'zi tozalaydi)
  * faqat QO'SHIMCHA telefon (phone2) bo'lsa — o'sha maydon
    bo'shatiladi, yozuvning o'zi qoladi. Aks holda ota-onasining
    raqamini ulashgan aka-uka ham o'chib ketardi.

Harakatlar tarixida (ActivityLog) raqam maydoni bo'shatiladi — tarix
yozuvlarining o'zi saqlanadi.

ISHLATISH

    # avval nima o'chishini ko'rish (hech narsa o'zgarmaydi)
    python manage.py purge_phone 955882345 900562345

    # rostdan o'chirish
    python manage.py purge_phone 955882345 900562345 --yes

    # menejer hisobiga tegilmasin (o'zingizni o'chirib qo'ymaslik uchun)
    python manage.py purge_phone 955882345 --yes --keep-managers

⚠️ O'chirilgan ma'lumot qaytmaydi. Avval --yes'siz ishga tushiring.
"""

from django.contrib.admin.utils import NestedObjects
from django.core.management.base import BaseCommand, CommandError
from django.db import router, transaction

from register_withvue.models import (
    ActivityLog,
    Lead,
    LoginDevice,
    Manager,
    PhoneVerification,
    Student,
    Teacher,
    TelegramSubscriber,
)
from register_withvue.phones import MIN_KEY_LEN as MIN_LEN, phone_key as key

# (model, o'chiriladigan maydon, bo'shatiladigan qo'shimcha maydon)
TARGETS = [
    (Student, "phone", "phone2"),
    (Lead, "phone", "phone2"),
    (Teacher, "phone", None),
    (Manager, "phone", None),
    (TelegramSubscriber, "phone", None),
    (PhoneVerification, "phone", None),
    (LoginDevice, "phone", None),
]


class Command(BaseCommand):
    help = "Berilgan telefon raqam(lar)ini bazadan butunlay o'chiradi"

    def add_arguments(self, parser):
        parser.add_argument("phones", nargs="+", help="Telefon raqam(lar)i")
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Rostdan o'chirish. Berilmasa faqat ko'rsatadi.",
        )
        parser.add_argument(
            "--keep-managers",
            action="store_true",
            help="Menejer hisobiga tegmaslik",
        )

    def handle(self, *args, **options):
        targets = []
        for raw in options["phones"]:
            k = key(raw)
            if len(k) < MIN_LEN:
                raise CommandError(f"Raqam juda qisqa: {raw}")
            targets.append(k)

        apply = options["yes"]
        models = [
            t for t in TARGETS
            if not (options["keep_managers"] and t[0] is Manager)
        ]

        if not apply:
            self.stdout.write(
                self.style.WARNING(
                    "SINOV REJIMI — hech narsa o'chirilmaydi. "
                    "Rostdan o'chirish uchun --yes qo'shing.\n"
                )
            )

        with transaction.atomic():
            for k in targets:
                self._purge_one(k, models, apply)
            if not apply:
                transaction.set_rollback(True)

    def _purge_one(self, k, models, apply):
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== {k} ==="))
        touched = False

        for model, main_field, alt_field in models:
            label = model.__name__

            # Format har xil bo'lgani uchun SQL emas, Python'da solishtiramiz
            rows = [
                obj for obj in model.objects.all()
                if key(getattr(obj, main_field, "")) == k
            ]
            if rows:
                touched = True
                self._report(label, rows)
                if apply:
                    for obj in rows:
                        obj.delete()

            if not alt_field:
                continue

            alt_rows = [
                obj for obj in model.objects.all()
                if key(getattr(obj, alt_field, "")) == k
                and key(getattr(obj, main_field, "")) != k
            ]
            for obj in alt_rows:
                touched = True
                self.stdout.write(
                    f"  {label} #{obj.pk} ({obj}) — {alt_field} bo'shatiladi"
                )
                if apply:
                    setattr(obj, alt_field, "")
                    obj.save(update_fields=[alt_field])

        logs = ActivityLog.objects.all()
        log_ids = [lg.pk for lg in logs if key(lg.actor_phone) == k]
        if log_ids:
            touched = True
            self.stdout.write(
                f"  ActivityLog — {len(log_ids)} ta yozuvda raqam bo'shatiladi"
            )
            if apply:
                ActivityLog.objects.filter(pk__in=log_ids).update(actor_phone="")

        if not touched:
            self.stdout.write("  bazada topilmadi — qiladigan ish yo'q")

    def _report(self, label, rows):
        """Yozuv bilan birga nima ketishini ham ko'rsatadi."""
        for obj in rows:
            collector = NestedObjects(using=router.db_for_write(obj.__class__))
            collector.collect([obj])
            extra = {
                m._meta.verbose_name_plural or m.__name__: len(objs)
                for m, objs in collector.model_objs.items()
                if m is not obj.__class__
            }
            tail = (
                "  (birga ketadi: "
                + ", ".join(f"{name} {n} ta" for name, n in extra.items())
                + ")"
                if extra
                else ""
            )
            self.stdout.write(
                self.style.WARNING(f"  {label} #{obj.pk} ({obj}) — o'chiriladi{tail}")
            )
            # Jadvaldan kelgan yozuv o'chirilsa ham qaytib keladi:
            # har deployda load_sheet_data source="sheet" larni o'chirib
            # jadvaldan qayta yaratadi. Buni bilmasa odam o'chirdim deb
            # o'ylab yuradi, keyingi deployda esa raqam yana paydo bo'ladi.
            if getattr(obj, "source", "") == "sheet":
                self.stdout.write(
                    self.style.ERROR(
                        "      ⚠ bu yozuv Google Sheets'dan kelgan — keyingi "
                        "deployda qayta yaratiladi. Raqamni JADVALDAN ham "
                        "o'chiring, aks holda tozalash vaqtinchalik bo'ladi."
                    )
                )
