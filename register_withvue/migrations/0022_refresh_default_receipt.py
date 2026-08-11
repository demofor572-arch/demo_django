"""Chek shablonini yangi ko'rinishga o'tkazadi.

Faqat standart matnni saqlab turganlar yangilanadi — markaz o'z matnini
yozgan bo'lsa, unga tegilmaydi.
"""

from django.db import migrations

OLD_TEMPLATE = (
    "🧾 <b>To'lov cheki</b>\n\n"
    "Hurmatli {ism}!\n"
    "{oy} oyi uchun to'lovingiz qabul qilindi.\n\n"
    "To'langan: <b>{summa}</b>\n"
    "Oylik to'lov: {jami}\n"
    "Qolgan: {qolgan}\n"
    "Sana: {sana}\n\n"
    "Rahmat! 🙏\n"
    "{markaz}"
)

_LINE = "━━━━━━━━━━━━━━━━━━━━"

NEW_TEMPLATE = (
    f"🧾 <b>TO'LOV CHEKI</b>\n"
    f"<code>{_LINE}</code>\n"
    "👤 <b>{ism}</b>\n"
    "👥 Guruh: {guruh}\n"
    "📅 Davr: {oy}\n"
    f"<code>{_LINE}</code>\n"
    "To'landi\n"
    "   💵 <b>{summa}</b>\n"
    "Oylik to'lov\n"
    "   <code>{jami}</code>\n"
    "Qolgan qarz\n"
    "   <code>{qolgan}</code>\n"
    f"<code>{_LINE}</code>\n"
    "✅ <b>To'lov qabul qilindi</b>\n"
    "🗓 {sana}\n\n"
    "Rahmat! 🙏\n"
    "<i>{markaz}</i>"
)


def _swap(apps, old, new):
    ReceiptSettings = apps.get_model("register_withvue", "ReceiptSettings")
    ReceiptSettings.objects.filter(template=old).update(template=new)


def forwards(apps, schema_editor):
    _swap(apps, OLD_TEMPLATE, NEW_TEMPLATE)


def backwards(apps, schema_editor):
    _swap(apps, NEW_TEMPLATE, OLD_TEMPLATE)


class Migration(migrations.Migration):

    dependencies = [
        ("register_withvue", "0021_alter_receiptsettings_template"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
