"""Face ID oqimi uchun testlar.

Qo'riqlanadigan narsalar: terminal raqami hech qachon takrorlanmasligi,
rasm terminal talablariga moslashtirilishi, navbat faqat kerakli
o'quvchilarni berishi va terminalning "HTTP 200 + xato" javobi xato
sifatida o'qilishi.
"""

import base64
import io
import json
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from . import faceid
from . import telegram as tg
from .models import FaceDevice, FaceSync, Student, TelegramSubscriber


def make_image(width, height, fmt="JPEG"):
    """Sinov uchun rasm baytlari."""
    from PIL import Image

    image = Image.new("RGB", (width, height), (120, 90, 60))
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


class PersonIdTests(TestCase):
    def test_id_is_stable_and_unique(self):
        a = Student.objects.create(name="Ali", surname="Valiyev", phone="+998901")
        b = Student.objects.create(name="Vali", surname="Aliyev", phone="+998902")

        first = faceid.allocate_person_id(a)
        second = faceid.allocate_person_id(b)

        self.assertNotEqual(first, second)
        # Qayta chaqirilsa o'sha raqam qaytadi — terminalda ikkinchi
        # yozuv paydo bo'lmasligi kerak
        self.assertEqual(faceid.allocate_person_id(a), first)

    def test_skips_manually_taken_number(self):
        a = Student.objects.create(name="Ali", surname="V", phone="+998901")
        taken = str(faceid.PERSON_ID_BASE + a.id)
        Student.objects.create(
            name="Qo'lda", surname="Kiritilgan", phone="+998903",
            face_person_id=taken,
        )

        self.assertNotEqual(faceid.allocate_person_id(a), taken)

    def test_duplicate_is_rejected_by_database(self):
        from django.db import IntegrityError

        Student.objects.create(name="A", surname="B", phone="+9989011",
                               face_person_id="10500")
        with self.assertRaises(IntegrityError):
            Student.objects.create(name="C", surname="D", phone="+9989012",
                                   face_person_id="10500")

    def test_blank_is_allowed_many_times(self):
        """Cheklov bo'sh raqamga tegmasligi kerak — ko'pchilik bog'lanmagan."""
        Student.objects.create(name="A", surname="B", phone="+9989013")
        Student.objects.create(name="C", surname="D", phone="+9989014")
        self.assertEqual(Student.objects.filter(face_person_id="").count(), 2)


class PhotoTests(TestCase):
    def test_accepts_4_by_3(self):
        photo, error = faceid.normalize_photo(make_image(1200, 900))
        self.assertIsNone(error)
        self.assertTrue(photo)
        self.assertLessEqual(len(base64.b64decode(photo)), faceid.MAX_PHOTO_BYTES)

    def test_accepts_portrait_3_by_4(self):
        _photo, error = faceid.normalize_photo(make_image(720, 960))
        self.assertIsNone(error)

    def test_rejects_square(self):
        _photo, error = faceid.normalize_photo(make_image(800, 800))
        self.assertIn("4:3", error)

    def test_rejects_wide_16_by_9(self):
        _photo, error = faceid.normalize_photo(make_image(1920, 1080))
        self.assertIn("4:3", error)

    def test_rejects_tiny(self):
        _photo, error = faceid.normalize_photo(make_image(160, 120))
        self.assertIn("kichik", error)

    def test_rejects_garbage(self):
        _photo, error = faceid.normalize_photo(b"not an image at all")
        self.assertIn("o'qib bo'lmadi", error)

    def test_shrinks_oversized_photo(self):
        """Telefon surati terminal chegarasiga o'zi moslashishi kerak."""
        photo, error = faceid.normalize_photo(make_image(3000, 2250))
        self.assertIsNone(error)

        from PIL import Image

        image = Image.open(io.BytesIO(base64.b64decode(photo)))
        self.assertLessEqual(max(image.size), faceid.MAX_PHOTO_SIDE)

    def test_png_is_converted_to_jpeg(self):
        photo, error = faceid.normalize_photo(make_image(800, 600, fmt="PNG"))
        self.assertIsNone(error)
        self.assertTrue(base64.b64decode(photo).startswith(b"\xff\xd8"))


