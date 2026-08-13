"""Menejer qo'shgan ustozning tizimga kira olishi bilan bog'liq testlar.

Regressiya: menejer panelidan ustoz qo'shilardi, lekin o'sha ustoz
hech qachon kira olmasdi. Ikki sabab bor edi:

  1. Login formasi avval raqamni parolsiz tekshiradi. `login_student`
     bu bosqichda faqat `Student` jadvalini ko'rardi — faqat `Teacher`
     yozuvi bo'lgan ustoz "topilmadi" bo'lib, parol maydoni umuman
     ochilmasdi.
  2. `create_teacher` parolga ADMIN_PASSWORD ni yozardi. Qo'shish
     formasida parol maydoni yo'q, ya'ni ustozga nima yozishini hech
     kim aytmasdi; ADMIN_PASSWORD berilmagan bo'lsa esa parol umuman
     mavjud bo'lmasdi.
"""

import json

from django.test import Client, TestCase

from .models import Manager, Student, Teacher


class TeacherPhoneCheckTests(TestCase):
    """Parolsiz tekshiruv ustozni ham topishi kerak."""

    def setUp(self):
        self.teacher = Teacher.objects.create(name="Salim", phone="90 123 45 67")

    def _check(self, phone):
        return Client().post(
            "/api/login/",
            data=json.dumps({"phone": phone, "password": None}),
            content_type="application/json",
        )

    def test_phone_check_finds_a_teacher_only_record(self):
        res = self._check("901234567")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(
            res.json().get("exists"),
            "ustoz topilmadi — login formasida parol maydoni ochilmaydi",
        )

    def test_phone_check_still_finds_students(self):
        Student.objects.create(name="Ali", surname="Valiyev", phone="911112233")

        self.assertTrue(self._check("911112233").json().get("exists"))

    def test_unknown_number_is_still_not_found(self):
        self.assertFalse(self._check("935556677").json().get("exists"))


class TeacherLoginTests(TestCase):
    """Yangi qo'shilgan ustoz ismi bilan kira olishi kerak."""

    def _login(self, phone, password):
        return Client().post(
            "/api/login/",
            data=json.dumps({"phone": phone, "password": password}),
            content_type="application/json",
        )

    def test_a_teacher_without_a_password_logs_in_with_their_name(self):
        """Importdan kelgan o'quvchilardagi qoida ustozga ham tegishli."""
        Teacher.objects.create(name="Toshtemirov Husniddin", phone="93 705 55 66")

        res = self._login("937055566", "Toshtemirov Husniddin")

        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertTrue(body["exists"])
        self.assertEqual(body["role"], "teacher")
        self.assertEqual(body["name"], "Toshtemirov Husniddin")

    def test_the_name_may_be_typed_in_any_order_or_case(self):
        Teacher.objects.create(name="Toshtemirov Husniddin", phone="93 705 55 66")

        for typed in ("husniddin toshtemirov", "TOSHTEMIROVHUSNIDDIN"):
            with self.subTest(typed=typed):
                self.assertEqual(self._login("937055566", typed).status_code, 200)

    def test_a_teacher_with_a_password_logs_in_with_it(self):
        from django.contrib.auth.hashers import make_password

        Teacher.objects.create(
            name="Aziz", phone="90 861 11 51", password=make_password("sirli-parol")
        )

        self.assertEqual(self._login("908611151", "sirli-parol").status_code, 200)

    def test_a_teacher_with_a_password_cannot_be_bypassed_with_their_name(self):
        """Parol o'rnatilgan bo'lsa ism bilan kirib bo'lmaydi."""
        from django.contrib.auth.hashers import make_password

        Teacher.objects.create(
            name="Aziz", phone="90 861 11 51", password=make_password("sirli-parol")
        )

        self.assertEqual(self._login("908611151", "Aziz").status_code, 401)

    def test_a_wrong_name_is_rejected(self):
        Teacher.objects.create(name="Salim", phone="90 123 45 67")

        self.assertEqual(self._login("901234567", "Kimdir Boshqa").status_code, 401)

    def test_an_empty_password_is_rejected(self):
        """Parolsiz ustozda bo'sh satr "to'g'ri" bo'lib qolmasin."""
        Teacher.objects.create(name="Salim", phone="90 123 45 67")

        self.assertEqual(self._login("901234567", "").status_code, 401)

    def test_a_student_profile_still_wins_over_the_teacher_row(self):
        """Ustozning panel profili (is_admin o'quvchi) avval tekshiriladi."""
        from django.contrib.auth.hashers import make_password

        Teacher.objects.create(name="Aziz Azizov", phone="90 861 11 51")
        Student.objects.create(
            name="Aziz",
            surname="Azizov",
            phone="90 861 11 51",
            is_admin=True,
            password=make_password("student-parol"),
        )

        body = self._login("908611151", "student-parol").json()
        self.assertTrue(body["is_admin"])


