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

from register_withvue.models import Student, Teacher


class Command(BaseCommand):
    help = "ADMIN_PASSWORD bilan yaratilgan ustoz parollarini tozalaydi"

    def handle(self, *args, **options):
        admin_password = settings.ADMIN_PASSWORD

        # Tozalanadigan parollar ikki xil:
        #   1. ADMIN_PASSWORD hashi — ustoz uni bilmaydi (qo'shish
        #      formasida parol maydoni yo'q edi).
        #   2. Bo'sh satr hashi — ADMIN_PASSWORD sozlanmaganda
        #      `make_password("")` shunday yozib qo'yardi. Bu "parol bor"
        #      hisoblanadi, lekin uni hech kim tera olmaydi: frontend bo'sh
        #      parolni yubormaydi. Bunday yozuv butunlay qulflangan bo'ladi.
        candidates = [p for p in (admin_password, "") if p is not None]

        def is_default(hashed):
            return any(check_password(p, hashed) for p in candidates)

        cleared_teachers = 0
        for teacher in Teacher.objects.exclude(password=""):
            if is_default(teacher.password):
                teacher.password = ""
                teacher.save(update_fields=["password"])
                cleared_teachers += 1
                self.stdout.write(f"Ustoz tozalandi: {teacher.name} ({teacher.phone})")

        # Ustozning panel profili (Student.is_admin) ham xuddi shu parol
        # bilan yaratilardi. U tozalanmasa login avval o'quvchi yozuviga
        # tushib, ism-familiya bilan kirishga yo'l bermay qolardi.
        cleared_admins = 0
        for student in Student.objects.filter(is_admin=True).exclude(password=""):
            if is_default(student.password):
                student.password = ""
                student.save(update_fields=["password"])
                cleared_admins += 1
                self.stdout.write(
                    f"Admin profili tozalandi: {student.name} {student.surname}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"{cleared_teachers} ta ustoz va {cleared_admins} ta admin "
                "profili paroli tozalandi — endi ism-familiya bilan kiriladi."
            )
        )
