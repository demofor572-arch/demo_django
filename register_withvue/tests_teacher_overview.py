"""Menejer paneli — ustozlar sahifasi uchun testlar.

Regressiya: sahifada ustozlar umuman ko'rinmasdi. Backend oddiy massiv
qaytarardi, frontend esa `data.teachers` ni o'qirdi — natijada ro'yxat
har doim bo'sh bo'lardi. `?format=full` va ustoz tarixi endpointi
umuman yo'q edi.
"""

import json
from datetime import date, datetime, time, timedelta

from django.test import Client, TestCase
from django.utils import timezone

from .models import (
    CashEntry,
    CashSession,
    Group,
    Payment,
    Student,
    Teacher,
)


def _month(offset=0):
    """Joriy oydan `offset` oy naridagi "YYYY-MM"."""
    today = date.today()
    y, m = today.year, today.month + offset
    while m > 12:
        y, m = y + 1, m - 12
    while m < 1:
        y, m = y - 1, m + 12
    return f"{y:04d}-{m:02d}"


def _first_day(month):
    return date(int(month[:4]), int(month[5:]), 1)


def _last_day(month):
    first = _first_day(month)
    nxt = date(first.year + 1, 1, 1) if first.month == 12 else date(
        first.year, first.month + 1, 1
    )
    return nxt - timedelta(days=1)


class TeachersOverviewShapeTests(TestCase):
    """Javob shakli — eski massiv va yangi `format=full`."""

    def setUp(self):
        self.teacher = Teacher.objects.create(name="Salim", phone="901234567")

    def _get(self, query=""):
        return Client().get(f"/api/teachers/overview/{query}")

    def test_the_default_response_is_still_a_plain_list(self):
        """Eski ko'rinishlar massiv kutadi — buzilmasin."""
        body = self._get().json()

        self.assertIsInstance(body, list)
        self.assertEqual(body[0]["name"], "Salim")

    def test_format_full_returns_teachers_and_totals(self):
        body = self._get("?format=full").json()

        self.assertIsInstance(body, dict)
        self.assertIn("teachers", body)
        self.assertIn("totals", body)
        self.assertIn("range", body)
        self.assertEqual(len(body["teachers"]), 1)
        self.assertEqual(
            body["teachers"][0]["name"],
            "Salim",
            "ustozlar ro'yxati bo'sh — sahifada hech kim ko'rinmaydi",
        )

    def test_a_teacher_without_payments_still_appears(self):
        """To'lovi yo'q ustoz ham ro'yxatda bo'lishi kerak."""
        body = self._get("?format=full").json()

        t = body["teachers"][0]
        self.assertEqual(t["collected"], 0)
        self.assertEqual(t["expected"], 0)
        self.assertEqual(t["collected_percent"], 0)
        self.assertEqual(t["paid_students"], 0)


class TeachersOverviewStatsTests(TestCase):
    """To'lov ko'rsatkichlari to'g'ri hisoblanishi."""

    def setUp(self):
        self.month = _month()
        self.teacher = Teacher.objects.create(name="Aziz", phone="908611151")
        self.paid = Student.objects.create(
            name="To'ladi", surname="A", phone="901112233", teacher=self.teacher
        )
        self.unpaid = Student.objects.create(
            name="To'lamadi", surname="B", phone="902223344", teacher=self.teacher
        )
        Payment.objects.create(
            student=self.paid,
            month=self.month,
            stage=1,
            amount_due=100000,
            paid_amount=100000,
            is_paid=True,
        )
        Payment.objects.create(
            student=self.unpaid,
            month=self.month,
            stage=1,
            amount_due=100000,
            paid_amount=0,
            is_paid=False,
        )

    def _teacher_row(self, query=""):
        body = Client().get(f"/api/teachers/overview/?format=full{query}").json()
        return body["teachers"][0], body["totals"]

    def test_expected_and_collected_are_summed(self):
        t, _ = self._teacher_row()

        self.assertEqual(t["expected"], 200000)
        self.assertEqual(t["collected"], 100000)
        self.assertEqual(t["remaining"], 100000)
        self.assertEqual(t["collected_percent"], 50)

    def test_paid_and_unpaid_students_are_counted(self):
        t, _ = self._teacher_row()

        self.assertEqual(t["students_count"], 2)
        self.assertEqual(t["paid_students"], 1)
        self.assertEqual(t["unpaid_students"], 1)

    def test_a_discount_lowers_what_is_expected(self):
        Payment.objects.filter(student=self.unpaid).update(discount=40000)

        t, _ = self._teacher_row()

        self.assertEqual(t["expected"], 160000)

    def test_a_discount_larger_than_the_fee_does_not_go_negative(self):
        Payment.objects.filter(student=self.unpaid).update(discount=500000)

        t, _ = self._teacher_row()

        self.assertEqual(t["expected"], 100000)

    def test_a_partial_payment_counts_the_student_as_paying(self):
        Payment.objects.filter(student=self.unpaid).update(paid_amount=10000)

        t, _ = self._teacher_row()

        self.assertEqual(t["collected"], 110000)
        self.assertEqual(t["paid_students"], 2)

    def test_totals_add_up_across_teachers(self):
        other = Teacher.objects.create(name="Bek", phone="933334455")
        s = Student.objects.create(
            name="C", surname="C", phone="903334455", teacher=other
        )
        Payment.objects.create(
            student=s,
            month=self.month,
            stage=1,
            amount_due=50000,
            paid_amount=50000,
            is_paid=True,
        )

        _, totals = self._teacher_row()

        self.assertEqual(totals["collected"], 150000)
        self.assertEqual(totals["expected"], 250000)
        self.assertEqual(totals["remaining"], 100000)
        self.assertEqual(totals["paid_students"], 2)

    def test_admin_profiles_are_left_out(self):
        """Ustozning panel profili o'quvchi sifatida sanalmasin."""
        Student.objects.create(
            name="Aziz",
            surname="Panel",
            phone="908611151",
            teacher=self.teacher,
            is_admin=True,
        )

        t, _ = self._teacher_row()

        self.assertEqual(t["students_count"], 2)

    def test_another_month_is_not_counted(self):
        far = _month(-6)
        t, _ = self._teacher_row(
            f"&from={_first_day(far)}&to={_last_day(far)}"
        )

        self.assertEqual(t["collected"], 0)
        self.assertEqual(t["expected"], 0)

    def test_a_day_range_still_covers_the_whole_month(self):
        """Menejer 10-20 avgustni tanlasa ham avgust to'lovi ko'rinsin."""
        first = _first_day(self.month)
        t, _ = self._teacher_row(
            f"&from={first + timedelta(days=9)}&to={first + timedelta(days=19)}"
        )

        self.assertEqual(t["expected"], 200000)


