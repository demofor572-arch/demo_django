"""Mavjud menejerlarga barcha vakolatlarni beradi.

`permissions` maydoni bo'sh ro'yxat bilan qo'shilgani uchun, backfill
qilinmasa eski menejerlar ertasi kuni bo'sh panel ko'rardi. Ular
ilgari hamma narsani qila olardi — shuning uchun boshlang'ich holat
"hammasi ruxsat". Supermenejer keyin keraksizlarini o'chiradi.

Keyin qo'shiladigan yangi menejerlar bunga aloqador emas — ularga
vakolatlar yaratilish paytida beriladi.
"""

from django.db import migrations


def grant_all_to_existing(apps, schema_editor):
    from register_withvue.access import PERMISSIONS

    all_keys = [key for key, _label, _section in PERMISSIONS]
    Manager = apps.get_model("register_withvue", "Manager")
    for manager in Manager.objects.all():
        if not manager.permissions:
            manager.permissions = all_keys
            manager.save(update_fields=["permissions"])


def noop(apps, schema_editor):
    """Orqaga qaytishda vakolatlarni o'chirmaymiz — zarari yo'q."""


class Migration(migrations.Migration):
    dependencies = [
        ("register_withvue", "0014_manager_is_super_manager_permissions_and_more"),
    ]

    operations = [
        migrations.RunPython(grant_all_to_existing, noop),
    ]
