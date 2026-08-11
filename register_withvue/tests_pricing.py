"""Narx o'zgarganda to'lovlar reaktiv yangilanishi testlari.

Kurs yoki stage narxi o'zgarsa — to'lanmagan to'lovlarning amount_due'si
joriy narxga tenglashadi (barcha bo'limlarda). To'langan yozuvlar
tarixiy qiymatida qoladi.
"""

import json
from datetime import time, date
from unittest.mock import patch

from django.test import TestCase
from django.test.client import RequestFactory

from . import views
from .models import Student, Payment, StagePrice, Course, Group

# Joriy oyni qotiramiz — "keyingi oy" solishtiruvi barqaror bo'lsin
TODAY = date(2026, 8, 15)  # joriy oy = 2026-08


def _fixed_today():
    return patch("register_withvue.views.tashkent_today", return_value=TODAY)


class PriceReactivityTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.student = Student.objects.create(
            name="Ali", surname="Vali", phone="+998900000010", stage=1
        )
        StagePrice.objects.create(stage=1, price=400000)

    def _patch(self, path, body):
        return self.rf.patch(path, data=json.dumps(body), content_type="application/json")

    def test_only_future_months_change(self):
        past = Payment.objects.create(
            student=self.student, month="2026-07", stage=1, amount_due=400000
        )
        current = Payment.objects.create(
            student=self.student, month="2026-08", stage=1, amount_due=400000
        )
        future = Payment.objects.create(
            student=self.student, month="2026-09", stage=1, amount_due=400000
        )
        paid_future = Payment.objects.create(
            student=self.student, month="2026-10", stage=1,
            amount_due=400000, is_paid=True, paid_amount=400000,
        )

        with _fixed_today():
            resp = views.update_stage_price(self._patch("/x", {"price": 500000}), stage=1)
        self.assertEqual(resp.status_code, 200)

        for p in (past, current, future, paid_future):
            p.refresh_from_db()
        self.assertEqual(past.amount_due, 400000)         # o'tgan — o'zgarmadi
        self.assertEqual(current.amount_due, 400000)      # joriy — o'zgarmadi
        self.assertEqual(future.amount_due, 500000)       # keyingi — yangilandi
        self.assertEqual(paid_future.amount_due, 400000)  # to'langan — tarixiy

    def test_discount_clamped_to_new_price(self):
        # Keyingi oy uchun to'lov
        p = Payment.objects.create(
            student=self.student, month="2026-09", stage=1,
            amount_due=400000, discount=350000,
        )
        with _fixed_today():
            views.update_stage_price(self._patch("/x", {"price": 300000}), stage=1)
        p.refresh_from_db()
        self.assertEqual(p.amount_due, 300000)
        self.assertEqual(p.discount, 300000)  # chegirma narxdan oshmaydi

    def test_course_price_updates_future_payments(self):
        course = Course.objects.create(name="Python", monthly_fee=300000)
        group = Group.objects.create(name="PY-1", lesson_time=time(10, 0), course=course)
        group.students.add(self.student)

        current = Payment.objects.create(
            student=self.student, month="2026-08", stage=1, amount_due=300000
        )
        future = Payment.objects.create(
            student=self.student, month="2026-09", stage=1, amount_due=300000
        )
        with _fixed_today():
            resp = views.update_course(self._patch("/x", {"monthly_fee": 350000}), course_id=course.id)
        self.assertEqual(resp.status_code, 200)
        current.refresh_from_db()
        future.refresh_from_db()
        self.assertEqual(current.amount_due, 300000)  # joriy oy o'zgarmadi
        self.assertEqual(future.amount_due, 350000)   # keyingi oy yangilandi
