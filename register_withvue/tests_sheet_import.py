"""Jadval importining takror ishlab ketishi bilan bog'liq testlar.

Regressiya: menejer eski ma'lumotlarni panelda o'chirardi, lekin ular
qaytib kelardi. Sabab — `load_sheet_data` ikki joydan shartsiz
chaqirilardi (render.yaml buildCommand va server ko'tarilishi), har
safar `_clear_previous()` bilan hammasini o'chirib qaytadan yaratardi.
Ayniqsa yomoni: server startidagi shart `not Lead.objects.exists()` edi
— ya'ni "leadlar o'chirilgan" holati "hali import qilinmagan" deb
tushunilardi va butun jadval qaytadan yuklanardi.
"""

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase

from .management.commands.load_sheet_data import DATA_VERSION
from .models import Lead, SheetImportMeta, Student, Teacher

SAMPLE = {
    "tabs": [
        {
            "slug": "sinov-leadlar",
            "title": "Sinov leadlari",
            "source_name": "Sinov",
            "category": "leads",
            "order": 1,
            "headers": ["Ism", "Telefon", "Holat"],
            "rows": [
                ["Anvar Anvarov", "901112233", "qiziqdi"],
                ["Bobur Boburov", "902223344", "javob bermadi"],
            ],
        }
    ]
}


class SheetImportGuardTests(TestCase):
    """Import bir marta ishlashi, keyin o'zi qayta ishlamasligi kerak."""

    def setUp(self):
        self._dir = TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "sheet.json"
        self.path.write_text(json.dumps(SAMPLE), encoding="utf-8")

    def _import(self, **kwargs):
        out = StringIO()
        call_command("load_sheet_data", file=str(self.path), stdout=out, **kwargs)
        return out.getvalue()

    def test_the_first_run_imports(self):
        self._import()

        self.assertEqual(Lead.objects.count(), 2)
        self.assertEqual(SheetImportMeta.objects.get(pk=1).version, DATA_VERSION)

    def test_a_second_run_is_skipped(self):
        self._import()
        output = self._import()

        self.assertIn("o'tkazib yuborildi", output)

    def test_deleted_data_does_not_come_back(self):
        """Asosiy muammo: o'chirilgan yozuv keyingi deployda qaytardi."""
        self._import()
        Lead.objects.all().delete()

        self._import()

        self.assertEqual(
            Lead.objects.count(),
            0,
            "o'chirilgan leadlar qayta import qilindi",
        )

    def test_force_reimports(self):
        self._import()
        Lead.objects.all().delete()

        self._import(force=True)

        self.assertEqual(Lead.objects.count(), 2)

    def test_a_new_mapping_version_reimports(self):
        self._import()
        Lead.objects.all().delete()
        SheetImportMeta.objects.filter(pk=1).update(version="eski-versiya")

        self._import()

        self.assertEqual(Lead.objects.count(), 2)


class StartupHookTests(TestCase):
    """config/wsgi.py dagi shart — o'chirilgan ma'lumotni tiklamasin."""

    def _should_import(self):
        """wsgi.py `_startup_tasks()` dagi shartning nusxasi."""
        meta = SheetImportMeta.objects.filter(pk=1).first()
        return not meta or meta.version != DATA_VERSION

    def test_it_imports_when_nothing_was_ever_imported(self):
        self.assertTrue(self._should_import())

    def test_it_does_not_import_after_the_data_was_deleted(self):
        SheetImportMeta.objects.create(pk=1, version=DATA_VERSION)
        Lead.objects.all().delete()

        self.assertFalse(
            self._should_import(),
            "leadlar bo'shligi qayta importni qo'zg'atdi",
        )

    def test_it_imports_again_when_the_mapping_version_changes(self):
        SheetImportMeta.objects.create(pk=1, version="eski-versiya")

        self.assertTrue(self._should_import())


class PurgeSheetDataTests(TestCase):
    """`purge_sheet_data` — import qilinganini o'chiradi, belgini qoldiradi."""

    def setUp(self):
        self.imported = Student.objects.create(
            name="Import", surname="Qilingan", phone="901112233", source="sheet"
        )
        self.manual = Student.objects.create(
            name="Qo'lda", surname="Kiritilgan", phone="902223344"
        )
        Teacher.objects.create(name="Import Ustoz", phone="903334455", source="sheet")
        Teacher.objects.create(name="Qo'lda Ustoz", phone="904445566")
        Lead.objects.create(name="Import Lead", source="sheet")

    def _purge(self, **kwargs):
        out = StringIO()
        call_command("purge_sheet_data", stdout=out, **kwargs)
        return out.getvalue()

    def test_without_yes_nothing_is_deleted(self):
        self._purge()

        self.assertEqual(Student.objects.count(), 2)
        self.assertEqual(Lead.objects.count(), 1)

    def test_it_deletes_only_the_imported_records(self):
        self._purge(yes=True)

        self.assertFalse(Student.objects.filter(id=self.imported.id).exists())
        self.assertTrue(Student.objects.filter(id=self.manual.id).exists())
        self.assertEqual(Teacher.objects.count(), 1)
        self.assertEqual(Lead.objects.count(), 0)

    def test_it_leaves_the_version_marker_in_place(self):
        """Belgi qolmasa server keyingi startda hammasini qaytarardi."""
        self._purge(yes=True)

        self.assertEqual(SheetImportMeta.objects.get(pk=1).version, DATA_VERSION)


class PurgeByEnvFlagTests(TestCase):
    """Render'ning bepul rejasida Shell yo'q — tozalash env orqali."""

    def setUp(self):
        Student.objects.create(name="Import", surname="Qilingan", source="sheet")
        Lead.objects.create(name="Import Lead", source="sheet")

    def _run(self):
        out = StringIO()
        call_command("purge_sheet_data", "--env-flag", stdout=out)
        return out.getvalue()

    def test_nothing_happens_without_the_flag(self):
        """Har deployda ishlaydi — o'zgaruvchisiz jim turishi shart."""
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PURGE_SHEET_DATA", None)
            self._run()

        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(Lead.objects.count(), 1)

    def test_the_flag_deletes_the_imported_data(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"PURGE_SHEET_DATA": "1"}):
            self._run()

        self.assertEqual(Student.objects.count(), 0)
        self.assertEqual(Lead.objects.count(), 0)
        self.assertEqual(SheetImportMeta.objects.get(pk=1).version, DATA_VERSION)

    def test_an_off_value_does_not_delete(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"PURGE_SHEET_DATA": "0"}):
            self._run()

        self.assertEqual(Lead.objects.count(), 1)


class PurgeExceptManagerTests(TestCase):
    """Hammasini o'chirgan buyruq ham import belgisini tiklab qo'yishi kerak."""

    def test_the_version_marker_survives_a_full_purge(self):
        from django.contrib.auth.hashers import make_password

        from .models import Manager

        Manager.objects.create(
            name="Menejer",
            surname="Menejerov",
            phone="917404000",
            password=make_password("x"),
        )
        Lead.objects.create(name="Import Lead", source="sheet")
        SheetImportMeta.objects.create(pk=1, version=DATA_VERSION)

        call_command(
            "purge_except_manager", "917404000", "--yes", stdout=StringIO()
        )

        self.assertEqual(Lead.objects.count(), 0)
        self.assertEqual(
            SheetImportMeta.objects.get(pk=1).version,
            DATA_VERSION,
            "belgi o'chdi — server qayta import qiladi va hammasi qaytadi",
        )
