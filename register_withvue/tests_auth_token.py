"""Kirish tokeni — autentifikatsiya testlari.

Ilgari chaqiruvchi 'X-User-Phone' sarlavhasi bilan aniqlanardi va uni
istalgan odam qo'lda yozib qo'ya olardi: menejer raqamini bilgan kishi
o'zini menejer qilib ko'rsata olardi. Token o'sha teshikni yopadi —
uni faqat parolni bilgan odam login orqali oladi.
"""

import json

from django.contrib.auth.hashers import make_password
from django.test import Client, TestCase

from .access import _hash_token
from .models import AuthToken, LoginDevice, Manager, Teacher


class TokenIssueTests(TestCase):
    """Login token berishi kerak."""

    def setUp(self):
        self.manager = Manager.objects.create(
            name="Menejer",
            surname="Menejerov",
            phone="917404000",
            password=make_password("sir"),
            is_super=True,
        )
        Teacher.objects.create(name="Salim", phone="901234567")

    def _manager_login(self):
        return Client().post(
            "/api/manager/login/",
            data=json.dumps({"phone": "917404000", "password": "sir"}),
            content_type="application/json",
            HTTP_X_DEVICE_ID="qurilma-1",
        )

    def test_manager_login_returns_a_token(self):
        body = self._manager_login().json()

        self.assertTrue(body.get("token"), "login token bermadi")
        self.assertEqual(AuthToken.objects.count(), 1)

    def test_teacher_login_returns_a_token(self):
        body = Client().post(
            "/api/login/",
            data=json.dumps({"phone": "901234567", "password": "Salim"}),
            content_type="application/json",
        ).json()

        self.assertTrue(body.get("token"))
        self.assertEqual(AuthToken.objects.get().role, "teacher")

    def test_the_raw_token_is_never_stored(self):
        """Baza nusxasi sizib chiqsa ham tirik token tiklanmasin."""
        raw = self._manager_login().json()["token"]

        stored = AuthToken.objects.get()
        self.assertNotEqual(stored.key_hash, raw)
        self.assertEqual(stored.key_hash, _hash_token(raw))

    def test_each_login_gets_its_own_token(self):
        first = self._manager_login().json()["token"]
        second = self._manager_login().json()["token"]

        self.assertNotEqual(first, second)
        self.assertEqual(AuthToken.objects.count(), 2)

    def test_a_failed_login_issues_nothing(self):
        Client().post(
            "/api/manager/login/",
            data=json.dumps({"phone": "917404000", "password": "notogri"}),
            content_type="application/json",
        )

        self.assertEqual(AuthToken.objects.count(), 0)