class SaveFaceTests(TestCase):
    def setUp(self):
        self.student = Student.objects.create(
            name="Ali", surname="Valiyev", phone="+998901234567"
        )

    def test_saves_photo_and_allocates_id(self):
        person_id, error = faceid.save_face_photo(self.student, make_image(800, 600))
        self.assertIsNone(error)

        self.student.refresh_from_db()
        self.assertEqual(self.student.face_person_id, person_id)
        self.assertEqual(self.student.face_status, "pending")
        self.assertTrue(self.student.face_photo)
        self.assertIsNotNone(self.student.face_updated_at)

    def test_bad_photo_leaves_student_untouched(self):
        person_id, error = faceid.save_face_photo(self.student, make_image(800, 800))
        self.assertIsNone(person_id)
        self.assertTrue(error)

        self.student.refresh_from_db()
        self.assertEqual(self.student.face_status, "none")
        self.assertEqual(self.student.face_person_id, "")


class QueueTests(TestCase):
    def setUp(self):
        self.device = FaceDevice.objects.create(name="Kirish", secret="s1")
        self.student = Student.objects.create(
            name="Ali", surname="Valiyev", phone="+998901234567"
        )
        faceid.save_face_photo(self.student, make_image(800, 600))

    def test_new_photo_is_pending(self):
        pending = faceid.pending_students(self.device)
        self.assertEqual([s.id for s in pending], [self.student.id])

    def test_synced_student_leaves_the_queue(self):
        faceid.mark_synced(self.device, self.student, ok=True)
        self.assertEqual(faceid.pending_students(self.device), [])

        self.student.refresh_from_db()
        self.assertEqual(self.student.face_status, "synced")

    def test_new_photo_returns_to_the_queue(self):
        """Regressiya: rasm almashsa terminal yangisini olishi kerak."""
        faceid.mark_synced(self.device, self.student, ok=True)
        faceid.save_face_photo(self.student, make_image(900, 675))

        pending = faceid.pending_students(self.device)
        self.assertEqual([s.id for s in pending], [self.student.id])

    def test_queue_defers_photo_but_sync_still_gets_it(self):
        """Ro'yxat rasmsiz o'qiladi (xotira uchun) — yozishda esa kerak."""
        light = faceid.pending_students(self.device)[0]
        self.assertIn("face_photo", light.get_deferred_fields())

        heavy = faceid.pending_students(self.device, with_photo=True)[0]
        self.assertNotIn("face_photo", heavy.get_deferred_fields())
        self.assertTrue(heavy.face_photo)

    def test_rejected_student_is_not_queued(self):
        self.student.face_status = "rejected"
        self.student.save(update_fields=["face_status"])
        self.assertEqual(faceid.pending_students(self.device), [])

    def test_each_device_tracks_its_own_state(self):
        other = FaceDevice.objects.create(name="Chiqish", secret="s2")
        faceid.mark_synced(self.device, self.student, ok=True)

        self.assertEqual(faceid.pending_students(self.device), [])
        self.assertEqual(len(faceid.pending_students(other)), 1)

    def test_repeated_failure_is_reported_once(self):
        """Agent har daqiqada urinadi — o'quvchi bir xil ogohlantirishni
        soatlab olmasligi kerak."""
        self.assertTrue(
            faceid.mark_synced(self.device, self.student, False, "Yuz topilmadi")
        )
        self.assertFalse(
            faceid.mark_synced(self.device, self.student, False, "Yuz topilmadi")
        )
        self.assertTrue(
            faceid.mark_synced(self.device, self.student, False, "Boshqa xato")
        )


