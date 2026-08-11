"""Kunlik umumiy kassa + tranzaksiya jurnali testlari.

Kassa bitta va har kunga bitta smena (umumiy account). Asosiy maqsad —
foydalanuvchi tasvirlagan xatoni yopish: to'lovni ertasi kuni qayta
yozish (200k -> 400k) kechagi topshirilgan kunni buzmasligi, balki
ertangi kun kassasiga +200k bo'lib tushishi kerak.
"""

import json
from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.test.client import RequestFactory

from . import views
from .models import (
    Manager,
    Student,
    Payment,
    CashSession,
    CashEntry,
    CashRegisterSettings,
)


def _on(day):
    """views.tashkent_today() ni berilgan kunga qotiradi."""
    return patch("register_withvue.views.tashkent_today", return_value=day)


class CashRegisterTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.mgr = Manager.objects.create(
            name="Kassir",
            surname="Test",
            phone="+998900000001",
            password="x",
            permissions=["cash.view", "cash.close"],
        )
        self.student = Student.objects.create(
            name="Ali", surname="Vali", phone="+998900000002", stage=1
        )
        self.payment = Payment.objects.create(
            student=self.student, month="2026-08", stage=1, amount_due=400000
        )
        CashRegisterSettings.get_settings()  # enabled=True (default)

    def _req(self, body="{}"):
        return self.rf.post(
            "/x",
            data=body,
            content_type="application/json",
            HTTP_X_USER_PHONE=self.mgr.phone,
        )

    def test_cross_day_history_preserved(self):
        """Foydalanuvchi ssenariysi — kechagi kun buzilmaydi."""
        # 1-kun: 0 -> 200 000, topshiriladi
        with _on(date(2026, 8, 1)):
            self.payment.paid_amount = 200000
            self.payment.save()
            views.record_cash_delta(self._req(), self.payment, 0, 200000)
            resp = views.close_cash_session(self._req('{"counted_total": 200000}'))
            self.assertEqual(resp.status_code, 200)

        s1 = CashSession.objects.get(date=date(2026, 8, 1))
        self.assertEqual(s1.status, CashSession.STATUS_CLOSED)
        self.assertEqual(s1.expected_total, 200000)

        # 2-kun: kassir 200 000 ni o'chirib 400 000 yozadi
        with _on(date(2026, 8, 2)):
            self.payment.paid_amount = 400000
            self.payment.save()
            views.record_cash_delta(self._req(), self.payment, 200000, 400000)

        s2 = CashSession.objects.get(date=date(2026, 8, 2))
        self.assertEqual(s2.live_total(), 200000)  # faqat 2-kun +200k

        # 1-kun tarixi buzilmagan
        s1.refresh_from_db()
        self.assertEqual(s1.expected_total, 200000)
        self.assertEqual(CashSession.objects.count(), 2)  # har kunga bitta

        # Jurnal jami = to'lov jami
        total = sum(e.amount for e in CashEntry.objects.all())
        self.assertEqual(total, 400000)

    def test_reopen_same_day_after_close(self):
        """Topshirilgan kunga yana pul tushsa smena qayta ochiladi (bitta qatorda)."""
        with _on(date(2026, 8, 3)):
            views.record_cash_delta(self._req(), self.payment, 0, 100000)
            resp = views.close_cash_session(self._req('{"counted_total": 100000}'))
            self.assertEqual(resp.status_code, 200)
            s = CashSession.objects.get(date=date(2026, 8, 3))
            self.assertEqual(s.status, CashSession.STATUS_CLOSED)

            # O'sha kuni yana pul tushdi
            views.record_cash_delta(self._req(), self.payment, 100000, 150000)
            s.refresh_from_db()
            self.assertEqual(s.status, CashSession.STATUS_OPEN)  # qayta ochildi
            self.assertEqual(s.live_total(), 150000)
            self.assertEqual(CashSession.objects.filter(date=date(2026, 8, 3)).count(), 1)

    def test_shortage_detected(self):
        """Kam sanalgan pul kamomad (manfiy farq) sifatida ko'rinadi."""
        with _on(date(2026, 8, 4)):
            views.record_cash_delta(self._req(), self.payment, 0, 300000)
            resp = views.close_cash_session(self._req('{"counted_total": 250000}'))
            self.assertEqual(resp.status_code, 200)
        s = CashSession.objects.get(date=date(2026, 8, 4))
        self.assertEqual(s.expected_total, 300000)
        self.assertEqual(s.counted_total, 250000)
        self.assertEqual(s.difference, -50000)

    def test_require_counted_blocks_empty_close(self):
        """Sanoq majburiy bo'lganda bo'sh topshiruv rad etiladi."""
        with _on(date(2026, 8, 5)):
            views.record_cash_delta(self._req(), self.payment, 0, 100000)
            resp = views.close_cash_session(self._req("{}"))
            self.assertEqual(resp.status_code, 400)
        s = CashSession.objects.get(date=date(2026, 8, 5))
        self.assertIsNone(s.closed_at)

    def test_disabled_records_ledger_without_session(self):
        """Kassa o'chirilganda smena ochilmaydi, lekin jurnal yoziladi."""
        st = CashRegisterSettings.get_settings()
        st.enabled = False
        st.save()
        with _on(date(2026, 8, 6)):
            views.record_cash_delta(self._req(), self.payment, 0, 100000)
        self.assertEqual(CashSession.objects.count(), 0)
        self.assertEqual(CashEntry.objects.count(), 1)
        self.assertIsNone(CashEntry.objects.first().session_id)

    def test_zero_delta_no_entry(self):
        """Summa o'zgarmasa jurnalga yozuv qo'shilmaydi."""
        with _on(date(2026, 8, 7)):
            views.record_cash_delta(self._req(), self.payment, 200000, 200000)
        self.assertEqual(CashEntry.objects.count(), 0)