class ClearDefaultPasswordsTests(TestCase):
    """Renderdagi mavjud ustozlarni qulfdan chiqaruvchi buyruq.

    Ular `create_teacher`/`load_sheet_data` tomonidan hech kim tera
    olmaydigan parol bilan yaratilgan — buyruq shu parollarni tozalaydi.
    """

    def _run(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("clear_teacher_default_passwords", stdout=out)
        return out.getvalue()

    def _login(self, phone, password):
        return Client().post(
            "/api/login/",
            data=json.dumps({"phone": phone, "password": password}),
            content_type="application/json",
        )

    def test_a_teacher_locked_behind_the_admin_password_is_freed(self):
        from django.contrib.auth.hashers import make_password
        from django.test import override_settings

        with override_settings(ADMIN_PASSWORD="admin-siri"):
            Teacher.objects.create(
                name="Eski Ustoz",
                phone="905556677",
                password=make_password("admin-siri"),
            )
            self.assertEqual(self._login("905556677", "Eski Ustoz").status_code, 401)

            self._run()

        self.assertEqual(self._login("905556677", "Eski Ustoz").status_code, 200)

    def test_a_teacher_locked_behind_an_empty_password_is_freed(self):
        """ADMIN_PASSWORD sozlanmaganda make_password("") yozilardi."""
        from django.contrib.auth.hashers import make_password
        from django.test import override_settings

        Teacher.objects.create(
            name="Eski Ustoz", phone="905556677", password=make_password("")
        )
        self.assertEqual(self._login("905556677", "Eski Ustoz").status_code, 401)

        with override_settings(ADMIN_PASSWORD=""):
            self._run()

        self.assertEqual(self._login("905556677", "Eski Ustoz").status_code, 200)

    def test_the_linked_admin_profile_is_cleared_too(self):
        from django.contrib.auth.hashers import make_password
        from django.test import override_settings

        with override_settings(ADMIN_PASSWORD="admin-siri"):
            teacher = Teacher.objects.create(
                name="Eski Ustoz",
                phone="905556677",
                password=make_password("admin-siri"),
            )
            Student.objects.create(
                name="Eski",
                surname="Ustoz",
                phone="905556677",
                is_admin=True,
                teacher=teacher,
                password=make_password("admin-siri"),
            )

            self._run()

        self.assertEqual(
            Student.objects.get(is_admin=True).password,
            "",
            "admin profili qulflangan qoldi",
        )

    def test_a_password_the_teacher_chose_is_left_alone(self):
        from django.contrib.auth.hashers import make_password
        from django.test import override_settings

        Teacher.objects.create(
            name="Eski Ustoz", phone="905556677", password=make_password("o'zim-qo'ydim")
        )

        with override_settings(ADMIN_PASSWORD="admin-siri"):
            self._run()

        self.assertEqual(self._login("905556677", "o'zim-qo'ydim").status_code, 200)
        self.assertEqual(self._login("905556677", "Eski Ustoz").status_code, 401)


class CreateTeacherTests(TestCase):
    """Ustoz qo'shish — ruxsat va parol qoidalari."""

    def setUp(self):
        from django.contrib.auth.hashers import make_password

        self.manager = Manager.objects.create(
            name="Menejer",
            surname="Menejerov",
            phone="917404000",
            password=make_password("x"),
            is_super=True,
        )

    def _create(self, body, as_manager=True):
        headers = {"HTTP_X_USER_PHONE": self.manager.phone} if as_manager else {}
        return Client().post(
            "/api/teachers/create/",
            data=json.dumps(body),
            content_type="application/json",
            **headers,
        )

    def test_a_stranger_cannot_create_a_teacher(self):
        res = self._create({"name": "Yangi", "phone": "935556677"}, as_manager=False)

        self.assertEqual(res.status_code, 403)
        self.assertFalse(Teacher.objects.filter(phone="935556677").exists())

    def test_a_new_teacher_can_log_in_right_away(self):
        """Qo'shish formasida parol maydoni yo'q — ism bilan kirsin."""
        res = self._create({"name": "Yangi Ustoz", "phone": "93 555 66 77"})
        self.assertEqual(res.status_code, 201, res.content)

        login = Client().post(
            "/api/login/",
            data=json.dumps({"phone": "935556677", "password": "Yangi Ustoz"}),
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200, login.content)

    def test_the_phone_check_opens_the_password_field_for_a_new_teacher(self):
        self._create({"name": "Yangi Ustoz", "phone": "93 555 66 77"})

        check = Client().post(
            "/api/login/",
            data=json.dumps({"phone": "935556677", "password": None}),
            content_type="application/json",
        )
        self.assertTrue(check.json().get("exists"))

    def test_a_password_may_be_set_at_creation(self):
        self._create(
            {"name": "Yangi Ustoz", "phone": "93 555 66 77", "password": "boshqa"}
        )

        login = Client().post(
            "/api/login/",
            data=json.dumps({"phone": "935556677", "password": "boshqa"}),
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200, login.content)

    def test_a_new_teacher_lands_in_the_teacher_panel(self):
        """Frontend router `is_admin` bo'yicha /admin ga kiritadi.

        False qaytsa ustoz kirgan bo'lsa ham o'quvchilar sahifasiga
        tushib qoladi — u uchun bu ham "kira olmadim" degani.
        """
        self._create({"name": "Yangi Ustoz", "phone": "93 555 66 77"})

        body = Client().post(
            "/api/login/",
            data=json.dumps({"phone": "935556677", "password": "Yangi Ustoz"}),
            content_type="application/json",
        ).json()

        self.assertTrue(body["is_admin"], "ustoz o'z paneliga o'ta olmaydi")
        self.assertIsNotNone(body["teacher_id"])

    def test_a_new_teacher_can_set_their_own_password_after_first_login(self):
        """To'liq yo'l: qo'shildi → ism bilan kirdi → parol o'rnatdi."""
        self._create({"name": "Yangi Ustoz", "phone": "93 555 66 77"})

        changed = Client().post(
            "/api/change-password/",
            data=json.dumps(
                {
                    "phone": "935556677",
                    "old_password": "Yangi Ustoz",
                    "new_password": "yangi-parol-123",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(changed.status_code, 200, changed.content)

        login = Client().post(
            "/api/login/",
            data=json.dumps({"phone": "935556677", "password": "yangi-parol-123"}),
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200, login.content)

        # Parol o'rnatilgach ism endi ishlamasligi kerak
        by_name = Client().post(
            "/api/login/",
            data=json.dumps({"phone": "935556677", "password": "Yangi Ustoz"}),
            content_type="application/json",
        )
        self.assertEqual(by_name.status_code, 401)

    def test_a_new_teacher_does_not_inherit_the_admin_password(self):
        """Har bir ustozga ADMIN_PASSWORD berilishi — imtiyoz oshirish yo'li."""
        from django.test import override_settings

        with override_settings(ADMIN_PASSWORD="admin-siri"):
            self._create({"name": "Yangi Ustoz", "phone": "93 555 66 77"})

        login = Client().post(
            "/api/login/",
            data=json.dumps({"phone": "935556677", "password": "admin-siri"}),
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 401, login.content)
