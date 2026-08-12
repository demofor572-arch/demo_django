"""Telegram webhook'ini ro'yxatdan o'tkazadi (bot shusiz ishlamaydi).

Bot "jim" bo'lib qolishining eng ko'p uchraydigan sababi — webhook
Telegram tomonda umuman o'rnatilmagan bo'lishi. U holda /start bosilsa
ham update backend'ga kelmaydi va bot javob bermaydi.

Bu buyruq har deployda `buildCommand` ichida o'zi ishlaydi:

    python manage.py set_webhook

Env o'zgaruvchilari:

    TG_BOT_TOKEN        @BotFather'dan olingan token   (majburiy)
    PUBLIC_BASE_URL     https://xxx.onrender.com       (majburiy)
    TG_WEBHOOK_SECRET   ixtiyoriy maxfiy kalit — soxta so'rovlarni rad etadi

Holatni tekshirish:

    python manage.py set_webhook --info      # hech narsa o'zgartirmaydi
    python manage.py set_webhook --delete    # webhook'ni o'chiradi

⚠️ Hech qachon xato bilan tugamaydi — bu yerdagi muammo tufayli butun
deploy yiqilib qolmasligi kerak.
"""

import json
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand

WEBHOOK_PATH = "/api/tg/webhook/"


def api(method, payload=None, timeout=30):
    """Telegram Bot API chaqiruvi (requests'siz — deploy paytida yengilroq)."""
    url = f"https://api.telegram.org/bot{settings.TG_BOT_TOKEN}/{method}"
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class Command(BaseCommand):
    help = "Telegram webhook'ini PUBLIC_BASE_URL bo'yicha o'rnatadi"

    def add_arguments(self, parser):
        parser.add_argument(
            "--info", action="store_true", help="Faqat holatni ko'rsatadi"
        )
        parser.add_argument(
            "--delete", action="store_true", help="Webhook'ni o'chiradi"
        )
        parser.add_argument("--url", help="PUBLIC_BASE_URL o'rniga shu manzil")

    def handle(self, *args, **options):
        try:
            self._run(options)
        except Exception as exc:  # deploy yiqilmasin
            self.stderr.write(
                self.style.WARNING(f"set_webhook o'tkazib yuborildi: {exc}")
            )

    def _run(self, options):
        if not settings.TG_BOT_TOKEN:
            self.stderr.write(
                self.style.WARNING(
                    "TG_BOT_TOKEN berilmagan — bot o'chiq holatda qoladi"
                )
            )
            return

        me = api("getMe")
        if not me.get("ok"):
            self.stderr.write(
                self.style.ERROR(f"Token yaroqsiz: {me.get('description')}")
            )
            return
        username = me["result"].get("username", "")
        self.stdout.write(f"Bot: @{username}")

        if options["info"]:
            info = api("getWebhookInfo").get("result", {})
            self.stdout.write(json.dumps(info, indent=2, ensure_ascii=False))
            if not info.get("url"):
                self.stderr.write(
                    self.style.WARNING(
                        "Webhook o'rnatilmagan — shuning uchun bot javob bermaydi"
                    )
                )
            if info.get("last_error_message"):
                self.stderr.write(
                    self.style.WARNING(
                        f"Oxirgi xato: {info['last_error_message']}"
                    )
                )
            return

        if options["delete"]:
            api("deleteWebhook", {"drop_pending_updates": False})
            self.stdout.write(self.style.SUCCESS("Webhook o'chirildi"))
            return

        base = (options.get("url") or settings.PUBLIC_BASE_URL or "").rstrip("/")
        if not base:
            self.stderr.write(
                self.style.WARNING(
                    "PUBLIC_BASE_URL berilmagan — webhook o'rnatilmadi. "
                    "Render → Environment'da uni backend manziliga tenglang, "
                    "masalan: https://demo-django-c3eh.onrender.com"
                )
            )
            return
        if not base.startswith("https://"):
            self.stderr.write(
                self.style.ERROR(
                    f"Telegram faqat https qabul qiladi, berilgani: {base}"
                )
            )
            return

        target = base + WEBHOOK_PATH
        info = api("getWebhookInfo").get("result", {})

        # Manzil to'g'ri bo'lsa ham qayta o'rnatamiz. Telegram maxfiy
        # kalitni qaytarmaydi, ya'ni u eskirganini bu yerdan bilib
        # bo'lmaydi — eskirgan bo'lsa esa har bir update 403 bilan rad
        # etiladi va bot butunlay jim qoladi. setWebhook idempotent va
        # arzon, deployda bir marta chaqirilgani zarar qilmaydi.
        if info.get("url") == target:
            self.stdout.write(f"Manzil o'zgarmagan, kalit yangilanadi: {target}")

        payload = {
            "url": target,
            # Faqat kerakli turlar — ortiqcha update'lar bilan bandlik bo'lmasin
            "allowed_updates": ["message", "edited_message"],
            "max_connections": 40,
        }
        if settings.TG_WEBHOOK_SECRET:
            payload["secret_token"] = settings.TG_WEBHOOK_SECRET

        res = api("setWebhook", payload)
        if res.get("ok"):
            self.stdout.write(self.style.SUCCESS(f"Webhook o'rnatildi: {target}"))
            if not settings.TG_WEBHOOK_SECRET:
                self.stderr.write(
                    self.style.WARNING(
                        "TG_WEBHOOK_SECRET berilmagan — webhook'ni istalgan "
                        "kishi chaqira oladi. Uni env'ga qo'shish tavsiya etiladi."
                    )
                )
        else:
            self.stderr.write(
                self.style.ERROR(f"setWebhook xatosi: {res.get('description')}")
            )