class IsapiResultTests(TestCase):
    """Hikvision xatoni HTTP 200 bilan qaytaradi — buni ushlash shart."""

    class FakeResponse:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self._body = body
            self.text = json.dumps(body) if isinstance(body, dict) else str(body)

        def json(self):
            if isinstance(self._body, dict):
                return self._body
            raise ValueError("not json")

    def test_ok(self):
        ok, _, _ = faceid._isapi_result(self.FakeResponse(200, {"statusCode": 1}))
        self.assertTrue(ok)

    def test_error_inside_200_response(self):
        ok, message, _ = faceid._isapi_result(
            self.FakeResponse(
                200, {"statusCode": 6, "subStatusCode": "noFaceDetected"}
            )
        )
        self.assertFalse(ok)
        self.assertIn("yuz topilmadi", message.lower())

    def test_already_exists_is_detected(self):
        """Regressiya: xabar o'zbekchaga o'girilgani uchun «exist» ni
        matndan emas, terminal kodidan qidirish kerak."""
        _ok, _message, code = faceid._isapi_result(
            self.FakeResponse(
                400, {"statusCode": 6, "subStatusCode": "employeeNoAlreadyExist"}
            )
        )
        self.assertTrue(faceid._is_already_exists(code))

    def test_plain_error_is_not_mistaken_for_exists(self):
        _ok, _message, code = faceid._isapi_result(
            self.FakeResponse(200, {"statusCode": 6, "subStatusCode": "noFaceDetected"})
        )
        self.assertFalse(faceid._is_already_exists(code))

    def test_non_json_response(self):
        ok, message, _ = faceid._isapi_result(self.FakeResponse(401, "<html>nope"))
        self.assertFalse(ok)
        self.assertIn("401", message)


class SyncEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.device = FaceDevice.objects.create(name="Kirish", secret="abc123")
        self.student = Student.objects.create(
            name="Ali", surname="Valiyev", phone="+998901234567"
        )
        faceid.save_face_photo(self.student, make_image(800, 600))

    def test_queue_returns_pending_students(self):
        res = self.client.get("/api/faceid/sync/abc123/")
        self.assertEqual(res.status_code, 200)

        data = res.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["students"][0]["person_id"],
                         self.student.face_person_id)
        self.assertTrue(data["students"][0]["photo"])

    def test_wrong_secret_is_rejected(self):
        self.assertEqual(self.client.get("/api/faceid/sync/nope/").status_code, 404)

    def test_ack_marks_student_synced(self):
        res = self.client.post(
            "/api/faceid/sync/abc123/",
            data=json.dumps(
                {"results": [{"person_id": self.student.face_person_id, "ok": True}]}
            ),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["saved"], 1)

        self.student.refresh_from_db()
        self.assertEqual(self.student.face_status, "synced")
        self.assertEqual(self.client.get("/api/faceid/sync/abc123/").json()["total"], 0)

    @patch.object(faceid, "notify_photo_rejected")
    def test_ack_failure_records_the_reason(self, notify):
        self.client.post(
            "/api/faceid/sync/abc123/",
            data=json.dumps(
                {
                    "results": [
                        {
                            "person_id": self.student.face_person_id,
                            "ok": False,
                            "error": "Rasmda yuz topilmadi",
                        }
                    ]
                }
            ),
            content_type="application/json",
        )

        self.student.refresh_from_db()
        self.assertEqual(self.student.face_note, "Rasmda yuz topilmadi")
        notify.assert_called_once()

        sync = FaceSync.objects.get(device=self.device, student=self.student)
        self.assertFalse(sync.ok)

    def test_failed_student_stays_in_the_queue(self):
        faceid.mark_synced(self.device, self.student, False, "Yuz topilmadi")
        self.assertEqual(self.client.get("/api/faceid/sync/abc123/").json()["total"], 1)