class CollectionPlanTests(TestCase):
    """Kassir "qancha yig'ildi / qancha yig'ilishi kerak" ni ko'radi."""

    def setUp(self):
        self.rf = RequestFactory()
        self.mgr = Manager.objects.create(
            name="Kassir",
            surname="Test",
            phone="+998900000031",
            password="x",
            permissions=["cash.view", "cash.close"],
        )
        CashRegisterSettings.get_settings()

    def _student(self, n):
        return Student.objects.create(
            name=f"O'quvchi{n}", surname="Test", phone=f"+99890000{n:04d}", stage=1
        )

    def test_plan_counts_net_due_and_collected(self):
        """Chegirma ayriladi, to'liq to'langanlar alohida sanaladi."""
        a = self._student(101)
        b = self._student(102)
        # a: 400k, 50k chegirma → 350k kerak, 350k to'landi
        Payment.objects.create(
            student=a,
            month="2026-08",
            stage=1,
            amount_due=400000,
            discount=50000,
            paid_amount=350000,
            is_paid=True,
        )
        # b: 400k, chegirmasiz → 400k kerak, 100k to'landi
        Payment.objects.create(
            student=b, month="2026-08", stage=1, amount_due=400000, paid_amount=100000
        )

        plan = views.month_collection_plan("2026-08")
        self.assertEqual(plan["due_total"], 750000)
        self.assertEqual(plan["collected_total"], 450000)
        self.assertEqual(plan["remaining_total"], 300000)
        self.assertEqual(plan["collected_percent"], 60)
        self.assertEqual(plan["paid_count"], 1)
        self.assertEqual(plan["unpaid_count"], 1)

    def test_overpayment_does_not_hide_another_students_debt(self):
        """Bittasining ortiqcha to'lovi boshqasining qarzini yopmaydi."""
        a = self._student(103)
        b = self._student(104)
        Payment.objects.create(
            student=a,
            month="2026-08",
            stage=1,
            amount_due=400000,
            paid_amount=600000,  # ortiqcha to'lagan
            is_paid=True,
        )
        Payment.objects.create(
            student=b, month="2026-08", stage=1, amount_due=400000, paid_amount=0
        )

        plan = views.month_collection_plan("2026-08")
        # Sodda ayirma 800k - 600k = 200k berardi; haqiqiy qarz 400k
        self.assertEqual(plan["remaining_total"], 400000)

    def test_empty_month_is_safe(self):
        """To'lov yaratilmagan oy 0 bilan qaytadi, nolga bo'linmaydi."""
        plan = views.month_collection_plan("2030-01")
        self.assertEqual(plan["due_total"], 0)
        self.assertEqual(plan["collected_percent"], 0)
        self.assertEqual(plan["total_count"], 0)

    def test_cash_current_carries_the_plan(self):
        """Smena ochilmagan bo'lsa ham maqsad ko'rinadi."""
        st = self._student(105)
        Payment.objects.create(
            student=st, month="2026-08", stage=1, amount_due=400000
        )
        req = self.rf.get(
            "/api/cash/current/",
            {"month": "2026-08"},
            HTTP_X_USER_PHONE=self.mgr.phone,
        )
        with _on(date(2026, 8, 10)):
            body = json.loads(views.get_cash_current(req).content)
        self.assertIsNone(body["session"])  # bugun hali to'lov yo'q
        self.assertEqual(body["plan"]["due_total"], 400000)
        self.assertEqual(body["plan"]["remaining_total"], 400000)