class TeacherHistoryTests(TestCase):
    """Ustoz tarixi — oylar, o'quvchilar va pul harakati."""

    def setUp(self):
        self.month = _month()
        self.teacher = Teacher.objects.create(name="Aziz", phone="908611151")
        self.student = Student.objects.create(
            name="Ali", surname="Valiyev", phone="901112233", teacher=self.teacher
        )
        self.group = Group.objects.create(
            name="K/S 85", teacher=self.teacher, lesson_time=time(9, 30)
        )
        self.group.students.add(self.student)
        self.payment = Payment.objects.create(
            student=self.student,
            month=self.month,
            stage=1,
            amount_due=100000,
            paid_amount=60000,
            is_paid=False,
        )

    def _history(self, teacher_id=None, query=""):
        tid = teacher_id or self.teacher.id
        return Client().get(f"/api/teachers/{tid}/history/{query}")

    def test_an_unknown_teacher_is_a_404(self):
        self.assertEqual(self._history(teacher_id=9999).status_code, 404)

    def test_totals_are_returned(self):
        body = self._history().json()

        self.assertEqual(body["totals"]["students"], 1)
        self.assertEqual(body["totals"]["active_students"], 1)
        self.assertEqual(body["totals"]["paid_students"], 1)
        self.assertEqual(body["totals"]["collected"], 60000)
        self.assertEqual(body["totals"]["remaining"], 40000)

    def test_the_month_breakdown_lists_the_range(self):
        body = self._history().json()

        months = {m["month"]: m for m in body["months"]}
        self.assertIn(self.month, months)
        self.assertEqual(months[self.month]["collected"], 60000)
        self.assertEqual(months[self.month]["expected"], 100000)
        self.assertEqual(months[self.month]["paid_students"], 1)

    def test_students_carry_their_group_and_balance(self):
        body = self._history().json()

        s = body["students"][0]
        self.assertEqual(s["name"], "Ali")
        self.assertEqual(s["group_name"], "K/S 85")
        self.assertEqual(s["collected"], 60000)
        self.assertEqual(s["remaining"], 40000)
        self.assertTrue(s["has_paid"])
        self.assertEqual(s["status"], "active")

    def test_a_student_without_a_group_is_fine(self):
        self.group.students.clear()

        self.assertEqual(self._history().json()["students"][0]["group_name"], "")

    def test_cash_entries_in_the_range_are_listed(self):
        session = CashSession.objects.create(date=date.today())
        CashEntry.objects.create(
            session=session,
            payment=self.payment,
            student=self.student,
            student_name="Ali Valiyev",
            amount=60000,
            month=self.month,
        )

        body = self._history().json()

        self.assertEqual(len(body["entries"]), 1)
        self.assertEqual(body["entries"][0]["amount"], 60000)
        self.assertEqual(body["entries"][0]["student_name"], "Ali Valiyev")
        self.assertEqual(body["totals"]["received_in_range"], 60000)

    def test_cash_entries_outside_the_range_are_skipped(self):
        session = CashSession.objects.create(date=date.today())
        entry = CashEntry.objects.create(
            session=session,
            payment=self.payment,
            student=self.student,
            student_name="Ali Valiyev",
            amount=60000,
            month=self.month,
        )
        # created_at auto_now_add — yaratilgandan keyin surib qo'yamiz
        CashEntry.objects.filter(id=entry.id).update(
            created_at=timezone.now() - timedelta(days=400)
        )

        body = self._history().json()

        self.assertEqual(body["entries"], [])
        self.assertEqual(body["totals"]["received_in_range"], 0)

    def test_another_teachers_students_are_not_included(self):
        other = Teacher.objects.create(name="Bek", phone="933334455")
        Student.objects.create(
            name="Boshqa", surname="X", phone="905556677", teacher=other
        )

        body = self._history().json()

        self.assertEqual(len(body["students"]), 1)
        self.assertEqual(body["students"][0]["name"], "Ali")