class BotPhotoTests(TestCase):
    def setUp(self):
        self.student = Student.objects.create(
            name="Ali", surname="Valiyev", phone="+998901234567"
        )
        TelegramSubscriber.objects.create(
            chat_id=555, student=self.student, role="student", phone="901234567"
        )

    def _photo_update(self, chat_id=555):
        return {
            "message": {
                "chat": {"id": chat_id, "first_name": "Ali"},
                "from": {"id": 777},
                "photo": [
                    {"file_id": "small", "file_size": 100},
                    {"file_id": "big", "file_size": 9000},
                ],
            }
        }

    @patch.object(tg, "_push_face_now")
    @patch.object(tg, "send_text")
    @patch.object(tg, "download_file")
    def test_photo_is_saved(self, download, send_text, _push):
        download.return_value = (make_image(800, 600), None)

        tg.handle_update(self._photo_update())

        # Eng katta o'lcham olinishi kerak — kichigi tanish uchun yaramaydi
        download.assert_called_once_with("big")

        self.student.refresh_from_db()
        self.assertEqual(self.student.face_status, "pending")
        self.assertIn(self.student.face_person_id, send_text.call_args[0][1])

    @patch.object(tg, "send_text")
    @patch.object(tg, "download_file")
    def test_bad_ratio_is_explained(self, download, send_text):
        download.return_value = (make_image(1920, 1080), None)

        tg.handle_update(self._photo_update())

        self.assertIn("4:3", send_text.call_args[0][1])
        self.student.refresh_from_db()
        self.assertEqual(self.student.face_status, "none")

    @patch.object(tg, "send_text")
    @patch.object(tg, "download_file")
    def test_photo_from_unlinked_chat_is_refused(self, download, send_text):
        tg.handle_update(self._photo_update(chat_id=888))

        download.assert_not_called()
        self.assertIn("/start", send_text.call_args[0][1])

    @patch.object(tg, "send_text")
    def test_face_button_asks_for_photo(self, send_text):
        tg.handle_update(
            {
                "message": {
                    "chat": {"id": 555, "first_name": "Ali"},
                    "from": {"id": 777},
                    "text": "🪪 Face ID",
                }
            }
        )
        body = send_text.call_args[0][1]
        self.assertIn("4:3", body)
        # Ogohlantirish ko'rinishi shart — coinga ta'siri aytilgan bo'lsin
        self.assertIn("coin", body.lower())

    @patch.object(tg, "send_text")
    def test_face_button_shows_status_when_photo_exists(self, send_text):
        faceid.save_face_photo(self.student, make_image(800, 600))

        tg.handle_update(
            {
                "message": {
                    "chat": {"id": 555, "first_name": "Ali"},
                    "from": {"id": 777},
                    "text": "🪪 Face ID",
                }
            }
        )
        self.assertIn(self.student.face_person_id, send_text.call_args[0][1])

    @patch.object(tg, "send_text")
    @patch.object(tg, "download_file")
    def test_teacher_photo_gets_a_clear_answer(self, download, send_text):
        """Ustozga «ulanmagansiz» deyish chalkash — u ulangan."""
        from .models import Teacher

        teacher = Teacher.objects.create(name="Ustoz", phone="+998907654321")
        TelegramSubscriber.objects.create(
            chat_id=444, teacher=teacher, role="teacher", phone="907654321"
        )

        tg.handle_update(self._photo_update(chat_id=444))

        download.assert_not_called()
        body = send_text.call_args[0][1]
        self.assertIn("o'quvchilar uchun", body)
        self.assertNotIn("/start", body)

    @patch.object(tg, "send_text")
    def test_non_image_document_is_refused(self, send_text):
        tg.handle_update(
            {
                "message": {
                    "chat": {"id": 555, "first_name": "Ali"},
                    "from": {"id": 777},
                    "document": {"file_id": "d1", "mime_type": "application/pdf"},
                }
            }
        )
        self.assertIn("rasm emas", send_text.call_args[0][1].lower())


