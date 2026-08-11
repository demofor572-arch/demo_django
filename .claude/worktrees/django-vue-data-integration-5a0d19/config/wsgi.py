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

        from register_withvue.models import Lead, SheetImportMeta
        from register_withvue.management.commands.load_sheet_data import (
            DATA_VERSION,
        )

        meta = SheetImportMeta.objects.filter(pk=1).first()
        if not Lead.objects.exists() or not meta or meta.version != DATA_VERSION:
            call_command("load_sheet_data")
    except Exception:
        logging.exception("Startup migrate/load_sheet_data xatosi")


threading.Thread(target=_startup_tasks, daemon=True).start()
