"""Eski xato bilan yaratilgan ustozlarning parolini tozalaydi.

`create_teacher` ilgari har bir yangi ustozga ADMIN_PASSWORD bilan
hech qachon ishlamaydigan parol o'rnatardi (frontendda parol maydoni
yo'q, ustoz ism-familiya bilan kirishni kutadi). Bu buyruq ADMIN_PASSWORD
bilan yaratilgan (ya'ni ustozning o'zi hech qachon o'rnatmagan) eski
parollarni tozalaydi — shundan keyin ular login_student() dagi
"ism-familiya" yo'li orqali kira oladi.

Ishlatish: python manage.py clear_teacher_default_passwords
"""

from django.contrib.auth.hashers import check_password
from django.conf import settings
from django.core.management.base import BaseCommand

from register_withvue.models import Teacher


class Command(BaseCommand):
    help = "ADMIN_PASSWORD bilan yaratilgan ustoz parollarini tozalaydi"

    def handle(self, *args, **options):
        admin_password = settings.ADMIN_PASSWORD
        if not admin_password:
            self.stdout.write(
                self.style.WARNING(
                    "ADMIN_PASSWORD sozlanmagan — tekshirish uchun kerak, "
                    "hech narsa o'zgartirilmadi."
                )
            )
            return

        cleared = 0
        for teacher in Teacher.objects.exclude(password=""):
            if check_password(admin_password, teacher.password):
                teacher.password = ""
                teacher.save(update_fields=["password"])
                cleared += 1
                self.stdout.write(f"Tozalandi: {teacher.name} ({teacher.phone})")

        self.stdout.write(
            self.style.SUCCESS(f"{cleared} ta ustoz paroli tozalandi.")
        )