class PaymentInstallmentTests(TestCase):
    """Bo'lib to'lash — kassir jami emas, "bugun tushgan"ni kiritadi.

    400 000 lik oyga bugun 200 000, ertaga yana 200 000 tushadi.
    Kassir har safar 200 000 yozadi; tizim jamini o'zi qo'shadi va
    har kun kassasiga aynan o'sha kuni tushgani chiqadi.
    """

    def setUp(self):
        self.rf = RequestFactory()
        self.mgr = Manager.objects.create(
            name="Kassir",
            surname="Test",
            phone="+998900000011",
            password="x",
            permissions=["cash.view", "cash.close"],
        )
        self.student = Student.objects.create(
            name="Ali", surname="Vali", phone="+998900000012", stage=1
        )
        self.payment = Payment.objects.create(
            student=self.student, month="2026-08", stage=1, amount_due=400000
        )
        CashRegisterSettings.get_settings()

    def _pay(self, amount):
        req = self.rf.post(
            f"/api/payments/{self.payment.id}/pay/",
            data=json.dumps({"amount": amount}),
            content_type="application/json",
            HTTP_X_USER_PHONE=self.mgr.phone,
        )
        # Chek telegramga ketmasin — testda tashqi so'rov bo'lmaydi
        with patch("register_withvue.telegram.send_receipt"):
            return views.add_payment_installment(req, self.payment.id)

    def test_two_day_split_lands_on_its_own_day(self):
        """Foydalanuvchi ssenariysi — har kun o'z pulini ko'rsatadi."""
        # 1-kun: 200 000 tushdi, kassa topshirildi
        with _on(date(2026, 8, 1)):
            self.assertEqual(self._pay(200000).status_code, 201)
            close = self.rf.post(
                "/x",
                data='{"counted_total": 200000}',
                content_type="application/json",
                HTTP_X_USER_PHONE=self.mgr.phone,
            )
            self.assertEqual(views.close_cash_session(close).status_code, 200)

        # 2-kun: kassir yana "200 000" yozadi (jami 400 000 emas)
        with _on(date(2026, 8, 2)):
            resp = self._pay(200000)
            self.assertEqual(resp.status_code, 201)
            body = json.loads(resp.content)
            self.assertEqual(body["paid_amount"], 400000)  # jamini tizim qo'shdi
            self.assertEqual(body["remaining"], 0)
            self.assertTrue(body["is_paid"])

        s1 = CashSession.objects.get(date=date(2026, 8, 1))
        s2 = CashSession.objects.get(date=date(2026, 8, 2))
        self.assertEqual(s1.expected_total, 200000)  # kechagi topshiruv buzilmadi
        self.assertEqual(s1.status, CashSession.STATUS_CLOSED)
        self.assertEqual(s2.live_total(), 200000)  # bugungi kassa kam emas

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.paid_amount, 400000)

    def test_history_shows_each_installment_with_its_day(self):
        """Tarixda har bo'lak o'z kuni bilan turadi."""
        with _on(date(2026, 8, 1)):
            self._pay(200000)
        with _on(date(2026, 8, 2)):
            self._pay(200000)

        req = self.rf.get(f"/api/payments/{self.payment.id}/history/")
        body = json.loads(views.get_payment_installments(req, self.payment.id).content)
        self.assertEqual(body["paid_amount"], 400000)
        self.assertEqual(body["remaining"], 0)
        days = sorted(i["date"] for i in body["installments"])
        self.assertEqual(days, ["2026-08-01", "2026-08-02"])
        self.assertEqual(sum(i["amount"] for i in body["installments"]), 400000)

    def test_partial_payment_does_not_close_the_month(self):
        """Yarim to'lov oyni yopmaydi, qolgani ko'rinib turadi."""
        with _on(date(2026, 8, 1)):
            body = json.loads(self._pay(150000).content)
        self.assertFalse(body["is_paid"])
        self.assertEqual(body["remaining"], 250000)

    def test_zero_or_negative_amount_rejected(self):
        """Bo'sh/manfiy summa qabul qilinmaydi."""
        with _on(date(2026, 8, 1)):
            self.assertEqual(self._pay(0).status_code, 400)
            self.assertEqual(self._pay(-5000).status_code, 400)
        self.assertEqual(CashEntry.objects.count(), 0)

    def test_lowering_total_needs_explicit_confirmation(self):
        """Jamini kamaytirish tasodifan o'tib ketmaydi."""
        with _on(date(2026, 8, 1)):
            self._pay(200000)

        def _patch(body):
            return self.rf.patch(
                f"/api/payments/{self.payment.id}/update/",
                data=json.dumps(body),
                content_type="application/json",
                HTTP_X_USER_PHONE=self.mgr.phone,
            )

        with _on(date(2026, 8, 2)):
            blocked = views.update_payment_amount(
                _patch({"paid_amount": 50000}), self.payment.id
            )
            self.assertEqual(blocked.status_code, 400)
            self.assertEqual(
                json.loads(blocked.content)["code"], "paid_amount_decrease"
            )
            self.payment.refresh_from_db()
            self.assertEqual(self.payment.paid_amount, 200000)  # tegilmadi

            allowed = views.update_payment_amount(
                _patch({"paid_amount": 50000, "allow_decrease": True}),
                self.payment.id,
            )
            self.assertEqual(allowed.status_code, 200)
            self.payment.refresh_from_db()
            self.assertEqual(self.payment.paid_amount, 50000)

        # Tuzatish bugungi kassadan yechiladi, kechagisi qolaveradi
        s2 = CashSession.objects.get(date=date(2026, 8, 2))
        self.assertEqual(s2.live_total(), -150000)
