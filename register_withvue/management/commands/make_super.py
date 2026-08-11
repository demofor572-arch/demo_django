"""Mavjud menejerni supermenejer qiladi.

    python manage.py make_super +998901234567

Raqam qanday formatda yozilsa ham topiladi. `--off` bilan aksincha —
supermenejerlikni olib tashlaydi.
"""

from django.core.management.base import BaseCommand, CommandError

from register_withvue.models import Manager


def _key(phone):
    return "".join(ch for ch in str(phone or "") if ch.isdigit())[-9:]


class Command(BaseCommand):
    help = "Menejerni telefon raqami bo'yicha supermenejer qiladi"

    def add_arguments(self, parser):
        parser.add_argument("phone", help="Menejerning telefon raqami")
        parser.add_argument(
            "--off",
            action="store_true",
            help="Supermenejerlikni olib tashlash",
        )

    def handle(self, *args, **options):
        target = _key(options["phone"])
        if len(target) < 7:
            raise CommandError("Telefon raqam juda qisqa")

        manager = next(
            (m for m in Manager.objects.all() if _key(m.phone) == target), None
        )
        if not manager:
            raise CommandError(f"{options['phone']} — bunday menejer topilmadi")

        manager.is_super = not options["off"]
        if manager.is_super:
            manager.is_active = True
        manager.save(update_fields=["is_super", "is_active"])

        state = "supermenejer" if manager.is_super else "oddiy menejer"
        self.stdout.write(
            self.style.SUCCESS(
                f"{manager.name} {manager.surname} ({manager.phone}) — endi {state}"
            )
        )
