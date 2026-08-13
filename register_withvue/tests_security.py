"""Xavfsizlik testlari.

Eng jiddiysi: `register_student` da rol bo'sh satr bilan taqqoslanardi.
ADMIN_PASSWORD/EXCELLENCE_PASSWORD muhit o'zgaruvchisi berilmasa ular
bo'sh satr bo'ladi, ro'yxatdan o'tuvchi esa maydonni umuman yubormasa
uning qiymati ham bo'sh satr — ya'ni `"" == ""` va istalgan begona odam
o'zini admin yoki menejer qilib ro'yxatdan o'tkaza olardi.
"""

import json
from unittest.mock import patch

from django.test import Client, TestCase

from .models import Student, Teacher


class RegisterRoleEscalationTests(TestCase):
    """Bo'sh parol hech qachon rol bermasligi kerak."""

    def _register(self, body):
        return Client().post(
            "/api/register/",
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_an_unset_admin_password_does_not_hand_out_admin(self):
        """ADMIN_PASSWORD sozlanmagan serverda hamma admin bo'lib qolardi."""
        with patch("register_withvue.views.ADMIN_PASSWORD", ""):
            res = self._register(
                {"name": "Begona", "surname": "Odam", "phone": "901112233"}
            )

        self.assertEqual(res.status_code, 201, res.content)
        student = Student.objects.get(phone="901112233")
        self.assertFalse(student.is_admin, "begona odam admin bo'lib qoldi")
        self.assertFalse(
            Teacher.objects.filter(phone="901112233").exists(),
            "begona odamga ustoz yozuvi ochib berildi",
        )

    def test_an_unset_manager_password_does_not_hand_out_the_manager_panel(self):
        with patch("register_withvue.views.EXCELLENCE_PASSWORD", ""):
            self._register(
                {"name": "Begona", "surname": "Odam", "phone": "901112233"}
            )

        self.assertFalse(Student.objects.get(phone="901112233").is_excellence)

    def test_sending_an_empty_password_explicitly_also_fails(self):
        """Maydonni bo'sh qilib yuborish ham yo'l bo'lmasin."""
        with patch("register_withvue.views.ADMIN_PASSWORD", ""):
            self._register(
                {
                    "name": "Begona",
                    "surname": "Odam",
                    "phone": "901112233",
                    "admin_password": "",
                    "excellence_password": "",
                }
            )

        student = Student.objects.get(phone="901112233")
        self.assertFalse(student.is_admin)
        self.assertFalse(student.is_excellence)

    def test_a_wrong_password_does_not_grant_admin(self):
        with patch("register_withvue.views.ADMIN_PASSWORD", "haqiqiy-sir"):
            self._register(
                {
                    "name": "Begona",
                    "surname": "Odam",
                    "phone": "901112233",
                    "admin_password": "taxmin",
                }
            )

        self.assertFalse(Student.objects.get(phone="901112233").is_admin)

    def test_the_correct_password_still_grants_admin(self):
        """To'g'ri parol ishlashda davom etsin — bu yo'l yopilmaydi."""
        with patch("register_withvue.views.ADMIN_PASSWORD", "haqiqiy-sir"):
            self._register(
                {
                    "name": "Ustoz",
                    "surname": "Ustozov",
                    "phone": "901112233",
                    "admin_password": "haqiqiy-sir",
                }
            )

        self.assertTrue(Student.objects.get(phone="901112233").is_admin)

    def test_the_correct_manager_password_still_grants_the_panel(self):
        with patch("register_withvue.views.EXCELLENCE_PASSWORD", "menejer-sir"):
            self._register(
                {
                    "name": "Menejer",
                    "surname": "Menejerov",
                    "phone": "901112233",
                    "excellence_password": "menejer-sir",
                }
            )

        self.assertTrue(Student.objects.get(phone="901112233").is_excellence)
