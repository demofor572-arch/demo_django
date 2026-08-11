"""Davomat ro'yxatidagi coin ma'lumotlari.

Coin berish "O'quvchilar" bo'limidan davomat belgilanadigan joyga
ko'chdi: ustoz dars belgilab turib imtihon/vazifa uchun coin beradi.
Shu sababli guruh-kun javobi balansni, oylik erkin bonus holatini va
tugmalar miqdorini ham qaytarishi kerak.
"""

import json
from datetime import time

from django.test import TestCase
from django.test.client import RequestFactory

from . import views
from .models import CoinTransaction, Group, Student, Teacher


class AttendanceCoinPayloadTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.teacher = Teacher.objects.create(name="Ustoz", phone="+998900000021")
        self.student = Student.objects.create(
            name="Ali", surname="Vali", phone="+998900000022", stage=1
        )
        self.group = Group.objects.create(
            name="Test guruh", teacher=self.teacher, lesson_time=time(10, 0)
        )
        self.group.students.add(self.student)

    def _day(self):
        req = self.rf.get(
            "/api/attendance/group-day/", {"group_id": self.group.id}
        )
        resp = views.attendance_group_day(req)
        self.assertEqual(resp.status_code, 200)
        return json.loads(resp.content)

    def test_day_rows_carry_coin_balance(self):
        """Ustoz har o'quvchining coinini shu ro'yxatda ko'radi."""
        views.apply_coin_transaction(self.student, 80, "exam_pass")

        body = self._day()
        row = body["students"][0]
        self.assertEqual(row["student_id"], self.student.id)
        self.assertEqual(row["coin_balance"], 80)
        self.assertFalse(row["bonus_used"])

    def test_coin_actions_come_from_backend(self):
        """Tugma miqdorlari frontendda qotirib yozilmagan."""
        body = self._day()
        by_reason = {a["reason"]: a["amount"] for a in body["coin_actions"]}
        self.assertEqual(by_reason["exam_pass"], views.EXAM_PASS_COINS)
        self.assertEqual(by_reason["homework_missed"], views.HOMEWORK_MISSED_COINS)

    def test_monthly_bonus_flag_matches_the_limit(self):
        """Bonus berilgach tugma yopiq bo'lib qaytadi (rad javob kutilmaydi)."""
        give = self.rf.post(
            "/api/coins/give/",
            data=json.dumps(
                {
                    "student_id": self.student.id,
                    "teacher_id": self.teacher.id,
                    "reason": "manual",
                    "amount": 50,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(views.give_manual_coins(give).status_code, 201)

        self.assertTrue(self._day()["students"][0]["bonus_used"])

        # Ikkinchi marta berilmaydi — ro'yxatdagi holat bilan bir xil
        again = self.rf.post(
            "/api/coins/give/",
            data=json.dumps(
                {
                    "student_id": self.student.id,
                    "teacher_id": self.teacher.id,
                    "reason": "manual",
                    "amount": 50,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(views.give_manual_coins(again).status_code, 400)
        self.assertEqual(
            CoinTransaction.objects.filter(reason="manual").count(), 1
        )

    def test_month_view_carries_coin_balance(self):
        """Oylik jadvalda ham coin ustuni to'ldiriladi."""
        views.apply_coin_transaction(self.student, 20, "homework_done")
        req = self.rf.get(
            "/api/attendance/group-month/",
            {"group_id": self.group.id, "month": "2026-08"},
        )
        resp = views.attendance_group_month(req)
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertEqual(body["students"][0]["coin_balance"], 20)