class TokenAuthenticationTests(TestCase):
    """Token bilan kirish va uni bekor qilish."""

    def setUp(self):
        self.manager = Manager.objects.create(
            name="Menejer",
            surname="Menejerov",
            phone="917404000",
            password=make_password("sir"),
            is_super=True,
        )
        self.token = Client().post(
            "/api/manager/login/",
            data=json.dumps({"phone": "917404000", "password": "sir"}),
            content_type="application/json",
            HTTP_X_DEVICE_ID="qurilma-1",
        ).json()["token"]

    def _create_teacher(self, **headers):
        """Ruxsat talab qiladigan amal — kimlik tekshiruvi shu yerda."""
        return Client().post(
            "/api/teachers/create/",
            data=json.dumps({"name": "Yangi", "phone": "935556677"}),
            content_type="application/json",
            **headers,
        )

    def test_no_credentials_are_refused(self):
        self.assertEqual(self._create_teacher().status_code, 403)

    def test_a_valid_token_is_accepted(self):
        res = self._create_teacher(HTTP_AUTHORIZATION=f"Bearer {self.token}")

        self.assertEqual(res.status_code, 201, res.content)

    def test_a_made_up_token_is_refused(self):
        res = self._create_teacher(HTTP_AUTHORIZATION="Bearer soxta-token")

        self.assertEqual(res.status_code, 403)

    def test_a_bad_token_does_not_fall_back_to_the_legacy_header(self):
        """Aks holda bekor qilingan token egasi sarlavha bilan davom etardi."""
        res = self._create_teacher(
            HTTP_AUTHORIZATION="Bearer soxta-token",
            HTTP_X_USER_PHONE="917404000",
        )

        self.assertEqual(res.status_code, 403)

    def test_the_token_wins_over_a_spoofed_header(self):
        """Sarlavhada boshqa raqam bo'lsa ham kimlik tokendan olinadi."""
        res = self._create_teacher(
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
            HTTP_X_USER_PHONE="900000000",
        )

        self.assertEqual(res.status_code, 201)

    def test_logging_out_kills_the_token(self):
        out = Client().post(
            "/api/logout/", HTTP_AUTHORIZATION=f"Bearer {self.token}"
        )
        self.assertEqual(out.status_code, 200)
        self.assertEqual(out.json()["revoked"], 1)

        res = self._create_teacher(HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(res.status_code, 403)

    def test_logging_out_leaves_other_devices_alone(self):
        other = Client().post(
            "/api/manager/login/",
            data=json.dumps({"phone": "917404000", "password": "sir"}),
            content_type="application/json",
            HTTP_X_DEVICE_ID="qurilma-2",
        ).json()["token"]

        Client().post("/api/logout/", HTTP_AUTHORIZATION=f"Bearer {self.token}")

        res = self._create_teacher(HTTP_AUTHORIZATION=f"Bearer {other}")
        self.assertEqual(res.status_code, 201)

    def test_last_used_is_recorded(self):
        self.assertIsNone(AuthToken.objects.get().last_used_at)

        self._create_teacher(HTTP_AUTHORIZATION=f"Bearer {self.token}")

        self.assertIsNotNone(AuthToken.objects.get().last_used_at)

    def test_a_malformed_authorization_header_is_ignored(self):
        for header in ("Bearer", "Basic abc", self.token):
            with self.subTest(header=header):
                res = self._create_teacher(HTTP_AUTHORIZATION=header)
                self.assertEqual(res.status_code, 403)


class BlockedDeviceTests(TestCase):
    """Token muddatsiz — qurilmani bloklash uni to'xtatadigan yo'l."""

    def setUp(self):
        self.manager = Manager.objects.create(
            name="Menejer",
            surname="Menejerov",
            phone="917404000",
            password=make_password("sir"),
            is_super=True,
        )
        self.token = Client().post(
            "/api/manager/login/",
            data=json.dumps({"phone": "917404000", "password": "sir"}),
            content_type="application/json",
            HTTP_X_DEVICE_ID="qurilma-1",
        ).json()["token"]

    def _create_teacher(self):
        return Client().post(
            "/api/teachers/create/",
            data=json.dumps({"name": "Yangi", "phone": "935556677"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

    def test_blocking_the_device_stops_the_token(self):
        self.assertEqual(self._create_teacher().status_code, 201)

        device = LoginDevice.objects.get(device_id="qurilma-1")
        Client().patch(
            f"/api/super/devices/{device.id}/block/",
            data=json.dumps({"is_blocked": True}),
            content_type="application/json",
            HTTP_X_USER_PHONE="917404000",
        )

        self.assertEqual(
            self._create_teacher().status_code,
            403,
            "bloklangan qurilmaning tokeni hali ishlayapti",
        )


class LegacyHeaderTests(TestCase):
    """Ko'chish davri — eski sarlavha hali ishlaydi.

    Frontend token yuboradigan bo'lgach va hamma qayta kirgach bu
    tarmoq olib tashlanadi; shu testlar o'shanda o'zgartiriladi.
    """

    def setUp(self):
        Manager.objects.create(
            name="Menejer",
            surname="Menejerov",
            phone="917404000",
            password=make_password("sir"),
            is_super=True,
        )

    def test_the_old_header_still_works_without_a_token(self):
        res = Client().post(
            "/api/teachers/create/",
            data=json.dumps({"name": "Yangi", "phone": "935556677"}),
            content_type="application/json",
            HTTP_X_USER_PHONE="917404000",
        )

        self.assertEqual(res.status_code, 201, res.content)