class EndToEndTests(TestCase):
    """Butun zanjir: bot rasmi → raqam → terminal → avtomatik davomat."""

    def setUp(self):
        self.client = Client()
        self.device = FaceDevice.objects.create(name="Kirish", secret="e2e")
        self.student = Student.objects.create(
            name="Ali", surname="Valiyev", phone="+998901234567"
        )
        TelegramSubscriber.objects.create(
            chat_id=555, student=self.student, role="student", phone="901234567"
        )

        from .models import Group

        # Bugun darsi bo'lishi uchun har kuni o'qiydigan guruh
        self.group = Group.objects.create(
            name="Python-1", schedule="daily", lesson_time="09:00"
        )
        self.group.students.add(self.student)

    @patch.object(tg, "_push_face_now")
    @patch.object(tg, "send_text")
    @patch.object(tg, "download_file")
    def test_photo_to_attendance(self, download, _send_text, _push):
        from .models import Attendance

        # ① O'quvchi botga rasm yuboradi
        download.return_value = (make_image(800, 600), None)
        tg.handle_update(
            {
                "message": {
                    "chat": {"id": 555, "first_name": "Ali"},
                    "from": {"id": 777},
                    "photo": [{"file_id": "big", "file_size": 9000}],
                }
            }
        )

        self.student.refresh_from_db()
        person_id = self.student.face_person_id
        self.assertTrue(person_id, "raqam berilmadi")

        # ② Terminal yonidagi agent navbatni oladi
        queue = self.client.get("/api/faceid/sync/e2e/").json()
        self.assertEqual(queue["total"], 1)
        self.assertEqual(queue["students"][0]["person_id"], person_id)

        # ③ Agent yozib bo'lgach natijani qaytaradi
        self.client.post(
            "/api/faceid/sync/e2e/",
            data=json.dumps({"results": [{"person_id": person_id, "ok": True}]}),
            content_type="application/json",
        )
        self.student.refresh_from_db()
        self.assertEqual(self.student.face_status, "synced")

        # ④ Terminal yuzni tanib hodisa yuboradi
        res = self.client.post(
            "/api/faceid/event/e2e/",
            data=json.dumps(
                {
                    "dateTime": timezone.localtime().isoformat(),
                    "AccessControllerEvent": {
                        "majorEventType": 5,
                        "subEventType": 75,
                        "employeeNoString": person_id,
                        "name": "Ali",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(res.json()["status"], "marked")

        # ⑤ Davomat belgilangan bo'lishi kerak
        attendance = Attendance.objects.get(student=self.student)
        self.assertIn(attendance.status, ("present", "late"))

    def test_unknown_number_does_not_mark_anyone(self):
        """Begona raqam hech kimning davomatiga tegmasligi kerak."""
        from .models import Attendance

        res = self.client.post(
            "/api/faceid/event/e2e/",
            data=json.dumps(
                {
                    "dateTime": timezone.localtime().isoformat(),
                    "AccessControllerEvent": {
                        "majorEventType": 5,
                        "subEventType": 75,
                        "employeeNoString": "99999",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(res.json()["status"], "unknown")
        self.assertEqual(Attendance.objects.count(), 0)


class ProductBroadcastTests(TestCase):
    def setUp(self):
        from .models import Product

        self.student = Student.objects.create(
            name="Ali", surname="Valiyev", phone="+998901234567"
        )
        TelegramSubscriber.objects.create(
            chat_id=555, student=self.student, role="student", phone="901234567"
        )
        self.product = Product.objects.create(
            name="Naushnik", price_coins=1500, image="https://example.com/a.jpg"
        )

    def _run_broadcast(self):
        """Fon oqimi testda kutilmasin — to'g'ridan-to'g'ri chaqiramiz."""
        with patch.object(tg.threading, "Thread") as thread:
            tg.broadcast_product(self.product)
            thread.call_args.kwargs["target"]()

    @patch.object(tg, "send_photo")
    def test_students_get_image_name_and_price(self, send_photo):
        self._run_broadcast()

        send_photo.assert_called_once()
        chat_id, photo, caption = send_photo.call_args[0]
        self.assertEqual(chat_id, 555)
        self.assertEqual(photo, "https://example.com/a.jpg")
        self.assertIn("Naushnik", caption)
        self.assertIn("1 500 coin", caption)

    @patch.object(tg, "send_text")
    @patch.object(tg, "send_photo")
    def test_broken_image_falls_back_to_text(self, send_photo, send_text):
        """Regressiya: ishlamaydigan rasm havolasi e'lonni yo'q qilmasin."""
        send_photo.side_effect = RuntimeError("wrong file identifier")

        self._run_broadcast()

        send_text.assert_called_once()
        self.assertIn("Naushnik", send_text.call_args[0][1])

    @patch.object(tg, "send_text")
    @patch.object(tg, "send_photo")
    def test_non_http_image_goes_straight_to_text(self, send_photo, send_text):
        self.product.image = "/media/a.jpg"

        self._run_broadcast()

        send_photo.assert_not_called()
        send_text.assert_called_once()
