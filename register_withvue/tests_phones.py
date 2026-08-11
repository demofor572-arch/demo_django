"""Telefon raqamining bazadan yo'qolishi bilan bog'liq testlar."""

import json

from django.test import Client, TestCase

from .models import Student, TelegramSubscriber
from .phones import forget_phone


class DeleteStudentClearsPhoneTests(TestCase):
    """Regressiya: o'quvchi o'chirilgach raqami bot jadvalida qolib ketardi."""

    def setUp(self):
        self.student = Student.objects.create(
            name="Ali", surname="Valiyev", phone="+998900562345"
        )
        TelegramSubscriber.objects.create(
            chat_id=1001, phone="900562345", student=self.student, role="student"
        )

    def _delete(self, student):
        return Client().delete(f"/api/students/delete/{student.id}/")

    def test_deleting_a_student_forgets_the_number(self):
        res = self._delete(self.student)

        self.assertEqual(res.status_code, 200)
        self.assertFalse(Student.objects.filter(id=self.student.id).exists())
        self.assertEqual(
            TelegramSubscriber.objects.filter(phone="900562345").count(),
            0,
            "bot ulanishi raqam bilan qolib ketdi",
        )

    def test_a_sibling_sharing_the_number_keeps_the_bot_link(self):
        """Aka-uka ota-onasining raqamini ulashadi — biri o'chsa ikkinchisi qolsin."""
        Student.objects.create(
            name="Vali", surname="Valiyev", phone="998911112222",
            phone2="90 056 23 45",
        )

        self._delete(self.student)

        self.assertEqual(
            TelegramSubscriber.objects.filter(phone="900562345").count(),
            1,
            "ukasining bot ulanishi ham o'chib ketdi",
        )

    def test_forget_phone_reports_why_it_skipped(self):
        outcome = forget_phone("900562345")
        self.assertIn("skipped", outcome)


class GraduatePhoneTests(TestCase):
    """Bitiruvchidan faqat ism-familiya qoladi."""

    def test_several_graduates_can_share_an_empty_phone(self):
        """`unique=True` bo'sh satrda to'qnashardi — NULL cheklanmaydi."""
        Student.objects.create(name="Bir", surname="B", phone=None, is_graduate=True)
        Student.objects.create(name="Ikki", surname="B", phone=None, is_graduate=True)

        self.assertEqual(Student.objects.filter(phone__isnull=True).count(), 2)

    def test_a_graduate_can_register_again_with_the_old_number(self):
        """Raqam bo'shaganidan keyin o'sha odam qaytib kela oladi."""
        Student.objects.create(
            name="Ali", surname="Valiyev", phone=None, is_graduate=True
        )

        res = Client().post(
            "/api/register/",
            data=json.dumps(
                {"phone": "+998900562345", "name": "Ali", "surname": "Valiyev"}
            ),
            content_type="application/json",
        )

        self.assertNotEqual(res.status_code, 400, res.content[:200])


class HiddenHolderTests(TestCase):
    """Ro'yxatda ko'rinmaydigan yozuv raqamni band qilib turganini aytadi."""

    def _search(self, term):
        res = Client().get(f"/api/students/overview/?search={term}")
        return json.loads(res.content)

    def test_a_teacher_profile_is_reported(self):
        Student.objects.create(
            name="Ustoz", surname="Aka", phone="+998900562345", is_admin=True
        )

        body = self._search("900562345")

        self.assertEqual(body["students"], [])
        self.assertEqual(len(body["hidden"]), 1)
        self.assertEqual(body["hidden"][0]["kind"], "ustoz profili")
        self.assertEqual(body["hidden"][0]["name"], "Ustoz Aka")

    def test_a_graduate_is_reported(self):
        Student.objects.create(
            name="Bitir", surname="Uvchi", phone="+998900562345", is_graduate=True
        )

        body = self._search("900562345")

        self.assertEqual(body["hidden"][0]["kind"], "bitiruvchi")

    def test_nothing_is_reported_when_the_student_is_visible(self):
        Student.objects.create(name="Ali", surname="V", phone="+998900562345")

        body = self._search("900562345")

        self.assertEqual(len(body["students"]), 1)
        self.assertEqual(body["hidden"], [])
