"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import logging
import os
import threading

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()


def _startup_tasks():
    """Render'da build vaqtida migrate ishlamasa ham, server ko'tarilganda
    migratsiyalarni bajaradi. Sheet importi bo'lmasa yoki versiyasi eskirgan
    bo'lsa — qayta import qiladi."""
    try:
        from django.core.management import call_command

        call_command("migrate", interactive=False)

        from register_withvue.models import SheetImportMeta
        from register_withvue.management.commands.load_sheet_data import (
            DATA_VERSION,
        )

        # Bir martalik tozalash — PURGE_SHEET_DATA muhit o'zgaruvchisi
        # yoqilgan bo'lsagina ishlaydi.
        #
        # Nega build buyrug'ida emas, shu yerda: `render.yaml` faqat
        # Blueprint bilan boshqariladigan servislarda o'qiladi. Servis
        # Render dashboard'idan qo'lda yaratilgan bo'lsa, build buyrug'i
        # dashboard'dagi qatordan olinadi va render.yaml'ga qo'shilgan
        # buyruq umuman ishga tushmaydi. Server ko'tarilishi esa har
        # ikkala holatda ham bo'ladi.
        #
        # ⚠️ Tozalangach o'zgaruvchini Render'dan olib tashlang: aks holda
        # har uyg'onishda takrorlanadi (import qilingan yozuv qolmagach
        # zarari yo'q, lekin keyin ataylab import qilsangiz darrov o'chadi).
        if os.environ.get("PURGE_SHEET_DATA", "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            logging.warning("PURGE_SHEET_DATA yoqilgan — import ma'lumoti o'chirilmoqda")
            call_command("purge_sheet_data", "--yes")

        # ⚠️ Bu yerda avval `not Lead.objects.exists()` sharti ham bor edi.
        # U "hali import qilinmagan" degan ma'noda yozilgan, lekin amalda
        # "menejer lead'larni o'chirib tashladi" holatini ham ushlab
        # olardi: eski ma'lumot o'chirilgach, server keyingi marta
        # ko'tarilishi bilan (Render'ning bepul rejasi harakatsizlikdan
        # keyin uxlaydi va qaytadan uyg'onadi) hammasi qayta import
        # qilinardi. Endi faqat import umuman bo'lmaganda yoki mapping
        # versiyasi o'zgarganda ishlaydi.
        meta = SheetImportMeta.objects.filter(pk=1).first()
        if not meta or meta.version != DATA_VERSION:
            call_command("load_sheet_data")
            SheetImportMeta.objects.filter(pk=1).update(last_error="")
    except Exception:
        logging.exception("Startup migrate/load_sheet_data xatosi")
        # Import atomic — xato bo'lsa hammasi qaytariladi va tashqaridan
        # "hech narsa o'zgarmadi" bo'lib ko'rinadi. Sababini saqlaymiz,
        # aks holda uni faqat Render loglaridan topish mumkin bo'lardi.
        try:
            import traceback

            from register_withvue.models import SheetImportMeta

            SheetImportMeta.objects.update_or_create(
                pk=1, defaults={"last_error": traceback.format_exc()[-4000:]}
            )
        except Exception:
            logging.exception("Import xatosini saqlab bo'lmadi")


threading.Thread(target=_startup_tasks, daemon=True).start()
