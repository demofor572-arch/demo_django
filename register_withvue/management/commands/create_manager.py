"""Terminal orqali menejer yaratadi.

Menejer qo'shish API'si supermenejer talab qiladi, supermenejer esa
mavjud menejerdan tayinlanadi — shu sababli bazada birorta menejer
bo'lmasa birinchisini yaratishning yo'li qolmasdi. Bu buyruq o'sha
holat uchun.

    python manage.py create_manager "Ism" +998901234567 --super

Parol berilmasa terminal so'raydi (ekranda ko'rinmaydi).
"""

from getpass import getpass

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError

from register_withvue.access import DEFAULT_PERMISSIONS
from register_withvue.models import Manager


def _key(phone):
    return "".join(ch for ch in str(phone or "") if ch.isdigit())[-9:]


class Command(BaseCommand):
    help = "Yangi menejer yaratadi (birinchi supermenejerni ochish uchun)"

    def add_arguments(self, parser):
        parser.add_argument("name", help="Ism")
        parser.add_argument("phone", help="Telefon raqam")
        parser.add_argument("--surname", default="", help="Familiya")
        parser.add_argument("--password", default="", help="Parol (berilmasa so'raladi)")
        parser.add_argument(
            "--super",
            dest="is_super",
            action="store_true",
            help="Darhol supermenejer qilib yaratish",
        )

    def handle(self, *args, **options):
        phone = options["phone"].strip()
        if len(_key(phone)) < 7:
            raise CommandError("Telefon raqam juda qisqa")

        target = _key(phone)
        if any(_key(m.phone) == target for m in Manager.objects.all()):
            raise CommandError(f"{phone} — bu raqamli menejer allaqachon bor")

        password = options["password"] or getpass("Parol: ")
        if not password:
            raise CommandError("Parol bo'sh bo'lishi mumkin emas")

        is_super = options["is_super"]
        manager = Manager.objects.create(
            name=options["name"].strip(),
            surname=options["surname"].strip(),
            phone=phone,
            password=make_password(password),
            is_super=is_super,
            # Supermenejerda vakolatlar ro'yxati tekshirilmaydi — unda
            # hammasi bor. Oddiy menejer standart to'plam bilan
            # boshlaydi, keyin supermenejer panel orqali o'zgartiradi.
            permissions=[] if is_super else list(DEFAULT_PERMISSIONS),
        )

        role = "supermenejer" if is_super else "menejer"
        self.stdout.write(
            self.style.SUCCESS(
                f"{manager.name} {manager.surname} ({manager.phone}) — {role} yaratildi"
            )
        )
        if not is_super:
            self.stdout.write(
                "Vakolat berish uchun supermenejer panelidan foydalaning."
            )
