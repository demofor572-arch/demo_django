import calendar
import json
import logging
import secrets
from datetime import datetime, date, timedelta

from django.db import transaction
from django.db.models import Sum, F, Count, Q, Value, IntegerField
from django.db.models.functions import Greatest
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.hashers import make_password, check_password
from django.db import models as db_models
from rest_framework import serializers


from .models import (
    Group,
    Student,
    Teacher,
    Lesson,
    Attendance,
    Payment,
    StagePrice,
    StudentPenalty,
    Manager,
    CoinTransaction,
    Product,
    Order,
    AttendanceCoinSettings,
    Course,
    News,
    Expense,
    Lead,
    AdChannel,
    LessonReminderLog,
    PaymentSettings,
    PaymentRequest,
    CashRegisterSettings,
    CashSession,
    CashEntry,
)

from django.utils import timezone
from django.conf import settings
from rest_framework import generics, permissions
from .phones import forget_student_phones
from .serializers import NewsSerializer
from .access import (
    DEFAULT_PERMISSIONS,
    caller_manager,
    is_device_blocked,
    log_action,
    log_attendance,
    record_login,
    require_permission,
    require_super,
    touch_presence,
)

# Parollar kodda saqlanmaydi — settings orqali env'dan keladi (.env / Render)
ADMIN_PASSWORD = settings.ADMIN_PASSWORD
EXCELLENCE_PASSWORD = settings.EXCELLENCE_PASSWORD

ODD_DAYS = {0, 2, 4}
EVEN_DAYS = {1, 3, 5}

ATTENDANCE_REASON = {
    "present": "present",
    "late": "late",
    "absent": "absent",
}

EXAM_PASS_COINS = 80
HOMEWORK_DONE_COINS = 20
HOMEWORK_PARTIAL_COINS = 10
HOMEWORK_MISSED_COINS = -20

# Davomat belgilanayotgan joyda chiqadigan tez tugmalar. Miqdor faqat
# shu yerda turadi — panel ro'yxatni API'dan oladi, shuning uchun
# o'zgarganda frontendni qayta yozish shart emas.
COIN_QUICK_ACTIONS = [
    {"reason": "exam_pass", "label": "Imtihon", "amount": EXAM_PASS_COINS},
    {
        "reason": "homework_done",
        "label": "Vazifa to'liq",
        "amount": HOMEWORK_DONE_COINS,
    },
    {
        "reason": "homework_partial",
        "label": "Vazifa chala",
        "amount": HOMEWORK_PARTIAL_COINS,
    },
    {
        "reason": "homework_missed",
        "label": "Vazifa yo'q",
        "amount": HOMEWORK_MISSED_COINS,
    },
]
COIN_QUICK_AMOUNTS = {a["reason"]: a["amount"] for a in COIN_QUICK_ACTIONS}


# ─────────────────────────────
# SERIALIZERS
# ─────────────────────────────


class StudentMinimalSerializer(serializers.ModelSerializer):
    """Guruh students uchun minimal ma'lumot."""

    class Meta:
        model = Student
        fields = ["id", "name", "surname", "phone", "stage", "schedule"]


class TeacherMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Teacher
        fields = ["id", "name", "phone", "is_senior"]


class CourseSerializer(serializers.ModelSerializer):
    groups_count = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = ["id", "name", "monthly_fee", "groups_count"]

    def get_groups_count(self, obj):
        return obj.groups.count()


class GroupSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source="course.name", read_only=True)
    # ✅ FIX: IntegerField (Course.monthly_fee = IntegerField)
    monthly_fee = serializers.IntegerField(
        source="course.monthly_fee", read_only=True, allow_null=True
    )

    students_count = serializers.SerializerMethodField()
    students = StudentMinimalSerializer(many=True, read_only=True)
    teacher = TeacherMiniSerializer(read_only=True)

    class Meta:
        model = Group
        fields = "__all__"

    def get_students_count(self, obj):
        return obj.students.count()


# ─────────────────────────────
# HELPERS
# ─────────────────────────────


def get_stage_price(stage):
    """Staganing narxini qaytaradi."""
    sp = StagePrice.objects.filter(stage=stage).first()
    return sp.price if sp else 0


def get_schedule_for_day(weekday):
    """Hafta kuniga qarab schedule qaytaradi."""
    if weekday in ODD_DAYS:
        return "odd"
    elif weekday in EVEN_DAYS:
        return "even"
    return None


def get_attendance_coins_map():
    """
    Davomat coin miqdorlarini bazadan (AttendanceCoinSettings'dan) olib keladi.
    """
    s = AttendanceCoinSettings.get_settings()
    return {"present": s.present, "late": s.late, "absent": s.absent}


def apply_coin_transaction(
    student, amount, reason, given_by=None, note="", attendance=None
):
    """
    Coin tranzaksiyasini yozadi. Student.coin_balance ni CoinTransaction.save()
    o'zi avtomatik (F() orqali) yangilaydi.
    """
    CoinTransaction.objects.create(
        student=student,
        given_by=given_by,
        reason=reason,
        amount=amount,
        note=note,
        attendance=attendance,
    )
    student.refresh_from_db(fields=["coin_balance"])
    return student.coin_balance


def monthly_bonus_used_ids(student_ids, teacher_id):
    """Shu oy erkin bonus olib bo'lgan o'quvchilar to'plami.

    Bonus (o'quvchi, ustoz, oy) bo'yicha bir martaga cheklangan. Ayni
    shu funksiya cheklovni ham tekshiradi, tugma holatini ham beradi —
    shunda panelda ochiq turgan tugma bosilganda "allaqachon berilgan"
    deb rad javob kelib qolmaydi.
    """
    if not student_ids:
        return set()
    now = tashkent_now()
    return set(
        CoinTransaction.objects.filter(
            student_id__in=student_ids,
            reason="manual",
            given_by_id=teacher_id,
            created_at__year=now.year,
            created_at__month=now.month,
        ).values_list("student_id", flat=True)
    )


# ─────────────────────────────
# OYLIK TO'LOV MUDDATI (guruh ochilgan kunga bog'liq)
# ─────────────────────────────

# Toshkent UTC+5, yozgi vaqt yo'q — server UTC bo'lgani uchun sanani
# to'g'ri chiqarish uchun qo'lda siljitamiz.
TASHKENT_OFFSET = timedelta(hours=5)

WEEKDAY_NAMES_UZ = [
    "Dushanba",
    "Seshanba",
    "Chorshanba",
    "Payshanba",
    "Juma",
    "Shanba",
    "Yakshanba",
]


def tashkent_now():
    return timezone.now() + TASHKENT_OFFSET


def tashkent_today():
    return tashkent_now().date()


def student_primary_group(student):
    """O'quvchining asosiy guruhi (birinchi biriktirilgani).

    `.all()` orqali — prefetch_related qilingan bo'lsa qo'shimcha so'rovsiz.
    """
    groups = list(student.groups.all())
    if not groups:
        return None
    return min(groups, key=lambda g: g.id)


def parse_opened_date(value):
    """'YYYY-MM-DD' sanani date'ga o'giradi. (date_or_None, error_or_None).

    Bo'sh/None qiymat — sana yo'q (xato emas).
    """
    if value in (None, ""):
        return None, None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None, None
        # ISO datetime kelsa ("2026-01-25T00:00:00") faqat sanani olamiz
        value = value[:10]
        try:
            return datetime.strptime(value, "%Y-%m-%d").date(), None
        except ValueError:
            return None, "opened_date 'YYYY-MM-DD' formatida bo'lishi kerak"
    return None, "opened_date noto'g'ri"


def payment_due_date(month_str, group):
    """Guruh ochilgan kunga qarab shu oyning to'lov muddatini qaytaradi.

    month_str: "YYYY-MM". Guruh 25-kuni ochilgan bo'lsa — muddat oyning
    25-kuni. Guruh ochilgan sana bo'lmasa — oyning 1-kuni (eski xatti-harakat).
    """
    try:
        year, mon = (int(x) for x in month_str.split("-"))
    except (ValueError, AttributeError):
        return None
    day = 1
    if group and group.opened_date:
        day = group.opened_date.day
    # Qisqa oylar uchun (masalan fevral) kunni oy oxiriga cheklaymiz
    last_day = calendar.monthrange(year, mon)[1]
    day = min(day, last_day)
    try:
        return date(year, mon, day)
    except ValueError:
        return None


def attendance_map_for_month(student_ids, month_str):
    """Oyда har o'quvchi uchun (kelgan, jami) davomat sonini qaytaradi.

    Dars yaratilganда har o'quvchiga 'absent' yozuvi yaratilgani uchun
    jami davomat yozuvlari = o'sha oyдаги darslar soni. Kelgan = present+late.
    Har kunlik kurslarда darslar ko'p bo'lgani uchun jami ham ko'p bo'ladi —
    hisob avtomatik to'g'ri chiqadi.
    """
    result = {}
    try:
        year, mon = int(str(month_str)[:4]), int(str(month_str)[5:7])
    except (ValueError, TypeError, IndexError):
        return result
    if not student_ids:
        return result
    rows = (
        Attendance.objects.filter(
            student_id__in=student_ids,
            lesson__date__year=year,
            lesson__date__month=mon,
        )
        .values("student_id")
        .annotate(
            total=Count("id"),
            attended=Count("id", filter=Q(status__in=["present", "late"])),
        )
    )
    for r in rows:
        result[r["student_id"]] = (r["attended"], r["total"])
    return result


# ─────────────────────────────────────────
# WALLET (virtual karta: ortiqcha balans + qarzdorlik)
# ─────────────────────────────────────────


def _wallet_from_totals(total_due, total_discount, total_paid):
    """Jami summalardan kartani hisoblaydi.

    Sof to'lanishi kerak = jami due − jami chegirma. Balans = to'langan −
    sof to'lanishi kerak. Musbat bo'lsa kartada ortiqcha pul qoladi,
    manfiy bo'lsa o'sha miqdor qarzdorlik.
    """
    net_due = int(total_due or 0) - int(total_discount or 0)
    paid = int(total_paid or 0)
    net = paid - net_due
    return {
        "balance": max(0, net),  # kartada qolgan (ortiqcha) pul
        "debt": max(0, -net),  # qarzdorlik
        "net": net,  # musbat=balans, manfiy=qarz
        "total_due": net_due,  # chegirmadan keyingi jami to'lov
        "total_paid": paid,
    }


def effective_monthly_fee(student):
    """O'quvchining haqiqiy oylik to'lovi.

    Ustunlik: guruh kursining narxi (Course.monthly_fee) -> stage narxi.
    To'lov yozuvida amount_due 0/belgilanmagan bo'lsa shu qiymat ishlatiladi —
    shunda karta (wallet) va ko'rsatilgan "Oylik to'lov" bir xil bo'ladi.
    """
    group = student_primary_group(student)
    if group and group.course_id:
        fee = getattr(group.course, "monthly_fee", 0) or 0
        if fee:
            return int(fee)
    return int(get_stage_price(student.stage) or 0)


def _payment_due(amount_due, fallback_fee):
    """To'lov yozuvining sof due'si — amount_due 0 bo'lsa kurs narxiga tayanadi."""
    a = int(amount_due or 0)
    return a if a > 0 else int(fallback_fee or 0)


def resync_unpaid_amount_due(students):
    """Narx o'zgargach faqat KEYINGI oylarning to'lovlarini yangilaydi.

    Kurs yoki stage narxi o'zgarganda chaqiriladi. Faqat joriy oydan
    keyingi (month > joriy oy) to'lanmagan to'lovlar joriy narxga
    tenglashadi — joriy oy, o'tgan oylar va to'langan (is_paid=True)
    yozuvlar tarixiy qiymatida qoladi. Chegirma yangi narxdan oshib
    ketmasin uchun qisqartiriladi.

    Nechta yozuv o'zgargani qaytadi.
    """
    current_month = tashkent_today().strftime("%Y-%m")
    updated = 0
    for student in students:
        fee = effective_monthly_fee(student)
        if fee <= 0:
            continue
        # month "YYYY-MM" — leksikografik solishtiruv oy tartibiga mos
        for p in student.payments.filter(is_paid=False, month__gt=current_month):
            new_disc = min(int(p.discount or 0), fee)
            if p.amount_due == fee and p.discount == new_disc:
                continue
            p.amount_due = fee
            p.discount = new_disc
            p.save(update_fields=["amount_due", "discount"])
            updated += 1
    return updated


def compute_wallet(student):
    """Bitta o'quvchining barcha oylari bo'yicha kartasini qaytaradi."""
    fee = effective_monthly_fee(student)
    total_due = total_disc = total_paid = 0
    for p in student.payments.all():
        total_due += _payment_due(p.amount_due, fee)
        total_disc += int(p.discount or 0)
        total_paid += int(p.paid_amount or 0)
    w = _wallet_from_totals(total_due, total_disc, total_paid)
    w["monthly_discount"] = student.monthly_discount
    return w


def wallets_for(student_ids):
    """Bir nechta o'quvchi uchun kartani hisoblaydi.

    {student_id: {"balance", "debt", "net", ...}} ko'rinishida qaytaradi.
    amount_due 0 bo'lsa har o'quvchining kurs narxiga tayanadi.
    """
    out = {}
    ids = list(student_ids)
    if not ids:
        return out
    # Har o'quvchining haqiqiy oylik narxi (guruh->kurs prefetch bilan)
    fee_map = {}
    for s in Student.objects.filter(id__in=ids).prefetch_related("groups__course"):
        fee_map[s.id] = effective_monthly_fee(s)

    agg = {}
    for r in Payment.objects.filter(student_id__in=ids).values(
        "student_id", "amount_due", "discount", "paid_amount"
    ):
        sid = r["student_id"]
        a = agg.setdefault(sid, {"due": 0, "disc": 0, "paid": 0})
        a["due"] += _payment_due(r["amount_due"], fee_map.get(sid, 0))
        a["disc"] += int(r["discount"] or 0)
        a["paid"] += int(r["paid_amount"] or 0)

    for sid, a in agg.items():
        out[sid] = _wallet_from_totals(a["due"], a["disc"], a["paid"])
    return out


def attendance_based_due(amount_due, attended, total):
    """Davomatга qarab to'lov = amount_due * kelgan / jami. Jami 0 → None."""
    if not total:
        return None
    try:
        return round(int(amount_due) * attended / total)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def payment_is_ontime(month_str, group, ref_date=None, grace_days=None):
    """To'lov shu kunga (ref_date) qadar vaqtida qilinganmi?

    Vaqtida = to'lov muddati (guruh ochilgan kun) yoki undan keyingi
    grace_days kun ichida. ref_date berilmasa — bugungi (Toshkent) sana.
    """
    due = payment_due_date(month_str, group)
    if due is None:
        return False
    if ref_date is None:
        ref_date = tashkent_today()
    if grace_days is None:
        grace_days = AttendanceCoinSettings.get_settings().payment_grace_days
    return ref_date <= due + timedelta(days=max(int(grace_days), 0))


def sync_payment_ontime_coin(payment):
    """To'lov holatiga qarab 'vaqtida to'lov' coin mukofotini beradi/qaytaradi.

    Idempotent: bir oy uchun eng ko'pi bilan bitta mukofot bo'ladi. To'lov
    bekor qilinsa yoki kechikkan bo'lsa — mukofot qaytariladi.
    """
    student = payment.student
    month = payment.month
    note_prefix = f"{month} oyi"

    existing = CoinTransaction.objects.filter(
        student=student, reason="payment_ontime", note__startswith=note_prefix
    ).first()

    settings_obj = AttendanceCoinSettings.get_settings()
    reward = settings_obj.payment_ontime

    should_reward = bool(
        payment.is_paid
        and reward
        and payment_is_ontime(
            month,
            student_primary_group(student),
            grace_days=settings_obj.payment_grace_days,
        )
    )

    if should_reward and not existing:
        apply_coin_transaction(
            student,
            reward,
            "payment_ontime",
            note=f"{note_prefix} to'lovi vaqtida (+{reward} coin)",
        )
    elif not should_reward and existing:
        # CoinTransaction.delete() balansni avtomatik qaytaradi
        existing.delete()


# ─────────────────────────────────────────
# MANAGER (eng yuqori daraja)
# ─────────────────────────────────────────


@csrf_exempt
def manager_register(request):
    """Yangi menejer yaratish — faqat supermenejer.

    Eski menejer panelidagi "menejer qo'shish" formasi olib tashlandi;
    yangi menejer supermenejer bo'limida vakolatlari bilan birga
    yaratiladi (`super_views.create_super_managed_manager`). Bu endpoint
    eski mijozlar uchun qoldirilgan, lekin endi super talab qiladi.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied
    try:
        data = json.loads(request.body)
        phone = data.get("phone", "").strip()

        if not phone:
            return JsonResponse(
                {"error": "Telefon raqam kiritilishi shart"}, status=400
            )

        if _find_manager_by_any_phone(phone, active_only=False):
            return JsonResponse(
                {"error": "Bu telefon raqam allaqachon ro'yxatdan o'tgan"}, status=400
            )

        manager = Manager.objects.create(
            name=data.get("name", "").strip(),
            surname=data.get("surname", "").strip(),
            phone=phone,
            password=make_password(data.get("password", "")),
            permissions=list(DEFAULT_PERMISSIONS),
        )
        return JsonResponse(
            {
                "id": manager.id,
                "name": manager.name,
                "surname": manager.surname,
                "phone": manager.phone,
                "role": "manager",
                "permissions": manager.permissions,
            },
            status=201,
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def manager_login(request):
    """Menejer login."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        phone = data.get("phone", "").strip()
        password = data.get("password", "")

        if not phone:
            return JsonResponse({"error": "Telefon kiritilishi shart"}, status=400)

        # Raqam qanday formatda kiritilsa ham topiladi ('+998 91 740 40 00',
        # '917404000', '91-740-40-00' — hammasi bir xil menejerga tushadi)
        manager = _find_manager_by_any_phone(phone)

        # Login formasi avval faqat raqamni tekshiradi (parolsiz) —
        # shunda menejer ham "topilmadi" deb rad etilmaydi
        if password is None:
            return JsonResponse({"exists": bool(manager)})

        if not password:
            return JsonResponse(
                {"error": "Telefon va parol kiritilishi shart"}, status=400
            )

        if not manager:
            return JsonResponse({"error": "Menejer topilmadi"}, status=404)

        if not check_password(password, manager.password):
            return JsonResponse({"error": "Parol noto'g'ri"}, status=401)

        if is_device_blocked(request, manager.phone):
            return JsonResponse(
                {"error": "Bu qurilma bloklangan — supermenejerga murojaat qiling"},
                status=403,
            )

        record_login(
            request,
            phone=manager.phone,
            role="super" if manager.is_super else "manager",
            user_name=f"{manager.name} {manager.surname}".strip(),
            manager=manager,
        )

        return JsonResponse(
            {
                "id": manager.id,
                "name": manager.name,
                "surname": manager.surname,
                "phone": manager.phone,
                "role": "manager",
                "is_active": manager.is_active,
                "is_super": manager.is_super,
                # Supermenejerda cheklov yo'q — frontend buni is_super
                # orqali biladi, ro'yxat faqat oddiy menejer uchun
                "permissions": manager.permissions or [],
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def excellence_login(request):
    """Excellence frontend uchun manager login alias."""
    return manager_login(request)


def excellence_info(request):
    """Simple health/info endpoint for the excellence frontend."""
    return JsonResponse(
        {
            "ok": True,
            "message": "Excellence backend yoqilgan",
            "api": "/api/excellence/login/",
        }
    )


def get_managers(request):
    """Menejerlar ro'yxati. ?all=1 — o'chirilganlari (is_active=False) ham."""
    try:
        qs = Manager.objects.all()
        if request.GET.get("all") not in ("1", "true", "yes"):
            qs = qs.filter(is_active=True)
        managers = list(
            qs.order_by("name", "surname").values(
                "id",
                "name",
                "surname",
                "phone",
                "is_active",
                "is_super",
                "created_at",
            )
        )
        return JsonResponse(managers, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def update_manager(request, manager_id):
    """Menejer ma'lumotlarini yangilash.

    Supermenejer yoki 'managers.edit' vakolati bor menejer qila oladi.
    """
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = _require_staff(request) or require_permission(request, "managers.edit")
    if denied:
        return denied
    try:
        data = json.loads(request.body)
        manager = Manager.objects.filter(id=manager_id).first()
        if not manager:
            return JsonResponse({"error": "Menejer topilmadi"}, status=404)

        # Supermenejer akkauntiga faqat supermenejerning o'zi tega oladi
        if manager.is_super and not _caller_is_super(request):
            return JsonResponse(
                {"error": "Supermenejer ma'lumotini faqat supermenejer o'zgartiradi"},
                status=403,
            )

        if "name" in data:
            manager.name = data["name"].strip()
        if "surname" in data:
            manager.surname = data["surname"].strip()
        if "phone" in data:
            new_phone = (data["phone"] or "").strip()
            if not new_phone:
                return JsonResponse(
                    {"error": "Telefon raqam bo'sh bo'lishi mumkin emas"}, status=400
                )
            # Formatdan qat'i nazar solishtiramiz — '917404000' va
            # '+998 91 740 40 00' bir xil raqam
            clash = next(
                (
                    m
                    for m in Manager.objects.exclude(id=manager_id)
                    if _phones_match(m.phone, new_phone)
                ),
                None,
            )
            if clash:
                return JsonResponse(
                    {"error": f"Bu telefon raqam band — {clash.name} {clash.surname}"},
                    status=400,
                )
            manager.phone = new_phone
        if "password" in data and data["password"]:
            manager.password = make_password(data["password"])
        if "is_active" in data:
            manager.is_active = data["is_active"]
        manager.save()
        log_action(
            request,
            "manager.update",
            f"{manager.name} {manager.surname} — ".strip()
            + (
                ("tiklandi" if manager.is_active else "faolsizlantirildi")
                if "is_active" in data
                else ", ".join(sorted(data.keys()))
            ),
            target_type="manager",
            target_id=manager.id,
            target_name=f"{manager.name} {manager.surname}".strip(),
            fields=sorted(data.keys()),
        )
        return JsonResponse({"message": "Menejer ma'lumotlari yangilandi!"})
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def delete_manager(request, manager_id):
    """Menejerni o'chirish (deaktivatsiya)."""
    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = _require_staff(request) or require_permission(request, "managers.edit")
    if denied:
        return denied
    try:
        manager = Manager.objects.filter(id=manager_id).first()
        if not manager:
            return JsonResponse({"error": "Menejer topilmadi"}, status=404)
        if manager.is_super:
            return JsonResponse(
                {"error": "Supermenejerni o'chirib bo'lmaydi"}, status=403
            )
        manager.is_active = False
        manager.save()
        log_action(
            request,
            "manager.delete",
            f"{manager.name} {manager.surname} menejerligi o'chirildi".strip(),
            target_type="manager",
            target_id=manager.id,
            target_name=f"{manager.name} {manager.surname}".strip(),
        )
        return JsonResponse({"message": "Menejer deaktivatsiya qilindi!"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# COIN
# ─────────────────────────────


def get_coin_balance(request, student_id):
    """Student coin balansini ko'rish."""
    try:
        student = Student.objects.filter(id=student_id).first()
        if not student:
            return JsonResponse({"error": "Student topilmadi"}, status=404)
        return JsonResponse(
            {
                "student_id": student.id,
                "student_name": f"{student.name} {student.surname}",
                "coin_balance": student.coin_balance,
            }
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_all_coin_balances(request):
    """Barcha studentlarning coin balansini ko'rish."""
    try:
        teacher_id = request.GET.get("teacher_id", "")
        qs = (
            Student.objects.select_related("teacher")
            .filter(is_admin=False, is_excellence=False)
            .order_by("-coin_balance")
        )

        if teacher_id:
            try:
                qs = qs.filter(teacher_id=int(teacher_id))
            except ValueError:
                return JsonResponse({"error": "Invalid teacher_id"}, status=400)

        data = [
            {
                "student_id": s.id,
                "name": s.name,
                "surname": s.surname,
                "teacher_name": s.teacher.name if s.teacher else "Biriktirilmagan",
                "coin_balance": s.coin_balance,
            }
            for s in qs
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def add_coin(request):
    """Studentga coin berish yoki olish."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)

        student = Student.objects.filter(id=data.get("student_id")).first()
        if not student:
            return JsonResponse({"error": "Student topilmadi"}, status=404)

        amount = data.get("amount")
        if amount is None:
            return JsonResponse({"error": "amount kiritilmadi"}, status=400)

        try:
            amount = int(amount)
        except (ValueError, TypeError):
            return JsonResponse({"error": "amount son bo'lishi kerak"}, status=400)

        if student.coin_balance + amount < 0:
            return JsonResponse(
                {"error": f"Yetarli coin yo'q. Joriy balans: {student.coin_balance}"},
                status=400,
            )

        given_by_teacher = None
        note = data.get("description", "").strip()

        if data.get("manager_id"):
            manager = Manager.objects.filter(
                id=data["manager_id"], is_active=True
            ).first()
            if manager:
                note = f"[Menejer: {manager.name} {manager.surname}] {note}".strip()

        elif data.get("teacher_id"):
            given_by_teacher = Teacher.objects.filter(id=data["teacher_id"]).first()

        new_balance = apply_coin_transaction(
            student,
            amount,
            data.get("reason", "manual"),
            given_by=given_by_teacher,
            note=note,
        )

        return JsonResponse(
            {
                "message": "Coin qo'shildi!" if amount >= 0 else "Coin ayirildi!",
                "amount": amount,
                "new_balance": new_balance,
            },
            status=201,
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def delete_coin_transaction(request, txn_id):
    """Coin tranzaksiyasini bekor qilish."""
    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        txn = CoinTransaction.objects.filter(id=txn_id).first()
        if not txn:
            return JsonResponse({"error": "Tranzaksiya topilmadi"}, status=404)

        Student.objects.filter(pk=txn.student_id).update(
            coin_balance=F("coin_balance") - txn.amount
        )
        txn.delete()
        return JsonResponse(
            {"message": "Tranzaksiya bekor qilindi va balans qayta hisoblandi!"}
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def set_coin_balance(request, student_id):
    """Faqat Manager: student coin balansini to'g'ridan-to'g'ri belgilash."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        new_balance = data.get("coin_balance")
        manager_id = data.get("manager_id")

        if new_balance is None:
            return JsonResponse({"error": "coin_balance kiritilmadi"}, status=400)

        try:
            new_balance = int(new_balance)
        except (ValueError, TypeError):
            return JsonResponse(
                {"error": "coin_balance son bo'lishi kerak"}, status=400
            )

        student = Student.objects.filter(id=student_id).first()
        if not student:
            return JsonResponse({"error": "Student topilmadi"}, status=404)

        diff = new_balance - student.coin_balance
        if diff == 0:
            return JsonResponse(
                {"message": "Balans o'zgarmadi", "coin_balance": student.coin_balance}
            )

        note = f"Balans to'g'ridan-to'g'ri {new_balance} ga o'rnatildi"
        if manager_id:
            manager = Manager.objects.filter(id=manager_id, is_active=True).first()
            if manager:
                note = f"[Menejer: {manager.name} {manager.surname}] {note}"

        updated_balance = apply_coin_transaction(
            student,
            diff,
            "manual",
            note=note,
        )

        return JsonResponse(
            {
                "message": "Balans yangilandi!",
                "coin_balance": updated_balance,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# TEACHERS
# ─────────────────────────────


def get_teachers(request):
    """Barcha o'qituvchilar ro'yxati."""
    try:
        teachers = list(
            Teacher.objects.all().values(
                "id", "name", "phone", "is_senior", "penalty_limit"
            )
        )
        return JsonResponse(teachers, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def create_teacher(request):
    """O'qituvchi yaratish."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    # ⚠️ Bu yerda avval hech qanday ruxsat tekshiruvi yo'q edi — istalgan
    # kishi (hatto kirmagan) ustoz yarata olardi. update_teacher/
    # delete_teacher kabi endi shu yerda ham tekshiriladi.
    denied = _require_staff(request) or require_permission(request, "teachers.add")
    if denied:
        return denied
    try:
        data = json.loads(request.body)
        phone = data.get("phone", "").strip()

        if not phone:
            return JsonResponse(
                {"error": "Telefon raqam kiritilishi shart"}, status=400
            )

        if _find_teacher_by_any_phone(phone):
            return JsonResponse(
                {"error": "Bu telefon raqam allaqachon mavjud"}, status=400
            )

        name = data.get("name", "").strip()
        if not name:
            return JsonResponse({"error": "Ism kiritilishi shart"}, status=400)

        raw_password = (data.get("password") or "").strip()
        teacher = Teacher.objects.create(
            name=name,
            phone=phone,
            # Parol berilmasa bo'sh qoldiriladi — login_student() bunday
            # holatda ism-familiya bo'yicha kirishga ruxsat beradi (xuddi
            # importdan kelgan studentlar kabi). Avval bu yerga
            # ADMIN_PASSWORD qattiq yozilardi, lekin frontendning
            # "ustoz qo'shish" formasida parol maydoni yo'q — shu sabab
            # yangi qo'shilgan ustozlar hech qachon ADMIN_PASSWORD bilan
            # kirishga urinmasdi va "login qila olmayapman" xatosi kelardi.
            password=make_password(raw_password) if raw_password else "",
            is_senior=data.get("is_senior", False),
        )
        log_action(
            request,
            "teacher.create",
            f"{name} ustoz sifatida qo'shildi ({phone})",
            target_type="teacher",
            target_id=teacher.id,
            target_name=name,
        )
        return JsonResponse(
            {"id": teacher.id, "name": teacher.name, "phone": teacher.phone}, status=201
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def delete_teacher(request, teacher_id):
    """O'qituvchini o'chirish.

    ?to_teacher_id=<id> berilsa, o'quvchilar va guruhlar avval o'sha
    ustozga o'tkaziladi. Berilmasa ular biriktirilmagan holga tushadi
    (FK SET_NULL) — bu holda nechta o'quvchi bo'shab qolgani javobda
    qaytariladi, menejer keyin biriktirib qo'yishi mumkin.
    """
    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = _require_staff(request)
    if denied:
        return denied
    try:
        teacher = Teacher.objects.filter(id=teacher_id).first()
        if not teacher:
            return JsonResponse({"error": "O'qituvchi topilmadi"}, status=404)

        to_id = request.GET.get("to_teacher_id")
        to_teacher = None
        if to_id:
            if str(to_id) == str(teacher_id):
                return JsonResponse(
                    {
                        "error": "O'quvchilarni o'sha ustozning o'ziga o'tkazib bo'lmaydi"
                    },
                    status=400,
                )
            to_teacher = Teacher.objects.filter(id=to_id).first()
            if not to_teacher:
                return JsonResponse(
                    {"error": "Qabul qiluvchi o'qituvchi topilmadi"}, status=404
                )

        with transaction.atomic():
            # Ustozning o'z admin profili o'quvchi sifatida qolib
            # ketmasligi kerak — u ustoz bilan birga o'chadi
            admin_profiles = Student.objects.filter(
                teacher_id=teacher_id, is_admin=True
            )
            admin_count = admin_profiles.count()
            admin_profiles.delete()

            real_students = Student.objects.filter(teacher_id=teacher_id)
            moved = real_students.count()
            if to_teacher:
                real_students.update(teacher_id=to_teacher.id, manual_teacher=True)
                Group.objects.filter(teacher_id=teacher_id).update(
                    teacher_id=to_teacher.id
                )

            name = teacher.name
            teacher.delete()

        log_action(
            request,
            "teacher.delete",
            f"{name} o'chirildi — {moved} ta o'quvchi "
            + (
                f"{to_teacher.name}ga o'tkazildi"
                if to_teacher
                else "biriktirilmagan holga tushdi"
            ),
            target_type="teacher",
            target_id=teacher_id,
            target_name=name,
            students_moved=moved,
            to_teacher=to_teacher.name if to_teacher else None,
        )

        return JsonResponse(
            {
                "message": (
                    f"{name} o'chirildi — {moved} ta o'quvchi "
                    + (
                        f"{to_teacher.name}ga o'tkazildi"
                        if to_teacher
                        else "biriktirilmagan holga tushdi"
                    )
                ),
                "students_moved": moved,
                "admin_profiles_deleted": admin_count,
                "to_teacher_id": to_teacher.id if to_teacher else None,
            }
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def update_teacher(request, teacher_id):
    """O'qituvchi ma'lumotlarini yangilash."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = _require_staff(request)
    if denied:
        return denied
    try:
        data = json.loads(request.body)
        teacher = Teacher.objects.filter(id=teacher_id).first()
        if not teacher:
            return JsonResponse({"error": "O'qituvchi topilmadi"}, status=404)

        old_phone = teacher.phone
        if "name" in data:
            teacher.name = data["name"].strip()

        if "phone" in data:
            new_phone = (data["phone"] or "").strip()
            if not new_phone:
                return JsonResponse(
                    {"error": "Telefon raqam bo'sh bo'lishi mumkin emas"}, status=400
                )
            # Formatdan qat'i nazar solishtiramiz — aks holda bir raqam
            # ikki xil yozuvda ikki marta saqlanib qolardi
            clash = next(
                (
                    t
                    for t in Teacher.objects.exclude(id=teacher_id)
                    if _phones_match(t.phone, new_phone)
                ),
                None,
            )
            if clash:
                return JsonResponse(
                    {"error": f"Bu telefon raqam band — {clash.name}"}, status=400
                )
            teacher.phone = new_phone

            # ✅ MUHIM: Bog'langan Student.is_admin/is_excellence yozuvini ham yangilash
            Student.objects.filter(teacher_id=teacher_id, is_admin=True).update(
                phone=new_phone
            )

            Student.objects.filter(teacher_id=teacher_id, is_excellence=True).update(
                phone=new_phone
            )

        if "is_senior" in data:
            teacher.is_senior = data["is_senior"]
        if "penalty_limit" in data:
            try:
                teacher.penalty_limit = int(data["penalty_limit"])
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "penalty_limit son bo'lishi kerak"}, status=400
                )

        teacher.save()

        # Ustozning admin profili (Student.is_admin) ham shu raqam bilan
        # login qiladi — aks holda raqam o'zgargach ustoz tizimga kira
        # olmay qolardi
        if "phone" in data and not _phones_match(old_phone, teacher.phone):
            # Student.phone unikal — raqamni allaqachon boshqa o'quvchi
            # egallagan bo'lsa (aka-uka bir raqamni ishlatsa) uni phone2
            # ga yozamiz, login ikkala maydonni ham tekshiradi
            for prof in Student.objects.filter(teacher_id=teacher_id, is_admin=True):
                if not (
                    _phones_match(prof.phone, old_phone)
                    or _phones_match(prof.phone2, old_phone)
                ):
                    continue
                taken = (
                    Student.objects.filter(phone=teacher.phone)
                    .exclude(id=prof.id)
                    .exists()
                )
                if taken:
                    prof.phone2 = teacher.phone[:50]
                else:
                    prof.phone = teacher.phone[:20]
                    if _phones_match(prof.phone2, teacher.phone):
                        prof.phone2 = ""
                prof.save(update_fields=["phone", "phone2"])

        log_action(
            request,
            "teacher.update",
            f"{teacher.name} ma'lumoti tahrirlandi: "
            + (", ".join(sorted(data.keys())) or "o'zgarishsiz"),
            target_type="teacher",
            target_id=teacher.id,
            target_name=teacher.name,
            fields=sorted(data.keys()),
        )

        return JsonResponse(
            {
                "message": "O'qituvchi yangilandi!",
                "id": teacher.id,
                "name": teacher.name,
                "phone": teacher.phone,
                "is_senior": teacher.is_senior,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def update_teacher_penalty_limit(request, teacher_id):
    """O'qituvchining ja'zo chegarasini yangilash."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        teacher = Teacher.objects.filter(id=teacher_id).first()
        if not teacher:
            return JsonResponse({"error": "O'qituvchi topilmadi"}, status=404)

        penalty_limit = data.get("penalty_limit")
        if penalty_limit is None:
            return JsonResponse({"error": "penalty_limit kiritilmadi"}, status=400)

        try:
            teacher.penalty_limit = int(penalty_limit)
        except (ValueError, TypeError):
            return JsonResponse(
                {"error": "penalty_limit son bo'lishi kerak"}, status=400
            )
        teacher.save()
        return JsonResponse({"penalty_limit": teacher.penalty_limit})
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def reassign_students(request):
    """O'quvchilarni o'qituvchidan boshqa o'qituvchiga o'tkazish."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = _require_staff(request)
    if denied:
        return denied
    try:
        data = json.loads(request.body)
        from_teacher_id = data.get("from_teacher_id")
        to_teacher_id = data.get("to_teacher_id")

        if not from_teacher_id or not to_teacher_id:
            return JsonResponse(
                {"error": "from_teacher_id va to_teacher_id kiritilishi shart"},
                status=400,
            )

        if not Teacher.objects.filter(id=to_teacher_id).exists():
            return JsonResponse({"error": "Yangi o'qituvchi topilmadi"}, status=404)

        # Faqat o'quvchilar ko'chadi — ustozning o'z admin/menejer profili
        # eski ustozga bog'langan holicha qoladi
        updated = Student.objects.filter(
            teacher_id=from_teacher_id, is_admin=False, is_excellence=False
        ).update(teacher_id=to_teacher_id, manual_teacher=True)
        return JsonResponse(
            {"message": f"{updated} ta o'quvchi o'tkazildi!", "count": updated}
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# MENEJER PANELI
# ─────────────────────────────


def sheet_import_status(request):
    """Sheet importi holati — deploy'dan keyin tekshirish uchun.

    Import server ko'tarilganda fon oqimida ishlaydi va atomic; xato
    bo'lsa hammasi qaytariladi va tashqaridan hech narsa o'zgarmagandek
    ko'rinadi. Shu sababli xatoning o'zi ham shu yerda ko'rsatiladi.
    """
    from .models import SheetImportMeta
    from .management.commands.load_sheet_data import DATA_VERSION

    meta = SheetImportMeta.objects.filter(pk=1).first()
    return JsonResponse(
        {
            "code_version": DATA_VERSION,
            "db_version": meta.version if meta else None,
            "up_to_date": bool(meta and meta.version == DATA_VERSION),
            "imported_at": meta.imported_at.isoformat() if meta else None,
            "last_error": (meta.last_error if meta else "") or "",
            "counts": {
                "teachers": Teacher.objects.count(),
                "students": Student.objects.count(),
                "managers": Manager.objects.count(),
                "leads": Lead.objects.count(),
                "groups": Group.objects.count(),
            },
        }
    )


def _require_staff(request):
    """Chaqiruvchi menejer yoki ustozmi — shuni tekshiradi.

    ⚠️ Bu TO'LIQ AUTENTIFIKATSIYA EMAS. Loyihada sessiya/token tizimi
    yo'q, shuning uchun bu yerda faqat 'X-User-Phone' sarlavhasi
    tekshiriladi — uni qo'lda soxtalashtirish mumkin. Maqsadi: ustoz
    o'chirish va o'quvchi ko'chirish kabi qaytarib bo'lmaydigan
    amallar tasodifan yoki URL'ni bilgan begona odam tomonidan
    ishga tushib ketmasin. Haqiqiy himoya uchun token/sessiya
    autentifikatsiyasi alohida qo'shilishi kerak.

    Mos kelsa None, aks holda tayyor 403 javobini qaytaradi.
    """
    phone = (request.headers.get("X-User-Phone") or "").strip()
    if phone and (
        _find_manager_by_any_phone(phone) or _find_teacher_by_any_phone(phone)
    ):
        return None
    return JsonResponse(
        {"error": "Bu amal uchun menejer yoki ustoz sifatida kirish kerak"},
        status=403,
    )


def _caller_is_super(request):
    """Chaqiruvchi supermenejermi."""
    manager = caller_manager(request)
    return bool(manager and manager.is_super)


def _caller_own_teacher(request):
    """Chaqiruvchi oddiy ustoz bo'lsa — uning Teacher yozuvi.

    Ustoz boshqa ustozning guruhlarini ko'rmasligi uchun ishlatiladi.
    Menejer va panel darajasidagi (is_excellence) foydalanuvchilar uchun
    None qaytaradi — ular hamma guruhni ko'raveradi. Sarlavha
    bo'lmasa ham None: eski mijozlar ishlashda davom etadi.
    """
    phone = (request.headers.get("X-User-Phone") or "").strip()
    if not phone:
        return None
    if _find_manager_by_any_phone(phone):
        return None

    # Panel darajasidagi o'quvchi profili (menejer huquqi) — cheklanmaydi
    key = _phone_key(phone)
    if len(key) >= MIN_PHONE_KEY_LEN:
        panel_user = next(
            (
                s
                for s in Student.objects.filter(is_excellence=True).only(
                    "id", "phone", "phone2"
                )
                if _phone_key(s.phone) == key or _phone_key(s.phone2) == key
            ),
            None,
        )
        if panel_user:
            return None

    return _find_teacher_by_any_phone(phone)


def _real_students():
    """Haqiqiy o'quvchilar — ustozlarning admin profillari kirmaydi."""
    return Student.objects.filter(is_admin=False, is_excellence=False)


def get_teachers_overview(request):
    """Menejer uchun ustozlar sahifasi — har biri bo'yicha statistika.

    Login qila oladimi (`can_login`) ham qaytariladi: import paytida
    telefoni to'liq kelmagan ustozlarga shartli kod berilgan, ular
    raqami kiritilmaguncha tizimga kira olmaydi.
    """
    try:
        counts = dict(
            _real_students()
            .filter(teacher__isnull=False)
            .values_list("teacher_id")
            .annotate(n=db_models.Count("id"))
        )
        groups = dict(
            Group.objects.filter(teacher__isnull=False)
            .values_list("teacher_id")
            .annotate(n=db_models.Count("id"))
        )
        data = []
        for t in Teacher.objects.order_by("name"):
            key = _phone_key(t.phone)
            data.append(
                {
                    "id": t.id,
                    "name": t.name,
                    "phone": t.phone,
                    "is_senior": t.is_senior,
                    "penalty_limit": t.penalty_limit,
                    "students_count": counts.get(t.id, 0),
                    "groups_count": groups.get(t.id, 0),
                    "can_login": len(key) >= MIN_PHONE_KEY_LEN,
                    # O'zbek raqami 9 xonali — undan qisqasi jadvaldan
                    # chala kelgan, menejer to'g'rilashi kerak
                    "phone_complete": len(key) == 9,
                    "phone_note": (
                        ""
                        if len(key) == 9
                        else (
                            "Telefon raqam yo'q — tizimga kira olmaydi"
                            if len(key) < MIN_PHONE_KEY_LEN
                            else f"Raqam to'liq emas ({len(key)} xonali) — tekshiring"
                        )
                    ),
                }
            )
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_students_overview(request):
    """Barcha o'quvchilar va ular biriktirilgan ustoz.

    Filtrlar: ?teacher_id=<id> — bitta ustozning o'quvchilari,
              ?teacher_id=none — biriktirilmaganlar,
              ?search=<matn> — ism yoki telefon bo'yicha,
              ?include_graduates=1 — bitiruvchilar ham.
    """
    try:
        qs = _real_students().select_related("teacher")
        if request.GET.get("include_graduates") not in ("1", "true", "yes"):
            qs = qs.filter(is_graduate=False)

        teacher_id = (request.GET.get("teacher_id") or "").strip()
        if teacher_id in ("none", "null", "0"):
            qs = qs.filter(teacher__isnull=True)
        elif teacher_id:
            try:
                qs = qs.filter(teacher_id=int(teacher_id))
            except ValueError:
                return JsonResponse({"error": "Invalid teacher_id"}, status=400)

        search = (request.GET.get("search") or "").strip()
        rows = list(qs.order_by("name", "surname"))
        if search:
            digits = _re.sub(r"\D", "", search)
            # '+998 91 740 40 00' kabi qidiruvda mamlakat kodi bazadagi
            # yozuvda yo'q — uni olib tashlaymiz
            if len(digits) > 9 and digits.startswith("998"):
                digits = digits[3:]
            low = search.lower()

            def hit(s):
                if low in f"{s.name} {s.surname}".lower():
                    return True
                # Saqlangan raqamda bo'shliq bor ('91 740 40 00'), shuning
                # uchun ikkala tomonni ham raqamlargacha tozalab solishtiramiz
                if len(digits) >= 3:
                    stored = _re.sub(r"\D", "", f"{s.phone} {s.phone2}")
                    return digits in stored
                return False

            rows = [s for s in rows if hit(s)]

        wallet_map = wallets_for([s.id for s in rows])
        data = [
            {
                "id": s.id,
                "name": s.name,
                "surname": s.surname,
                # Import paytida raqami band bo'lgan o'quvchilarga '—0001'
                # kabi shartli kod berilgan — uni ko'rsatmaymiz
                "phone": "" if (s.phone or "").startswith("—") else (s.phone or ""),
                "phone2": s.phone2,
                "teacher_id": s.teacher_id,
                "teacher_name": s.teacher.name if s.teacher else "",
                "stage": s.stage,
                "schedule": s.schedule,
                "is_graduate": s.is_graduate,
                "coin_balance": s.coin_balance,
                "monthly_discount": s.monthly_discount,
                "wallet_balance": wallet_map.get(s.id, {}).get("balance", 0),
                "wallet_debt": wallet_map.get(s.id, {}).get("debt", 0),
                # Yuz tanish terminalidagi raqami (bo'lmasa bo'sh satr)
                "face_person_id": s.face_person_id,
            }
            for s in rows
        ]
        return JsonResponse(
            {
                "count": len(data),
                "students": data,
                "hidden": _hidden_phone_holders(search) if search and not data else [],
            }
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _hidden_phone_holders(search):
    """Qidiruvda ko'rinmaydigan, lekin raqamni band qilib turgan yozuvlar.

    Bu ro'yxat uchta filtrni chetlab o'tadi: ustozning admin profili,
    menejerning profili va bitiruvchi. Ular ro'yxatda ko'rsatilmaydi,
    lekin login va ro'yxatdan o'tkazish ularni KO'RADI — ya'ni menejer
    "hech narsa topilmadi" deb turganda bot "bu raqam bazada bor"
    deyishi mumkin edi. Sababini aytmasak, izlash imkonsiz.
    """
    key = _phone_key(search)
    if len(key) < MIN_PHONE_KEY_LEN:
        return []

    out = []
    for s in Student.objects.filter(is_graduate=True):
        if key in (_phone_key(s.phone), _phone_key(s.phone2)):
            out.append(
                {
                    "id": s.id,
                    "name": f"{s.name} {s.surname}".strip(),
                    "kind": "bitiruvchi",
                }
            )
    for s in Student.objects.filter(is_admin=True):
        if key in (_phone_key(s.phone), _phone_key(s.phone2)):
            out.append(
                {
                    "id": s.id,
                    "name": f"{s.name} {s.surname}".strip(),
                    "kind": "ustoz profili",
                }
            )
    for s in Student.objects.filter(is_excellence=True):
        if key in (_phone_key(s.phone), _phone_key(s.phone2)):
            out.append(
                {
                    "id": s.id,
                    "name": f"{s.name} {s.surname}".strip(),
                    "kind": "menejer profili",
                }
            )
    return out


@csrf_exempt
def transfer_students(request):
    """Tanlangan o'quvchilarni boshqa ustozga o'tkazadi.

    Body: {student_ids: [1, 2, ...], to_teacher_id: <id>,
           detach_old_groups: true}

    Bir nechta o'quvchini birdaniga belgilab o'tkazish uchun.
    Standart holatda o'quvchi eski ustozning guruhlaridan chiqariladi —
    aks holda u yangi ustozga tegishli bo'lsa-da, eski ustozning
    davomat ro'yxatida qolib ketardi.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = _require_staff(request)
    if denied:
        return denied
    try:
        data = json.loads(request.body)
        ids = data.get("student_ids") or []
        to_teacher_id = data.get("to_teacher_id")

        if not isinstance(ids, list) or not ids:
            return JsonResponse(
                {"error": "student_ids — bo'sh bo'lmagan ro'yxat bo'lishi kerak"},
                status=400,
            )
        if not to_teacher_id:
            return JsonResponse(
                {"error": "to_teacher_id kiritilishi shart"}, status=400
            )

        to_teacher = Teacher.objects.filter(id=to_teacher_id).first()
        if not to_teacher:
            return JsonResponse({"error": "Yangi o'qituvchi topilmadi"}, status=404)

        try:
            ids = [int(i) for i in ids]
        except (ValueError, TypeError):
            return JsonResponse(
                {"error": "student_ids butun son bo'lishi kerak"}, status=400
            )

        students = list(_real_students().filter(id__in=ids))
        found = {s.id for s in students}
        missing = [i for i in ids if i not in found]

        detach = data.get("detach_old_groups", True)
        detached = 0
        with transaction.atomic():
            if detach:
                for s in students:
                    if s.teacher_id and s.teacher_id != to_teacher.id:
                        old_groups = s.groups.filter(teacher_id=s.teacher_id)
                        detached += old_groups.count()
                        for g in old_groups:
                            g.students.remove(s)
            # manual_teacher — sheet qayta import qilinganda bu biriktiruv
            # tiklanadi, aks holda menejerning ishi deployda yo'qolardi
            moved = (
                _real_students()
                .filter(id__in=found)
                .update(teacher_id=to_teacher.id, manual_teacher=True)
            )

        log_action(
            request,
            "student.transfer",
            f"{moved} ta o'quvchi {to_teacher.name}ga ko'chirildi",
            target_type="teacher",
            target_id=to_teacher.id,
            target_name=to_teacher.name,
            count=moved,
            groups_detached=detached,
        )

        return JsonResponse(
            {
                "message": f"{moved} ta o'quvchi {to_teacher.name}ga o'tkazildi",
                "count": moved,
                "to_teacher_id": to_teacher.id,
                "to_teacher_name": to_teacher.name,
                "groups_detached": detached,
                "not_found": missing,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# STAGE PRICES
# ─────────────────────────────


def get_stage_prices(request):
    """Etaplar bo'yicha narxlar."""
    try:
        prices = list(
            StagePrice.objects.all().order_by("stage").values("id", "stage", "price")
        )
        return JsonResponse(prices, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def update_stage_price(request, stage):
    """Etap narxini yangilash."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        price = data.get("price")
        if price is None:
            return JsonResponse({"error": "price kiritilmadi"}, status=400)

        try:
            stage = int(stage)
            price = int(price)
        except (ValueError, TypeError):
            return JsonResponse(
                {"error": "stage va price sonlar bo'lishi kerak"}, status=400
            )

        sp, _ = StagePrice.objects.get_or_create(stage=stage, defaults={"price": price})
        price_changed = sp.price != price
        sp.price = price
        sp.save()

        # Narx o'zgardi — shu stage'dagi o'quvchilarning to'lanmagan
        # to'lovlari joriy narxga yangilanadi (reaktiv)
        synced = 0
        if price_changed:
            synced = resync_unpaid_amount_due(
                Student.objects.filter(stage=stage).prefetch_related(
                    "payments", "groups"
                )
            )
        return JsonResponse(
            {"stage": stage, "price": sp.price, "payments_synced": synced}
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# STUDENTS
# ─────────────────────────────


def get_students(request):
    """O'quvchilar ro'yxati."""
    try:
        teacher_id = request.GET.get("teacher_id")
        qs = Student.objects.select_related("teacher").filter(
            is_admin=False, is_excellence=False, is_graduate=False
        )
        if teacher_id and teacher_id != "null":
            try:
                qs = qs.filter(teacher_id=int(teacher_id))
            except ValueError:
                return JsonResponse({"error": "Invalid teacher_id"}, status=400)

        rows = list(qs)
        wallet_map = wallets_for([s.id for s in rows])
        data = [
            {
                "id": s.id,
                "name": s.name,
                "surname": s.surname,
                "phone": "" if (s.phone or "").startswith("—") else (s.phone or ""),
                "phone2": s.phone2,
                "teacher_id": s.teacher_id,
                "teacher_name": s.teacher.name if s.teacher else "Biriktirilmagan",
                "stage": s.stage,
                "schedule": s.schedule,
                "coin_balance": s.coin_balance,
                "monthly_discount": s.monthly_discount,
                "wallet_balance": wallet_map.get(s.id, {}).get("balance", 0),
                "wallet_debt": wallet_map.get(s.id, {}).get("debt", 0),
            }
            for s in rows
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def update_student(request, student_id):
    """O'quvchi ma'lumotlarini yangilash."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        student = (
            Student.objects.select_related("teacher").filter(id=student_id).first()
        )
        if not student:
            return JsonResponse({"error": "Student topilmadi"}, status=404)

        if "stage" in data:
            try:
                student.stage = int(data["stage"])
            except (ValueError, TypeError):
                return JsonResponse({"error": "stage son bo'lishi kerak"}, status=400)

            if student.stage == 5:
                senior_teacher = Teacher.objects.filter(is_senior=True).first()
                if senior_teacher:
                    student.teacher = senior_teacher

        if "schedule" in data:
            if data["schedule"] not in ["odd", "even", "daily"]:
                return JsonResponse(
                    {"error": "schedule 'odd' yoki 'even' bo'lishi kerak"}, status=400
                )
            student.schedule = data["schedule"]

        # ✅ Doimiy oylik chegirma. O'zgartirilganda mavjud (hali to'lanmagan)
        # oylarга ham qo'llanadi — to'langan oylar tegilmaydi.
        monthly_discount_changed = False
        if "monthly_discount" in data:
            try:
                student.monthly_discount = max(0, int(data["monthly_discount"]))
                monthly_discount_changed = True
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "monthly_discount son bo'lishi kerak"}, status=400
                )

        student.save()

        if monthly_discount_changed:
            for p in student.payments.filter(is_paid=False):
                new_disc = max(0, min(student.monthly_discount, p.amount_due))
                if p.discount != new_disc:
                    p.discount = new_disc
                    p.save(update_fields=["discount"])

        # Doimiy chegirma o'zgarishi pulga tegadi — alohida ko'rsatamiz
        if monthly_discount_changed:
            detail = f"doimiy chegirma {student.monthly_discount:,} so'm".replace(
                ",", " "
            )
        else:
            detail = ", ".join(sorted(data.keys())) or "o'zgarishsiz"
        log_action(
            request,
            "student.update",
            f"{student.name} {student.surname} — {detail}".strip(),
            target_type="student",
            target_id=student.id,
            target_name=f"{student.name} {student.surname}".strip(),
            fields=sorted(data.keys()),
        )

        wallet = compute_wallet(student)
        return JsonResponse(
            {
                "message": "O'quvchi yangilandi!",
                "stage": student.stage,
                "schedule": student.schedule,
                "teacher_id": student.teacher_id,
                "teacher_name": student.teacher.name if student.teacher else "",
                "monthly_discount": student.monthly_discount,
                "wallet_balance": wallet.get("balance", 0),
                "wallet_debt": wallet.get("debt", 0),
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


import re as _re

# Solishtirish uchun kalit shu uzunlikdan qisqa bo'lsa ishlatilmaydi —
# aks holda import paytida berilgan shartli kodlar ('t0014', '—0007')
# bir-biriga mos kelib ketardi
MIN_PHONE_KEY_LEN = 7


def _phone_key(phone):
    """Telefonni solishtirish uchun normal ko'rinishga keltiradi.

    Format qanday bo'lishidan qat'i nazar bir xil natija beradi:
    '+998 91 740 40 00', '917404000', '91-740-40-00' → '917404000'.

    Jadvalda to'liq kiritilmagan qisqa raqamlar (masalan 8 xonali
    '91858990') ham o'z raqamlari bilan qaytariladi — eski versiya
    ularni bo'sh satrga aylantirgani uchun bunday ustozlar hech
    qachon topilmasdi va tizimga kira olmasdi.
    """
    d = _re.sub(r"\D", "", str(phone or ""))
    if len(d) > 9 and d.startswith("998"):
        d = d[3:]
    return d[-9:] if len(d) >= 9 else d


# Eski nom — PhoneVerification va TelegramSubscriber yozuvlari shu
# kalit bilan saqlangan, 9 xonali raqamlar uchun natija o'zgarmagan
_digits9 = _phone_key


def _phones_match(a, b):
    """Ikki telefon (format farqidan qat'i nazar) bir xilmi."""
    ka, kb = _phone_key(a), _phone_key(b)
    return bool(ka) and len(ka) >= MIN_PHONE_KEY_LEN and ka == kb


def _find_students_by_any_phone(phone):
    """Telefon bo'yicha barcha mos studentlarni qaytaradi.

    Format farqi hisobga olinmaydi ('+998903068558' == '90 306 85 58').
    Bir nechta bo'lishi normal — aka-uka bir xil ota-ona raqamini
    ishlatsa, ular parol (ism-familiya) bo'yicha ajratiladi.
    """
    target = _phone_key(phone)
    if len(target) < MIN_PHONE_KEY_LEN:
        return list(Student.objects.select_related("teacher").filter(phone=phone))
    return [
        s
        for s in Student.objects.select_related("teacher").all()
        if _phone_key(s.phone) == target or _phone_key(s.phone2) == target
    ]


def _find_student_by_any_phone(phone):
    """Telefon bo'yicha bitta student (mavjudligini tekshirish uchun)."""
    matches = _find_students_by_any_phone(phone)
    return matches[0] if matches else None


def _find_teacher_by_any_phone(phone):
    """O'qituvchini telefon bo'yicha topadi — format farqiga qaramasdan."""
    exact = Teacher.objects.filter(phone=phone).first()
    if exact:
        return exact
    target = _phone_key(phone)
    if len(target) < MIN_PHONE_KEY_LEN:
        return None
    for t in Teacher.objects.all():
        if _phone_key(t.phone) == target:
            return t
    return None


def _find_manager_by_any_phone(phone, active_only=True):
    """Menejerni telefon bo'yicha topadi — format farqiga qaramasdan."""
    qs = (
        Manager.objects.filter(is_active=True) if active_only else Manager.objects.all()
    )
    exact = qs.filter(phone=phone).first()
    if exact:
        return exact
    target = _phone_key(phone)
    if len(target) < MIN_PHONE_KEY_LEN:
        return None
    for m in qs:
        if _phone_key(m.phone) == target:
            return m
    return None


def _find_admin_student_by_phone(phone):
    """Telefon bo'yicha admin/excellence o'quvchini topadi (format farqiga qaramay)."""
    target = _phone_key(phone)
    if len(target) < MIN_PHONE_KEY_LEN:
        return None
    for s in Student.objects.filter(
        db_models.Q(is_admin=True) | db_models.Q(is_excellence=True)
    ).only("id", "phone", "phone2"):
        if _phone_key(s.phone) == target or _phone_key(s.phone2) == target:
            return s
    return None


def _require_manager_or_admin(request):
    """Chaqiruvchi menejer yoki admin o'quvchimi — shuni tekshiradi.

    `_require_staff`'dan farqi: oddiy ustozlarga ruxsat bermaydi — faqat
    menejer yoki is_admin/is_excellence o'quvchi. O'quvchini butunlay
    o'chirish kabi qaytarib bo'lmaydigan amallar uchun.

    ⚠️ Bu TO'LIQ AUTENTIFIKATSIYA EMAS — 'X-User-Phone' sarlavhasini
    soxtalashtirish mumkin. Maqsadi: begona yoki oddiy ustoz tasodifan
    o'chirib yubormasin. Mos kelsa None, aks holda 403 javob.
    """
    phone = (request.headers.get("X-User-Phone") or "").strip()
    if phone and (
        _find_manager_by_any_phone(phone) or _find_admin_student_by_phone(phone)
    ):
        return None
    return JsonResponse(
        {"error": "Bu amal uchun admin yoki menejer sifatida kirish kerak"},
        status=403,
    )


# Jadvalda ismlar turk/nemis harflari bilan kelgan ('Möydınov',
# 'Damırov') — egasi ularni klaviaturada tera olmaydi. NFKD ajratmaydigan
# harflarni qo'lda moslashtiramiz, qolganini NFKD hal qiladi.
_LOOKALIKE = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "ş": "s",
        "Ş": "s",
        "ğ": "g",
        "Ğ": "g",
        "ç": "c",
        "Ç": "c",
        "ö": "o",
        "Ö": "o",
        "ü": "u",
        "Ü": "u",
        "ə": "a",
        "æ": "a",
        "ø": "o",
        "ß": "s",
    }
)


def _fold_name(value):
    """Ismni solishtirish uchun sodda ko'rinishga keltiradi.

    Katta-kichik harf, bo'shliq, apostrof va diakritik belgilar
    hisobga olinmaydi: 'Möydınov' va 'moydinov' bir xil.
    """
    import unicodedata

    s = str(value or "").lower().translate(_LOOKALIKE)
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if ch.isalnum() and not unicodedata.combining(ch))


def _name_password_matches(student, password):
    """Import qilingan studentlar uchun parol — ism va familiya.

    'Abdulloh Ibrohimov', 'abdullohibrohimov', 'Ibrohimov Abdulloh' —
    hammasi to'g'ri. Jadvaldan ismga qo'shilib kelgan bir-ikki harfli
    qoldiqlar ("Abdurovuf Möydınov y" dagi 'y') ham talab qilinmaydi.
    """
    typed = _fold_name(password)
    if not typed:
        return False

    # Teacher modelida 'surname' maydoni yo'q — shu sabab getattr bilan
    # ehtiyotkorlik qilinadi (ism yolg'iz ham yetadi)
    full_name = f"{student.name} {getattr(student, 'surname', '')}".strip()
    tokens = [t for t in (_fold_name(p) for p in full_name.split()) if t]
    if not tokens:
        return False

    # jadval qoldig'i bo'lgan qisqa bo'laklarsiz variant
    core = [t for t in tokens if len(t) > 2] or tokens

    forms = set()
    for variant in (tokens, core):
        forms.add("".join(variant))
        forms.add("".join(reversed(variant)))
    return typed in forms


@csrf_exempt
def register_student(request):
    """O'quvchi ro'yxatdan o'tkazish."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        phone = data.get("phone", "").strip()

        if not phone:
            return JsonResponse(
                {"error": "Telefon raqam kiritilishi shart"}, status=400
            )

        existing = _find_student_by_any_phone(phone)
        if existing:
            # Kim egalik qilayotganini aytamiz. Ustoz/menejer profillari
            # hech qaysi ro'yxatda ko'rinmaydi — "allaqachon bor" degan
            # quruq xabar bilan ularni topib bo'lmasdi.
            kind = (
                "menejer profili"
                if existing.is_excellence
                else "ustoz profili" if existing.is_admin else "o'quvchi"
            )
            who = f"{existing.name} {existing.surname}".strip()
            return JsonResponse(
                {
                    "error": (
                        f"Bu raqam allaqachon band — {who} ({kind}). "
                        "Kirish uchun parol sifatida ism va familiyangizni yozing."
                    ),
                    "holder": {
                        "id": existing.id,
                        "name": who,
                        "kind": kind,
                        "is_admin": existing.is_admin,
                        "is_excellence": existing.is_excellence,
                    },
                },
                status=400,
            )

        name = data.get("name", "").strip()
        if not name:
            return JsonResponse({"error": "Ism kiritilishi shart"}, status=400)

        admin_password = data.get("admin_password", "")
        excellence_password = data.get("excellence_password", "")

        is_admin = admin_password == ADMIN_PASSWORD
        is_excellence = excellence_password == EXCELLENCE_PASSWORD

        teacher = None
        if not is_admin and not is_excellence:
            teacher_id = data.get("teacher_id")
            if teacher_id:
                teacher = Teacher.objects.filter(id=teacher_id).first()

        password = (
            make_password(data.get("password", "")) if data.get("password") else ""
        )

        student = Student.objects.create(
            name=name,
            surname=data.get("surname", "").strip(),
            phone=phone,
            password=password,
            teacher=teacher,
            is_admin=is_admin,
            is_excellence=is_excellence,
            schedule=data.get("schedule", "odd"),
        )

        if is_admin or is_excellence:
            new_teacher = Teacher.objects.create(
                name=f"{name} {data.get('surname', '')}".strip(),
                phone=phone,
                is_senior=is_excellence,
            )
            student.teacher = new_teacher
            student.save()

        # Botga oldindan ulangan chat'ni yangi o'quvchiga bog'laymiz —
        # shundan keyin unga xabarlar to'g'ridan-to'g'ri boradi
        from .models import TelegramSubscriber

        TelegramSubscriber.objects.filter(
            phone=_digits9(phone), student__isnull=True
        ).update(student=student)

        role = (
            "menejer profili"
            if student.is_excellence
            else "ustoz profili" if student.is_admin else "o'quvchi"
        )
        log_action(
            request,
            "student.create",
            f"{student.name} {student.surname} qo'shildi — {role}"
            + (f", ustoz {student.teacher.name}" if student.teacher else ""),
            target_type="student",
            target_id=student.id,
            target_name=f"{student.name} {student.surname}".strip(),
            role=role,
        )

        return JsonResponse(
            {
                "message": "O'quvchi muvaffaqiyatli ro'yxatdan o'tdi!",
                "id": student.id,
                "name": student.name,
                "surname": student.surname,
                "phone": student.phone,
                "teacher_id": student.teacher_id,
                "teacher_name": student.teacher.name if student.teacher else "",
                "stage": student.stage,
                "schedule": student.schedule,
                "is_admin": student.is_admin,
                "is_excellence": student.is_excellence,
                "coin_balance": student.coin_balance,
            },
            status=201,
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def login_student(request):
    """O'quvchi va o'qituvchi login."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        phone = data.get("phone", "").strip()
        password = data.get("password")

        if not phone:
            return JsonResponse(
                {"error": "Telefon raqam kiritilishi shart"}, status=400
            )

        candidates = _find_students_by_any_phone(phone)

        if password is None:
            return JsonResponse({"exists": bool(candidates)})

        # Bir xil raqamli bir nechta o'quvchi bo'lishi mumkin (aka-uka) —
        # parolga mos kelganini tanlaymiz
        student, password_ok = None, False
        for cand in candidates:
            if cand.password and check_password(password, cand.password):
                student, password_ok = cand, True
                break
            # Parol o'rnatilmagan (importdan kelgan) o'quvchilar uchun
            # parol — ism va familiya
            if not cand.password and _name_password_matches(cand, password):
                student, password_ok = cand, True
                break

        if student and password_ok:
            if is_device_blocked(request, student.phone):
                return JsonResponse(
                    {"error": "Bu qurilma bloklangan — menejerga murojaat qiling"},
                    status=403,
                )
            record_login(
                request,
                phone=student.phone,
                # Ustozlarning panel profili — is_admin bo'lgan o'quvchi
                role=(
                    "teacher"
                    if (student.is_admin or student.is_excellence)
                    else "student"
                ),
                user_name=f"{student.name} {student.surname}".strip(),
            )
            return JsonResponse(
                {
                    "exists": True,
                    "id": student.id,
                    "name": student.name,
                    "surname": student.surname,
                    "phone": student.phone,
                    "teacher_id": student.teacher_id,
                    "teacher_name": student.teacher.name if student.teacher else "",
                    "is_admin": student.is_admin,
                    "is_excellence": student.is_excellence,
                    "stage": student.stage,
                    "schedule": student.schedule,
                    "coin_balance": student.coin_balance,
                }
            )

        teacher = _find_teacher_by_any_phone(phone)
        teacher_password_ok = False
        if teacher:
            if teacher.password and check_password(password, teacher.password):
                teacher_password_ok = True
            # Yangi qo'shilgan ustozga hali parol o'rnatilmagan bo'lsa —
            # studentlardagi kabi ism-familiya orqali kirishga ruxsat
            elif not teacher.password and _name_password_matches(teacher, password):
                teacher_password_ok = True
        if teacher and teacher_password_ok:
            if is_device_blocked(request, teacher.phone):
                return JsonResponse(
                    {"error": "Bu qurilma bloklangan — menejerga murojaat qiling"},
                    status=403,
                )
            record_login(
                request,
                phone=teacher.phone,
                role="teacher",
                user_name=teacher.name,
            )
            return JsonResponse(
                {
                    "exists": True,
                    "id": teacher.id,
                    "name": teacher.name,
                    "phone": teacher.phone,
                    "teacher_id": teacher.id,
                    "is_admin": False,
                    "is_excellence": teacher.is_senior,
                    "role": "teacher",
                }
            )

        return JsonResponse({"exists": False, "error": "Parol noto'g'ri"}, status=401)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# LESSONS
# ─────────────────────────────


def get_lessons(request):
    """Darslar ro'yxati."""
    try:
        teacher_id = request.GET.get("teacher_id", "")
        qs = Lesson.objects.select_related("teacher").order_by("-date")
        if teacher_id:
            try:
                qs = qs.filter(teacher_id=int(teacher_id))
            except ValueError:
                return JsonResponse({"error": "Invalid teacher_id"}, status=400)

        data = [
            {
                "id": lesson.id,
                "title": lesson.title,
                "date": str(lesson.date),
                "teacher_name": lesson.teacher.name if lesson.teacher else "",
            }
            for lesson in qs
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def create_lesson(request):
    """Yangi dars yaratish."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        teacher_id = data.get("teacher_id")

        if not teacher_id:
            return JsonResponse({"error": "teacher_id kiritilmadi"}, status=400)

        teacher = Teacher.objects.filter(id=teacher_id).first()
        if not teacher:
            return JsonResponse({"error": "O'qituvchi topilmadi"}, status=404)

        lesson_date = data.get("date", "").strip()
        title = data.get("title", "").strip()

        if not lesson_date or not title:
            return JsonResponse(
                {"error": "date va title kiritilishi shart"}, status=400
            )

        try:
            parsed_date = datetime.strptime(lesson_date, "%Y-%m-%d").date()
        except ValueError:
            return JsonResponse(
                {"error": "date format '%Y-%m-%d' bo'lishi kerak"}, status=400
            )

        weekday = parsed_date.weekday()
        schedule_for_day = get_schedule_for_day(weekday)

        lesson = Lesson.objects.create(
            title=title,
            teacher=teacher,
            date=parsed_date,
        )

        group_id = data.get("group_id")

        if group_id:
            try:
                group = Group.objects.filter(id=int(group_id)).first()
                if group:
                    students_qs = group.students.filter(
                        is_admin=False, is_excellence=False
                    )
                else:
                    students_qs = teacher.students.filter(
                        is_admin=False, is_excellence=False
                    )
            except ValueError:
                students_qs = teacher.students.filter(
                    is_admin=False, is_excellence=False
                )
        else:
            students_qs = teacher.students.filter(is_admin=False, is_excellence=False)
            if schedule_for_day:
                # "Har kuni" (daily) o'quvchilar har darsga qo'shiladi
                students_qs = students_qs.filter(
                    schedule__in=[schedule_for_day, "daily"]
                )

        for student in students_qs:
            Attendance.objects.get_or_create(
                student=student,
                lesson=lesson,
                defaults={"status": "absent"},
            )

        return JsonResponse(
            {
                "id": lesson.id,
                "message": "Dars muvaffaqiyatli yaratildi!",
                "schedule": schedule_for_day,
            },
            status=201,
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# ATTENDANCE
# ─────────────────────────────


def get_attendance(request, lesson_id):
    """Darsga davomat ro'yxati."""
    try:
        try:
            lesson_id = int(lesson_id)
        except ValueError:
            return JsonResponse({"error": "Invalid lesson_id"}, status=400)

        attendances = Attendance.objects.filter(lesson_id=lesson_id).select_related(
            "student"
        )
        data = [
            {
                "id": a.id,
                "student_id": a.student.id,
                "student_name": f"{a.student.name} {a.student.surname}",
                "schedule": a.student.schedule,
                "status": a.status,
            }
            for a in attendances
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def update_attendance(request, attendance_id):
    """Davomat statusini yangilash va coin berish/olish."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        attendance = (
            Attendance.objects.select_related("student")
            .filter(id=attendance_id)
            .first()
        )
        if not attendance:
            return JsonResponse({"error": "Davomat topilmadi"}, status=404)

        new_status = data.get("status", "").strip()
        if not new_status:
            return JsonResponse({"error": "status kiritilmadi"}, status=400)

        if new_status not in dict(Attendance.STATUS_CHOICES):
            return JsonResponse({"error": "Noto'g'ri status"}, status=400)

        old_status = attendance.status

        if new_status == old_status:
            return JsonResponse(
                {
                    "message": "Status o'zgarmadi",
                    "coin_balance": attendance.student.coin_balance,
                }
            )

        attendance_coins = get_attendance_coins_map()

        with transaction.atomic():
            student = attendance.student

            if old_status in attendance_coins:
                apply_coin_transaction(
                    student,
                    -attendance_coins[old_status],
                    ATTENDANCE_REASON.get(old_status, "manual"),
                    note=f"Status '{old_status}' bekor qilindi",
                    attendance=attendance,
                )

            if new_status in attendance_coins:
                apply_coin_transaction(
                    student,
                    attendance_coins[new_status],
                    ATTENDANCE_REASON.get(new_status, "manual"),
                    note=f"Status: {new_status}",
                    attendance=attendance,
                )

            attendance.status = new_status
            attendance.save()

        lesson = attendance.lesson
        log_attendance(
            request,
            lesson_id=lesson.id,
            group_name=(lesson.group.name if lesson.group else lesson.title),
            date_label=str(lesson.date),
        )

        return JsonResponse(
            {
                "message": "Davomat yangilandi!",
                "status": attendance.status,
                "coin_balance": attendance.student.coin_balance,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def attendance_group_day(request):
    """Guruh + sana bo'yicha davomat ro'yxati (kerak bo'lsa avtomatik yaratadi).

    GET ?group_id=<id>&date=YYYY-MM-DD (date bo'lmasa — bugun).
    Dars (group, date) bo'yicha get_or_create qilinadi, har o'quvchiga
    'absent' yozuvi ochiladi va ro'yxat qaytariladi. Qo'lda "dars yaratish"
    kerak emas — ustoz/menejer shunchaki guruhni tanlab, belgilaydi.
    """
    try:
        group_id = request.GET.get("group_id")
        date_str = (request.GET.get("date") or "").strip()
        if not group_id:
            return JsonResponse({"error": "group_id kiritilmadi"}, status=400)
        group = Group.objects.select_related("teacher").filter(id=group_id).first()
        if not group:
            return JsonResponse({"error": "Guruh topilmadi"}, status=404)

        if date_str:
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                return JsonResponse(
                    {"error": "date format YYYY-MM-DD bo'lishi kerak"}, status=400
                )
        else:
            d = tashkent_today()

        lesson, _ = Lesson.objects.get_or_create(
            group=group,
            date=d,
            defaults={"title": group.name, "teacher": group.teacher},
        )

        students = list(
            group.students.filter(is_admin=False, is_excellence=False).order_by(
                "name", "surname"
            )
        )
        existing = {a.student_id: a for a in Attendance.objects.filter(lesson=lesson)}
        to_create = [
            Attendance(student=s, lesson=lesson, status="absent")
            for s in students
            if s.id not in existing
        ]
        if to_create:
            Attendance.objects.bulk_create(to_create)
            existing = {
                a.student_id: a for a in Attendance.objects.filter(lesson=lesson)
            }

        # Coin ustoz davomat belgilayotgan joyda beriladi — shu yerda
        # balans ham, oylik erkin bonus ishlatilganmi ham ko'rinishi kerak
        bonus_used = monthly_bonus_used_ids([s.id for s in students], group.teacher_id)

        rows = [
            {
                "attendance_id": existing[s.id].id,
                "student_id": s.id,
                "name": f"{s.name} {s.surname}",
                "status": existing[s.id].status,
                "coin_balance": s.coin_balance,
                "bonus_used": s.id in bonus_used,
            }
            for s in students
            if s.id in existing
        ]
        return JsonResponse(
            {
                "lesson_id": lesson.id,
                "group_id": group.id,
                "teacher_id": group.teacher_id,
                "date": str(d),
                "students": rows,
                # Tugmalar miqdorini frontend qotirib yozmasin
                "coin_actions": COIN_QUICK_ACTIONS,
            }
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def attendance_group_month(request):
    """Guruh + oy bo'yicha har o'quvchining davomati (jadval uchun).

    GET ?group_id=<id>&month=YYYY-MM
    Har o'quvchi uchun shu oydagi dars sanalari bo'yicha status va
    present/late/absent sonlari qaytariladi. 'dates' — guruhning shu oydagi
    dars kunlari (jadval ustunlari).
    """
    try:
        group_id = request.GET.get("group_id")
        month = (request.GET.get("month") or "").strip()
        if not group_id:
            return JsonResponse({"error": "group_id kiritilmadi"}, status=400)
        group = Group.objects.filter(id=group_id).first()
        if not group:
            return JsonResponse({"error": "Guruh topilmadi"}, status=404)
        try:
            year, mon = int(month[:4]), int(month[5:7])
        except (ValueError, IndexError):
            return JsonResponse(
                {"error": "month format YYYY-MM bo'lishi kerak"}, status=400
            )

        students = list(
            group.students.filter(is_admin=False, is_excellence=False).order_by(
                "name", "surname"
            )
        )
        lessons = list(
            Lesson.objects.filter(
                group=group, date__year=year, date__month=mon
            ).order_by("date")
        )
        dates = [str(les.date) for les in lessons]
        att = Attendance.objects.filter(
            lesson__in=lessons, student__in=students
        ).select_related("lesson")

        by_student = {}
        for a in att:
            by_student.setdefault(a.student_id, {})[str(a.lesson.date)] = (
                a.id,
                a.status,
            )

        rows = []
        for s in students:
            recs = by_student.get(s.id, {})
            statuses = [v[1] for v in recs.values()]
            rows.append(
                {
                    "student_id": s.id,
                    "name": f"{s.name} {s.surname}",
                    "coin_balance": s.coin_balance,
                    "present": statuses.count("present"),
                    "late": statuses.count("late"),
                    "absent": statuses.count("absent"),
                    "records": [
                        {
                            "date": dt,
                            "attendance_id": recs[dt][0],
                            "status": recs[dt][1],
                        }
                        for dt in dates
                        if dt in recs
                    ],
                }
            )
        return JsonResponse(
            {"group_id": group.id, "month": month, "dates": dates, "students": rows}
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_student_attendance(request, student_id):
    """O'quvchining davomati."""
    try:
        try:
            student_id = int(student_id)
        except ValueError:
            return JsonResponse({"error": "Invalid student_id"}, status=400)

        month = request.GET.get("month", "")
        qs = (
            Attendance.objects.filter(student_id=student_id)
            .select_related("lesson")
            .order_by("lesson__date")
        )
        if month:
            try:
                year, mon = month.split("-")
                qs = qs.filter(
                    lesson__date__year=int(year), lesson__date__month=int(mon)
                )
            except ValueError:
                return JsonResponse(
                    {"error": "month format 'YYYY-MM' bo'lishi kerak"}, status=400
                )

        data = [
            {
                "id": a.id,
                "lesson_id": a.lesson.id,
                "lesson_title": a.lesson.title,
                "lesson_date": str(a.lesson.date),
                "status": a.status,
            }
            for a in qs
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_monthly_absences(request):
    """Oylik davomatlar statistikasi."""
    try:
        month = request.GET.get("month", datetime.now().strftime("%Y-%m"))
        teacher_id = request.GET.get("teacher_id", "")

        try:
            year, mon = month.split("-")
            year, mon = int(year), int(mon)
        except ValueError:
            return JsonResponse(
                {"error": "month format 'YYYY-MM' bo'lishi kerak"}, status=400
            )

        qs = Student.objects.filter(is_admin=False, is_excellence=False)
        if teacher_id:
            try:
                qs = qs.filter(teacher_id=int(teacher_id))
            except ValueError:
                return JsonResponse({"error": "Invalid teacher_id"}, status=400)

        result = {}
        for student in qs:
            count = Attendance.objects.filter(
                student=student,
                status="absent",
                lesson__date__year=year,
                lesson__date__month=mon,
            ).count()
            result[student.id] = count

        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# ─────────────────────────────
# ATTENDANCE COIN SETTINGS
# ─────────────────────────────


def get_attendance_coin_settings(request):
    """Davomat coin sozlamalarini olish."""
    try:
        s = AttendanceCoinSettings.get_settings()
        return JsonResponse(
            {
                "present": s.present,
                "late": s.late,
                "absent": s.absent,
                "payment_ontime": s.payment_ontime,
                "payment_grace_days": s.payment_grace_days,
            }
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def update_attendance_coin_settings(request):
    """Davomat coin sozlamalarini yangilash."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        s = AttendanceCoinSettings.get_settings()

        if "present" in data:
            try:
                s.present = int(data["present"])
            except (ValueError, TypeError):
                return JsonResponse({"error": "present son bo'lishi kerak"}, status=400)

        if "late" in data:
            try:
                s.late = int(data["late"])
            except (ValueError, TypeError):
                return JsonResponse({"error": "late son bo'lishi kerak"}, status=400)

        if "absent" in data:
            try:
                s.absent = int(data["absent"])
            except (ValueError, TypeError):
                return JsonResponse({"error": "absent son bo'lishi kerak"}, status=400)

        if "payment_ontime" in data:
            try:
                s.payment_ontime = int(data["payment_ontime"])
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "payment_ontime son bo'lishi kerak"}, status=400
                )

        if "payment_grace_days" in data:
            try:
                s.payment_grace_days = max(int(data["payment_grace_days"]), 0)
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "payment_grace_days son bo'lishi kerak"}, status=400
                )

        s.save()
        return JsonResponse(
            {
                "message": "Sozlamalar yangilandi!",
                "present": s.present,
                "late": s.late,
                "absent": s.absent,
                "payment_ontime": s.payment_ontime,
                "payment_grace_days": s.payment_grace_days,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# STUDENT PENALTIES
# ─────────────────────────────


def get_student_penalties(request, student_id):
    """O'quvchining ja'zolari."""
    try:
        try:
            student_id = int(student_id)
        except ValueError:
            return JsonResponse({"error": "Invalid student_id"}, status=400)

        penalties = StudentPenalty.objects.filter(student_id=student_id).order_by(
            "-date"
        )
        data = [
            {
                "id": p.id,
                "reason": p.reason,
                "reason_display": p.get_reason_display(),
                "description": p.description,
                "amount": p.amount,
                "date": str(p.date),
                "given_by": p.given_by.name if p.given_by else "",
            }
            for p in penalties
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_teacher_students_penalties(request, teacher_id):
    """O'qituvchining o'z studentlarining ja'zolari."""
    try:
        try:
            teacher_id = int(teacher_id)
        except ValueError:
            return JsonResponse({"error": "Invalid teacher_id"}, status=400)

        penalties = (
            StudentPenalty.objects.filter(student__teacher_id=teacher_id)
            .select_related("student")
            .order_by("-date")
        )
        data = [
            {
                "id": p.id,
                "student_id": p.student.id,
                "student_name": f"{p.student.name} {p.student.surname}",
                "reason": p.reason,
                "reason_display": p.get_reason_display(),
                "description": p.description,
                "amount": p.amount,
                "date": str(p.date),
            }
            for p in penalties
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def create_student_penalty(request):
    """O'quvchiga ja'zo berish."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        student_id = data.get("student_id")

        if not student_id:
            return JsonResponse({"error": "student_id kiritilmadi"}, status=400)

        student = Student.objects.filter(id=student_id).first()
        if not student:
            return JsonResponse({"error": "O'quvchi topilmadi"}, status=404)

        given_by = None
        if data.get("teacher_id"):
            given_by = Teacher.objects.filter(id=data["teacher_id"]).first()

        try:
            amount = int(data.get("amount", 0))
        except (ValueError, TypeError):
            return JsonResponse({"error": "amount son bo'lishi kerak"}, status=400)

        penalty = StudentPenalty.objects.create(
            student=student,
            given_by=given_by,
            reason=data.get("reason", "other"),
            description=data.get("description", "").strip(),
            amount=amount,
        )
        return JsonResponse(
            {"id": penalty.id, "message": "Ja'zo qo'shildi"}, status=201
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def delete_student_penalty(request, penalty_id):
    """Ja'zoni o'chirish."""
    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        try:
            penalty_id = int(penalty_id)
        except ValueError:
            return JsonResponse({"error": "Invalid penalty_id"}, status=400)

        penalty = StudentPenalty.objects.filter(id=penalty_id).first()
        if not penalty:
            return JsonResponse({"error": "Ja'zo topilmadi"}, status=404)
        penalty.delete()
        return JsonResponse({"message": "Ja'zo o'chirildi!"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# PAYMENTS
# ─────────────────────────────


def get_payments(request, student_id):
    """O'quvchining to'lovlari."""
    try:
        try:
            student_id = int(student_id)
        except ValueError:
            return JsonResponse({"error": "Invalid student_id"}, status=400)

        payments = (
            Payment.objects.filter(student_id=student_id)
            .prefetch_related("student__groups")
            .order_by("-month")
        )
        data = []
        for p in payments:
            group = student_primary_group(p.student)
            due = payment_due_date(p.month, group)
            attended, total = attendance_map_for_month([student_id], p.month).get(
                student_id, (0, 0)
            )
            data.append(
                {
                    "id": p.id,
                    "month": p.month,
                    "stage": p.stage,
                    "amount_due": p.amount_due,
                    "discount": p.discount,
                    "paid_amount": p.paid_amount,
                    "is_paid": p.is_paid,
                    "paid_at": p.paid_at.strftime("%Y-%m-%d") if p.paid_at else None,
                    "due_date": due.isoformat() if due else None,
                    "attended_count": attended,
                    "total_lessons": total,
                    "attendance_due": attendance_based_due(
                        p.amount_due, attended, total
                    ),
                }
            )
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_student_wallet(request, student_id):
    """O'quvchining virtual kartasi — ortiqcha balans va qarzdorlik."""
    try:
        try:
            student_id = int(student_id)
        except ValueError:
            return JsonResponse({"error": "Invalid student_id"}, status=400)
        student = Student.objects.filter(id=student_id).first()
        if not student:
            return JsonResponse({"error": "Student topilmadi"}, status=404)
        return JsonResponse(compute_wallet(student))
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_all_payments(request):
    """Barcha to'lovlar."""
    try:
        month = request.GET.get("month", "").strip()
        teacher_id = request.GET.get("teacher_id", "").strip()
        qs = (
            Payment.objects.select_related("student", "student__teacher")
            .prefetch_related("student__groups")
            .order_by("-month", "student__name")
        )

        if month:
            qs = qs.filter(month=month)
        if teacher_id:
            try:
                qs = qs.filter(student__teacher_id=int(teacher_id))
            except ValueError:
                return JsonResponse({"error": "Invalid teacher_id"}, status=400)

        payments = list(qs)
        att_map = (
            attendance_map_for_month([p.student_id for p in payments], month)
            if month
            else {}
        )
        # Har o'quvchining kartasi (barcha oylar bo'yicha) — bitta so'rovда
        wallet_map = wallets_for({p.student_id for p in payments})
        # Bugun kassaga qaysi to'lovdan qancha tushgani — bitta so'rovda
        today_map = paid_today_map([p.id for p in payments])

        data = []
        for p in payments:
            group = student_primary_group(p.student)
            due = payment_due_date(p.month, group)
            attended, total = att_map.get(p.student_id, (0, 0))
            wallet = wallet_map.get(p.student_id, {})
            data.append(
                {
                    "id": p.id,
                    "student_id": p.student.id,
                    "student_name": f"{p.student.name} {p.student.surname}",
                    "student_phone": p.student.phone,
                    "teacher_name": p.student.teacher.name if p.student.teacher else "",
                    "month": p.month,
                    "stage": p.stage,
                    "amount_due": p.amount_due,
                    "discount": p.discount,
                    "monthly_discount": p.student.monthly_discount,
                    "paid_amount": p.paid_amount,
                    "is_paid": p.is_paid,
                    "paid_at": str(p.paid_at) if p.paid_at else None,
                    "due_date": due.isoformat() if due else None,
                    # Davomatга qarab to'lov (kelgan darslar uchun)
                    "attended_count": attended,
                    "total_lessons": total,
                    "attendance_due": attendance_based_due(
                        p.amount_due, attended, total
                    ),
                    # Virtual karta (barcha oylar bo'yicha, o'quvchi darajasida)
                    "wallet_balance": wallet.get("balance", 0),
                    "wallet_debt": wallet.get("debt", 0),
                    # Bugungi kassaga shu to'lovdan tushgan pul
                    "paid_today": today_map.get(p.id, 0),
                }
            )
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def generate_payments(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        month = data.get("month", "").strip()

        if not month:
            return JsonResponse({"error": "month kiritilmadi"}, status=400)

        try:
            year, mon = (int(x) for x in month.split("-"))
            month_last_day = date(year, mon, calendar.monthrange(year, mon)[1])
        except ValueError:
            return JsonResponse(
                {"error": "month format 'YYYY-MM' bo'lishi kerak"}, status=400
            )

        students = Student.objects.filter(
            is_admin=False, is_excellence=False
        ).prefetch_related("groups")
        created_count = 0
        skipped_count = 0
        not_opened_count = 0

        for student in students:
            # Guruh o'sha oydan keyin ochilgan bo'lsa — to'lov hali boshlanmagan
            group = student_primary_group(student)
            if group and group.opened_date and group.opened_date > month_last_day:
                not_opened_count += 1
                continue

            price = effective_monthly_fee(student)
            _, created = Payment.objects.get_or_create(
                student=student,
                month=month,
                defaults={
                    "stage": student.stage,
                    "amount_due": price,
                    # Doimiy oylik chegirma bo'lsa — shu oyga avtomatik qo'llanadi
                    "discount": max(0, min(int(student.monthly_discount or 0), price)),
                },
            )
            if created:
                created_count += 1
            else:
                skipped_count += 1

        msg = f"{created_count} ta yangi to'lov yaratildi, {skipped_count} ta allaqachon mavjud edi."
        if not_opened_count:
            msg += f" {not_opened_count} ta o'quvchi guruhi bu oydan keyin ochilgani uchun o'tkazib yuborildi."

        log_action(
            request,
            "payment.generate",
            f"{month} uchun to'lovlar yaratildi — {created_count} ta yangi, "
            f"{skipped_count} ta mavjud edi",
            target_type="month",
            target_name=month,
            month=month,
            created=created_count,
            skipped=skipped_count,
        )

        return JsonResponse(
            {
                "message": msg,
                "month": month,
                "created": created_count,
                "skipped": skipped_count,
                "not_opened": not_opened_count,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# BO'LIB TO'LASH — "bugun qancha tushdi"
# ─────────────────────────────
#
# `Payment.paid_amount` — oy boshidan beri to'plangan JAMI. Kassaga esa
# har safar shu jamining o'zgarishi (delta) tushadi. Menejer 400 000 lik
# oyga bugun 200 000 olib, ertaga yana 200 000 olsa, ikkinchi kuni
# maydonga 400 000 (yangi jami) yozishi kerak edi — 200 000 (bugun
# qo'lga tushgani) yozsa delta 0 chiqib, bugungi kassa 200 000 kam
# ko'rsatardi.
#
# Shuning uchun asosiy yo'l — `add_payment_installment`: unga jami emas,
# SHU SAFAR tushgan summa yuboriladi, jamini tizim o'zi qo'shadi. Jamini
# to'g'ridan-to'g'ri yozish faqat tuzatish uchun qoladi va kamaytirish
# `allow_decrease` bilan ochiq tasdiqlanishini talab qiladi.


def guard_paid_amount_change(payment, data):
    """Jamini kamaytirish tasodifan o'tib ketmasin.

    `paid_amount` jami tushgan pul. Uni kamaytirish kassadan pul
    yechish demak (jurnalga manfiy yozuv tushadi), shuning uchun bu
    faqat ataylab qilingan tuzatish bo'lishi kerak. Odatiy xato —
    "bugun tushgan summani" jami o'rniga yozish; bunda yangi qiymat
    eskisidan kichik chiqadi va pul yo'qoladi.

    Qaytaradi: xato bo'lsa JsonResponse, aks holda None.
    """
    if "paid_amount" not in data:
        return None
    try:
        new_amount = int(data["paid_amount"])
    except (ValueError, TypeError):
        return None  # formatni chaqiruvchining o'zi tekshiradi
    old_amount = int(payment.paid_amount or 0)
    if new_amount >= old_amount or data.get("allow_decrease"):
        return None
    return JsonResponse(
        {
            "error": (
                f"To'langan jami {old_amount:,} dan {new_amount:,} ga kamaymoqda. "
                "Bo'lib to'lash bo'lsa — 'To'lov qo'shish' orqali shu safar "
                "tushgan summani kiriting. Haqiqatan tuzatmoqchi bo'lsangiz "
                "allow_decrease bilan yuboring."
            ).replace(",", " "),
            "code": "paid_amount_decrease",
            "paid_amount": old_amount,
        },
        status=400,
    )


def payment_net_due(payment):
    """Chegirmadan keyin to'lanishi kerak bo'lgan sof summa."""
    return max(0, int(payment.amount_due or 0) - int(payment.discount or 0))


@csrf_exempt
def add_payment_installment(request, payment_id):
    """Bo'lib to'lash — SHU SAFAR tushgan summani qo'shadi.

    Body: {"amount": 200000, "note": "..."} — `amount` jami emas, hozir
    kassaga tushgan pul. Jami (`paid_amount`) ustiga qo'shiladi, kassa
    jurnaliga esa shu summa bugungi smenaga yoziladi. Ertaga yana pul
    kelsa yana shu yo'l bilan qo'shiladi — kechagi topshirilgan smena
    umuman qo'zg'almaydi.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    try:
        payment_id = int(payment_id)
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid payment_id"}, status=400)

    payment = Payment.objects.select_related("student").filter(id=payment_id).first()
    if not payment:
        return JsonResponse({"error": "To'lov topilmadi"}, status=404)

    try:
        amount = int(data.get("amount"))
    except (ValueError, TypeError):
        return JsonResponse({"error": "amount son bo'lishi kerak"}, status=400)
    if amount <= 0:
        return JsonResponse({"error": "amount 0 dan katta bo'lishi kerak"}, status=400)

    paid_amount_before = int(payment.paid_amount or 0)
    payment.paid_amount = paid_amount_before + amount

    # Sof summa qoplansa — oy yopiladi. Narx belgilanmagan (0) bo'lsa
    # bu qadam o'tkazib yuboriladi, aks holda 1 so'm ham "to'landi"
    # bo'lib qolardi.
    net_due = payment_net_due(payment)
    was_paid = payment.is_paid
    if net_due > 0 and payment.paid_amount >= net_due:
        payment.is_paid = True
        payment.paid_at = timezone.now()
    payment.save()

    # 💵 Kassa jurnali — shu summa bugungi smenaga tushadi
    record_cash_delta(request, payment, paid_amount_before, payment.paid_amount)

    coin_awarded = 0
    try:
        student = Student.objects.filter(id=payment.student_id).first()
        if student:
            before = student.coin_balance
            sync_payment_ontime_coin(payment)
            student.refresh_from_db(fields=["coin_balance"])
            coin_awarded = student.coin_balance - before
    except Exception:  # noqa: BLE001 — coin to'lovni to'smasin
        logging.getLogger(__name__).exception("payment ontime coin xatosi")

    student_label = str(payment.student) if payment.student_id else "—"
    remaining = max(0, net_due - payment.paid_amount)
    log_action(
        request,
        "payment.installment",
        f"{student_label} — {payment.month}: +{amount:,} so'm qabul qilindi, "
        f"jami {payment.paid_amount:,}, qolgan {remaining:,}".replace(",", " "),
        target_type="payment",
        target_id=payment.id,
        target_name=student_label,
        month=payment.month,
        amount=amount,
        paid_amount=payment.paid_amount,
    )

    # Har qabulda chek ketadi — o'quvchi shu safar qancha
    # o'tkazganini va qancha qolganini ko'rsin (oy yopilmasa ham).
    try:
        from . import telegram as tg

        tg.send_receipt(payment, amount=amount)
    except Exception:  # noqa: BLE001 — chek to'lovni to'smasin
        logging.getLogger(__name__).exception("Chek yuborilmadi")

    wallet = compute_wallet(payment.student) if payment.student_id else {}
    return JsonResponse(
        {
            "message": f"{amount:,} so'm qabul qilindi".replace(",", " "),
            "amount": amount,
            "paid_amount": payment.paid_amount,
            "amount_due": payment.amount_due,
            "discount": payment.discount,
            "remaining": remaining,
            "is_paid": payment.is_paid,
            "closed_now": payment.is_paid and not was_paid,
            "coin_awarded": coin_awarded,
            "wallet_balance": wallet.get("balance", 0),
            "wallet_debt": wallet.get("debt", 0),
            "installments": payment_installments(payment),
        },
        status=201,
    )


def payment_installments(payment):
    """Shu to'lovga tushgan pul harakatlari (kassa jurnalidan).

    Manba kassa jurnalining o'zi — hisobot va bu ro'yxat hech qachon
    bir-biridan ayrilmasin. Eng yangisi yuqorida.
    """
    entries = (
        CashEntry.objects.filter(payment=payment)
        .select_related("session")
        .order_by("-created_at")
    )
    return [
        {
            "id": e.id,
            "amount": e.amount,
            "kind": e.kind,
            "cashier_name": e.cashier_name,
            "note": e.note,
            # Kassa kuni (smena sanasi) — hisobot shu kun bo'yicha yig'iladi
            "date": e.session.date.isoformat() if e.session_id else None,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]


@csrf_exempt
def get_payment_installments(request, payment_id):
    """To'lov tarixi — qaysi kuni qancha tushgani."""
    try:
        payment_id = int(payment_id)
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid payment_id"}, status=400)

    payment = Payment.objects.select_related("student").filter(id=payment_id).first()
    if not payment:
        return JsonResponse({"error": "To'lov topilmadi"}, status=404)

    net_due = payment_net_due(payment)
    return JsonResponse(
        {
            "payment_id": payment.id,
            "month": payment.month,
            "amount_due": payment.amount_due,
            "discount": payment.discount,
            "net_due": net_due,
            "paid_amount": payment.paid_amount,
            "remaining": max(0, net_due - int(payment.paid_amount or 0)),
            "is_paid": payment.is_paid,
            "installments": payment_installments(payment),
        }
    )


@csrf_exempt
def confirm_payment(request, payment_id):
    """To'lovni tasdiqlash."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        try:
            payment_id = int(payment_id)
        except ValueError:
            return JsonResponse({"error": "Invalid payment_id"}, status=400)

        payment = Payment.objects.filter(id=payment_id).first()
        if not payment:
            return JsonResponse({"error": "To'lov topilmadi"}, status=404)

        denied = guard_paid_amount_change(payment, data)
        if denied:
            return denied

        # Jurnal uchun — nima o'zgarganini keyin solishtiramiz
        paid_before = payment.is_paid
        paid_amount_before = payment.paid_amount
        discount_before = payment.discount

        if "amount_due" in data:
            try:
                payment.amount_due = int(data["amount_due"])
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "amount_due son bo'lishi kerak"}, status=400
                )

        # ✅ QO'SHILDI: paid_amount ni ham saqlaymiz
        if "paid_amount" in data:
            try:
                payment.paid_amount = int(data["paid_amount"])
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "paid_amount son bo'lishi kerak"}, status=400
                )

        # ✅ Chegirma (shu oy uchun) — 0..amount_due oralig'iga cheklanadi
        if "discount" in data:
            try:
                payment.discount = max(
                    0, min(int(data["discount"]), int(payment.amount_due))
                )
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "discount son bo'lishi kerak"}, status=400
                )

        payment.is_paid = data.get("is_paid", payment.is_paid)
        payment.paid_at = timezone.now() if payment.is_paid else None
        payment.save()

        # 💵 Kassa jurnali — to'langan summa o'zgargani (delta) bugungi smenaga
        record_cash_delta(request, payment, paid_amount_before, payment.paid_amount)

        # ✅ Vaqtida to'lov uchun coin mukofoti (yoki bekor qilinsa qaytarish)
        coin_awarded = 0
        try:
            student = Student.objects.filter(id=payment.student_id).first()
            if student:
                before = student.coin_balance
                sync_payment_ontime_coin(payment)
                student.refresh_from_db(fields=["coin_balance"])
                coin_awarded = student.coin_balance - before
        except Exception:
            logging.getLogger(__name__).exception("payment ontime coin xatosi")

        wallet = compute_wallet(payment.student) if payment.student_id else {}

        student_label = str(payment.student) if payment.student_id else "—"
        if payment.discount != discount_before:
            log_action(
                request,
                "payment.discount",
                f"{student_label} — {payment.month} chegirma: "
                f"{discount_before:,} → {payment.discount:,}".replace(",", " "),
                target_type="payment",
                target_id=payment.id,
                target_name=student_label,
                month=payment.month,
                before=discount_before,
                after=payment.discount,
            )
        if payment.is_paid != paid_before or payment.paid_amount != paid_amount_before:
            state = "to'landi" if payment.is_paid else "to'lanmagan"
            log_action(
                request,
                "payment.confirm",
                f"{student_label} — {payment.month}: {state}, "
                f"{payment.paid_amount:,} so'm".replace(",", " "),
                target_type="payment",
                target_id=payment.id,
                target_name=student_label,
                month=payment.month,
                is_paid=payment.is_paid,
                paid_amount=payment.paid_amount,
            )

        # To'lov endigina tasdiqlangan bo'lsa o'quvchiga chek ketadi.
        # Faqat o'tish paytida — qayta saqlashda takror yuborilmasin.
        if payment.is_paid and not paid_before:
            try:
                from . import telegram as tg

                # {summa} — "shu safar to'langan". Oldin oy bo'yicha
                # jami ketardi: bo'lib to'laganda o'quvchi o'zi
                # o'tkazmagan summani ko'rardi.
                added = (payment.paid_amount or 0) - (paid_amount_before or 0)
                tg.send_receipt(payment, amount=added if added > 0 else None)
            except Exception:  # noqa: BLE001 — chek to'lovni to'smasin
                logging.getLogger(__name__).exception("Chek yuborilmadi")

        return JsonResponse(
            {
                "message": "To'lov yangilandi!",
                "is_paid": payment.is_paid,
                "amount_due": payment.amount_due,
                "discount": payment.discount,
                "paid_amount": payment.paid_amount,  # ✅ QO'SHILDI
                "coin_awarded": coin_awarded,
                "wallet_balance": wallet.get("balance", 0),
                "wallet_debt": wallet.get("debt", 0),
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def update_payment_amount(request, payment_id):
    """To'lov summasini yangilash."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        try:
            payment_id = int(payment_id)
        except ValueError:
            return JsonResponse({"error": "Invalid payment_id"}, status=400)

        payment = Payment.objects.filter(id=payment_id).first()
        if not payment:
            return JsonResponse({"error": "To'lov topilmadi"}, status=404)

        denied = guard_paid_amount_change(payment, data)
        if denied:
            return denied

        # Kassa jurnali uchun — o'zgarishdan oldingi to'langan summa
        paid_amount_before = payment.paid_amount

        if "amount_due" in data:
            try:
                payment.amount_due = int(data["amount_due"])
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "amount_due son bo'lishi kerak"}, status=400
                )

        # ✅ QO'SHILDI: paid_amount va is_paid ni ham qabul qilamiz
        if "paid_amount" in data:
            try:
                payment.paid_amount = int(data["paid_amount"])
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "paid_amount son bo'lishi kerak"}, status=400
                )

        # ✅ Chegirma (shu oy uchun)
        if "discount" in data:
            try:
                payment.discount = max(
                    0, min(int(data["discount"]), int(payment.amount_due))
                )
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "discount son bo'lishi kerak"}, status=400
                )

        is_paid_changed = "is_paid" in data
        if is_paid_changed:
            payment.is_paid = bool(data["is_paid"])
            payment.paid_at = timezone.now() if payment.is_paid else None

        payment.save()

        # 💵 Kassa jurnali — to'langan summa o'zgargani (delta) bugungi smenaga
        record_cash_delta(request, payment, paid_amount_before, payment.paid_amount)

        # ✅ is_paid o'zgargan bo'lsa — vaqtida to'lov coinini sinxronlaymiz
        coin_awarded = 0
        if is_paid_changed:
            try:
                student = Student.objects.filter(id=payment.student_id).first()
                if student:
                    before = student.coin_balance
                    sync_payment_ontime_coin(payment)
                    student.refresh_from_db(fields=["coin_balance"])
                    coin_awarded = student.coin_balance - before
            except Exception:
                logging.getLogger(__name__).exception("payment ontime coin xatosi")

        log_action(
            request,
            "payment.update",
            f"{payment.student} — {payment.month}: summa "
            f"{payment.amount_due:,} so'm, to'langan "
            f"{payment.paid_amount:,} so'm".replace(",", " "),
            target_type="payment",
            target_id=payment.id,
            target_name=str(payment.student),
            month=payment.month,
            amount_due=payment.amount_due,
            paid_amount=payment.paid_amount,
        )

        wallet = compute_wallet(payment.student) if payment.student_id else {}
        return JsonResponse(
            {
                "message": "Summa yangilandi!",
                "amount_due": payment.amount_due,
                "discount": payment.discount,
                "paid_amount": payment.paid_amount,  # ✅ QO'SHILDI
                "is_paid": payment.is_paid,
                "coin_awarded": coin_awarded,
                "wallet_balance": wallet.get("balance", 0),
                "wallet_debt": wallet.get("debt", 0),
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# KASSA — kunlik smena + tranzaksiya jurnali
# ─────────────────────────────
#
# Har to'lov qabuli/tuzatilishi shu yerdan o'tadi: paid_amount qancha
# o'zgargani (delta) o'chirilmaydigan CashEntry sifatida yoziladi va
# kassirning bugungi ochiq smenasiga tushadi. Shu tufayli to'lovni qayta
# yozish (200k -> 400k) ham bugungi kassaga +200k bo'lib qo'shiladi,
# kechagi topshirilgan smena buzilmaydi.


def record_cash_delta(request, payment, old_amount, new_amount):
    """To'lov summasi o'zgarishini kassa jurnaliga yozadi.

    `old_amount` -> `new_amount` farqi 0 bo'lsa hech narsa qilmaydi.
    Kunlik kassa yoqilgan bo'lsa — yozuv bugungi umumiy smenaga tushadi
    (yo'q bo'lsa avtomatik ochiladi, har kuni yangi). Agar bugungi smena
    allaqachon topshirilgan bo'lsa, yangi pul kelgani uchun qayta
    ochiladi — kun tugamaguncha yakuniy emas. Kassa o'chirilgan bo'lsa
    yozuv smensiz qoladi, lekin baribir saqlanadi.

    Hech qachon xato otmaydi — jurnal yozilmagani uchun to'lov buzilmasin.
    """
    try:
        delta = int(new_amount or 0) - int(old_amount or 0)
        if delta == 0:
            return None

        manager = caller_manager(request)
        actor_name = f"{manager.name} {manager.surname}".strip() if manager else ""
        settings_obj = CashRegisterSettings.get_settings()

        session = None
        if settings_obj.enabled:
            session = CashSession.for_date(tashkent_today())
            # Topshirilgan kunga yana pul tushdi — qayta ochamiz
            if session.status == CashSession.STATUS_CLOSED:
                session.status = CashSession.STATUS_OPEN
                session.counted_total = None
                session.difference = 0
                session.closed_at = None
            if actor_name:
                session.cashier_name = actor_name
                if manager is not None:
                    session.cashier = manager
            session.save()

        student = payment.student if payment.student_id else None
        kind = CashEntry.KIND_PAYMENT if not old_amount else CashEntry.KIND_ADJUST

        return CashEntry.objects.create(
            session=session,
            payment=payment,
            student=student,
            student_name=str(student) if student else "",
            cashier=manager,
            cashier_name=actor_name,
            amount=delta,
            month=payment.month or "",
            kind=kind,
        )
    except Exception:  # noqa: BLE001 — kassa yozuvi to'lovni to'smasin
        logging.getLogger(__name__).exception("CashEntry yozilmadi")
        return None


def paid_today_map(payment_ids):
    """{payment_id: bugungi smenaga tushgan summa} — bitta so'rovda.

    To'lovlar jadvalida "bugun qancha oldik" ko'rinib tursin: menejer
    jamiga qarab emas, shu ko'rsatkichga qarab kassani solishtiradi.
    """
    if not payment_ids:
        return {}
    session = CashSession.on_date(tashkent_today())
    if session is None:
        return {}
    rows = (
        CashEntry.objects.filter(session=session, payment_id__in=payment_ids)
        .values("payment_id")
        .annotate(total=Sum("amount"))
    )
    return {r["payment_id"]: r["total"] or 0 for r in rows}


def month_collection_plan(month=None):
    """Shu oy: qancha yig'ilishi kerak, qancha yig'ilgan, qancha qolgan.

    Kassir kun davomida faqat "bugun qancha tushdi" ni ko'rardi —
    oylik maqsad ko'rinmagani uchun qancha qarz qolganini bilmasdi.

    Summalar CHEGIRMADAN KEYIN olinadi: kassir qo'liga tushadigan pul
    shu. Qolgan har qator bo'yicha alohida 0 ga cheklanadi — bittasining
    ortiqcha to'lovi boshqasining qarzini yopib ko'rsatmasin.
    """
    month = (month or tashkent_today().strftime("%Y-%m")).strip()

    net_due = Greatest(
        F("amount_due") - F("discount"), Value(0), output_field=IntegerField()
    )
    rows = Payment.objects.filter(month=month).annotate(
        net_due=net_due,
        row_remaining=Greatest(
            net_due - F("paid_amount"), Value(0), output_field=IntegerField()
        ),
    )
    totals = rows.aggregate(
        due=Sum("net_due"),
        collected=Sum("paid_amount"),
        remaining=Sum("row_remaining"),
    )

    due_total = totals["due"] or 0
    collected_total = totals["collected"] or 0
    remaining_total = totals["remaining"] or 0
    total_count = rows.count()
    paid_count = rows.filter(is_paid=True).count()

    # Kassaga shu oyda haqiqatda tushgan pul (jurnal bo'yicha). Yuqoridagi
    # "yig'ilgan" dan farq qiladi: bu yerda boshqa oy uchun qilingan
    # to'lovlar ham bor, chunki kassa kun bo'yicha yig'iladi.
    try:
        year, mon = (int(x) for x in month.split("-"))
        cash_month_total = (
            CashEntry.objects.filter(
                session__date__year=year, session__date__month=mon
            ).aggregate(s=Sum("amount"))["s"]
            or 0
        )
    except (ValueError, TypeError):
        cash_month_total = 0

    return {
        "month": month,
        "due_total": due_total,
        "collected_total": collected_total,
        "remaining_total": remaining_total,
        # 0..100 — panel progress chizig'i uchun
        "collected_percent": (
            round(collected_total * 100 / due_total) if due_total > 0 else 0
        ),
        "total_count": total_count,
        "paid_count": paid_count,
        "unpaid_count": total_count - paid_count,
        "cash_month_total": cash_month_total,
    }


def _session_dict(s, *, with_entries=False):
    """CashSession -> JSON (hisobotlar uchun umumiy shakl)."""
    total = s.live_total()
    data = {
        "id": s.id,
        "cashier_id": s.cashier_id,
        "cashier_name": s.cashier_name,
        "date": s.date.isoformat() if s.date else None,
        "status": s.status,
        "is_open": s.is_open,
        "opened_at": s.opened_at.isoformat() if s.opened_at else None,
        "closed_at": s.closed_at.isoformat() if s.closed_at else None,
        # Ochiq smenada jonli yig'indi, yopilganda muhrlangan qiymat
        "expected_total": total if s.is_open else s.expected_total,
        "counted_total": s.counted_total,
        "difference": s.difference,
        "entries_count": s.entries.count(),
        "note": s.note,
    }
    if with_entries:
        data["entries"] = [
            {
                "id": e.id,
                "student_name": e.student_name,
                "amount": e.amount,
                "month": e.month,
                "kind": e.kind,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in s.entries.select_related().all()
        ]
    return data


@csrf_exempt
def get_cash_current(request):
    """Bugungi kunlik kassa (yo'q bo'lsa hali to'lov qabul qilinmagan).

    Kunlik kassa o'chirilgan bo'lsa `enabled=false` qaytadi — panel
    kassa bo'limini ko'rsatmaydi.
    """
    denied = require_permission(request, "cash.view")
    if denied:
        return denied

    settings_obj = CashRegisterSettings.get_settings()
    if not settings_obj.enabled:
        return JsonResponse({"enabled": False, "session": None, "plan": None})

    session = CashSession.on_date(tashkent_today())
    return JsonResponse(
        {
            "enabled": True,
            "require_counted": settings_obj.require_counted,
            "session": _session_dict(session, with_entries=True) if session else None,
            # Oylik maqsad — bugungi smena ochilmagan bo'lsa ham ko'rinadi
            "plan": month_collection_plan(request.GET.get("month")),
        }
    )


@csrf_exempt
def close_cash_session(request):
    """Kunlik kassani topshirish — fizik sanoqni kiritib smenani yopadi."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    denied = require_permission(request, "cash.close")
    if denied:
        return denied

    settings_obj = CashRegisterSettings.get_settings()
    if not settings_obj.enabled:
        return JsonResponse({"error": "Kunlik kassa o'chirilgan"}, status=400)

    session = CashSession.on_date(tashkent_today())
    if session is None:
        return JsonResponse({"error": "Bugun hali to'lov qabul qilinmagan"}, status=400)

    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    manager = caller_manager(request)
    expected = session.live_total()
    counted = data.get("counted_total")

    if settings_obj.require_counted:
        if counted is None or str(counted).strip() == "":
            return JsonResponse({"error": "Sanalgan pulni kiriting"}, status=400)
        try:
            counted = int(counted)
        except (ValueError, TypeError):
            return JsonResponse(
                {"error": "counted_total son bo'lishi kerak"}, status=400
            )
        if counted < 0:
            return JsonResponse({"error": "counted_total manfiy bo'lmaydi"}, status=400)
    else:
        # Sanoq ixtiyoriy — kiritilmasa tizim hisobi olinadi. Kiritilgani
        # son bo'lmasa jim 500 emas, tushunarli xato qaytishi kerak.
        if counted in (None, ""):
            counted = expected
        else:
            try:
                counted = int(counted)
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "counted_total son bo'lishi kerak"}, status=400
                )

    session.expected_total = expected
    session.counted_total = counted
    session.difference = counted - expected
    session.status = CashSession.STATUS_CLOSED
    session.closed_at = timezone.now()
    session.note = str(data.get("note") or "").strip()[:255]
    if manager is not None:
        session.cashier = manager
        session.cashier_name = f"{manager.name} {manager.surname}".strip()
    session.save()

    log_action(
        request,
        "cash.close",
        f"Kunlik kassa topshirildi — {session.date}: "
        f"tizim {expected:,}, sanoq {counted:,}, farq {session.difference:+,}".replace(
            ",", " "
        ),
        target_type="cash_session",
        target_id=session.id,
        target_name=session.cashier_name,
        expected=expected,
        counted=counted,
        difference=session.difference,
    )

    return JsonResponse(
        {"message": "Kassa topshirildi", "session": _session_dict(session)}
    )


@csrf_exempt
def get_cash_sessions(request):
    """Kunlik kassa tarixi (oylik ko'rinish).

    Kassa umumiy — `cash.view` vakolati bo'lgan har kim ko'radi.
    ?month=YYYY-MM bilan filtrlanadi (standart — joriy oy).
    """
    denied = require_permission(request, "cash.view")
    if denied:
        return denied

    month = (request.GET.get("month") or tashkent_today().strftime("%Y-%m")).strip()
    try:
        year, mon = month.split("-")
        year, mon = int(year), int(mon)
    except (ValueError, AttributeError):
        return JsonResponse(
            {"error": "month format 'YYYY-MM' bo'lishi kerak"}, status=400
        )

    qs = CashSession.objects.filter(date__year=year, date__month=mon)
    sessions = [_session_dict(s) for s in qs]
    closed = [s for s in sessions if s["status"] == CashSession.STATUS_CLOSED]

    summary = {
        "month": month,
        "sessions_count": len(sessions),
        "closed_count": len(closed),
        "open_count": len(sessions) - len(closed),
        "expected_total": sum(s["expected_total"] or 0 for s in sessions),
        "counted_total": sum((s["counted_total"] or 0) for s in closed),
        "difference_total": sum((s["difference"] or 0) for s in closed),
    }
    return JsonResponse(
        {
            "summary": summary,
            "sessions": sessions,
            # Shu oy qancha yig'ilishi kerak edi / qancha yig'ildi
            "plan": month_collection_plan(month),
        }
    )


@csrf_exempt
def get_cash_settings(request):
    """Kassa sozlamasi (faqat supermenejer)."""
    denied = require_super(request)
    if denied:
        return denied
    s = CashRegisterSettings.get_settings()
    return JsonResponse(
        {
            "enabled": s.enabled,
            "require_counted": s.require_counted,
            "lock_after_close": s.lock_after_close,
        }
    )


@csrf_exempt
def update_cash_settings(request):
    """Kassa sozlamasini yangilash (faqat supermenejer)."""
    if request.method not in ("POST", "PATCH"):
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    s = CashRegisterSettings.get_settings()
    if "enabled" in data:
        s.enabled = bool(data["enabled"])
    if "require_counted" in data:
        s.require_counted = bool(data["require_counted"])
    if "lock_after_close" in data:
        s.lock_after_close = bool(data["lock_after_close"])
    s.save()

    state_word = "yoqildi" if s.enabled else "o'chirildi"
    log_action(
        request,
        "cash.settings",
        f"Kassa sozlamasi — kunlik kassa {state_word}",
        target_type="cash_settings",
        enabled=s.enabled,
        require_counted=s.require_counted,
    )
    return JsonResponse(
        {
            "message": "Saqlandi",
            "enabled": s.enabled,
            "require_counted": s.require_counted,
            "lock_after_close": s.lock_after_close,
        }
    )


# ─────────────────────────────
# TO'LOV KARTASI + TO'LOV SO'ROVLARI (chek)
# ─────────────────────────────


def get_payment_settings(request):
    """To'lov qabul qilinadigan karta ma'lumoti (student ko'radi)."""
    s = PaymentSettings.get_settings()
    return JsonResponse(
        {
            "card_number": s.card_number,
            "card_holder": s.card_holder,
            "note": s.note,
        }
    )


@csrf_exempt
def update_payment_settings(request):
    """Kartani sozlash (manager)."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        s = PaymentSettings.get_settings()
        if "card_number" in data:
            s.card_number = str(data["card_number"]).strip()[:32]
        if "card_holder" in data:
            s.card_holder = str(data["card_holder"]).strip()[:100]
        if "note" in data:
            s.note = str(data["note"]).strip()[:255]
        s.save()
        log_action(
            request,
            "payment.settings",
            f"To'lov kartasi o'zgartirildi: {s.card_number} ({s.card_holder})",
            target_type="settings",
            target_name="To'lov kartasi",
            card_holder=s.card_holder,
        )
        return JsonResponse(
            {
                "message": "Karta saqlandi",
                "card_number": s.card_number,
                "card_holder": s.card_holder,
                "note": s.note,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def create_payment_request(request):
    """Student to'lov so'rovi (chek rasmi) yuboradi."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        student_id = data.get("student_id")
        receipt = (data.get("receipt_b64") or "").strip()
        if not student_id:
            return JsonResponse({"error": "student_id kiritilmadi"}, status=400)
        if not receipt:
            return JsonResponse({"error": "Chek rasmi yuklanmadi"}, status=400)
        # Haddan tashqari katta rasmni rad etamiz (~700KB base64)
        if len(receipt) > 720_000:
            return JsonResponse(
                {"error": "Rasm juda katta — kichikroq surat yuklang"}, status=400
            )
        student = Student.objects.filter(id=student_id).first()
        if not student:
            return JsonResponse({"error": "Student topilmadi"}, status=404)

        pr = PaymentRequest.objects.create(
            student=student,
            receipt_b64=receipt,
            note=str(data.get("note") or "").strip()[:255],
        )

        # Menejerga darhol bildiramiz — chek panelda ko'rilmay yotib
        # qolmasin. Yuborish fon oqimida, javob kutilmaydi.
        try:
            from . import telegram as tg

            pending = PaymentRequest.objects.filter(status="pending").count()
            tg.notify_managers(
                f"🧾 <b>Yangi to'lov cheki</b>\n\n"
                f"{student.name} {student.surname} chek yubordi.\n"
                f"Kutayotgan so'rovlar: {pending} ta"
            )
        except Exception:  # noqa: BLE001 — bildirishnoma chekni to'smasin
            logging.getLogger(__name__).exception("Menejer bildirishnomasi ketmadi")

        return JsonResponse(
            {"message": "To'lov so'rovi yuborildi", "id": pr.id, "status": pr.status},
            status=201,
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


def _payment_request_row(pr, include_receipt=False):
    row = {
        "id": pr.id,
        "student_id": pr.student_id,
        "student_name": f"{pr.student.name} {pr.student.surname}",
        "student_phone": pr.student.phone,
        "status": pr.status,
        "amount": pr.amount,
        "month": pr.month,
        "paid_at": str(pr.paid_at) if pr.paid_at else None,
        "note": pr.note,
        "created_at": pr.created_at.strftime("%Y-%m-%d %H:%M"),
        "resolved_at": (
            pr.resolved_at.strftime("%Y-%m-%d %H:%M") if pr.resolved_at else None
        ),
    }
    if include_receipt:
        row["receipt_b64"] = pr.receipt_b64
    return row


def get_payment_requests(request):
    """Manager uchun to'lov so'rovlari. ?status=pending|accepted|rejected|all."""
    try:
        status = (request.GET.get("status") or "pending").strip()
        qs = PaymentRequest.objects.select_related("student").all()
        if status != "all":
            qs = qs.filter(status=status)
        rows = [
            _payment_request_row(pr, include_receipt=(pr.status == "pending"))
            for pr in qs[:200]
        ]
        return JsonResponse(rows, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def pending_requests_count(request):
    """Kutayotgan to'lov so'rovlari soni (badge uchun)."""
    return JsonResponse(
        {"count": PaymentRequest.objects.filter(status="pending").count()}
    )


def get_student_payment_requests(request, student_id):
    """Studentning o'z to'lov so'rovlari (holatini ko'rish uchun)."""
    try:
        qs = PaymentRequest.objects.select_related("student").filter(
            student_id=student_id
        )
        return JsonResponse([_payment_request_row(pr) for pr in qs[:50]], safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def accept_payment_request(request, req_id):
    """Manager so'rovni qabul qiladi: miqdor + sana + oy kiritadi.

    Tegishli Payment yangilanadi, chek rasmi o'chiriladi, tarixda faqat
    manager kiritgan ma'lumot qoladi.
    """
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        pr = PaymentRequest.objects.select_related("student").filter(id=req_id).first()
        if not pr:
            return JsonResponse({"error": "So'rov topilmadi"}, status=404)
        if pr.status != "pending":
            return JsonResponse(
                {"error": "So'rov allaqachon ko'rib chiqilgan"}, status=400
            )

        try:
            amount = int(data.get("amount"))
        except (ValueError, TypeError):
            return JsonResponse({"error": "amount son bo'lishi kerak"}, status=400)
        if amount <= 0:
            return JsonResponse(
                {"error": "amount 0 dan katta bo'lishi kerak"}, status=400
            )

        month = (data.get("month") or "").strip()
        if not month:
            return JsonResponse(
                {"error": "month (YYYY-MM) kiritilishi kerak"}, status=400
            )

        paid_at = None
        paid_at_str = (data.get("paid_at") or "").strip()
        if paid_at_str:
            try:
                paid_at = datetime.strptime(paid_at_str, "%Y-%m-%d").date()
            except ValueError:
                return JsonResponse(
                    {"error": "paid_at format YYYY-MM-DD bo'lishi kerak"}, status=400
                )

        student = pr.student
        fee = effective_monthly_fee(student)
        payment, _ = Payment.objects.get_or_create(
            student=student,
            month=month,
            defaults={
                "stage": student.stage,
                "amount_due": fee,
                "discount": max(0, min(int(student.monthly_discount or 0), fee)),
            },
        )
        # Eski yozuvda narx belgilanmagan bo'lsa — kurs narxiga to'g'rilaymiz
        if (payment.amount_due or 0) <= 0 and fee > 0:
            payment.amount_due = fee
        paid_amount_before = payment.paid_amount or 0
        payment.paid_amount = (payment.paid_amount or 0) + amount
        net_due = max(0, payment.amount_due - payment.discount)
        # To'liq qoplansa (yoki narx belgilanmagan bo'lsa) — to'langan deb belgilaymiz
        if payment.paid_amount >= net_due:
            payment.is_paid = True
            payment.paid_at = timezone.now()
        payment.save()

        # 💵 Kassa jurnali — chek orqali tushgan summa bugungi smenaga
        record_cash_delta(request, payment, paid_amount_before, payment.paid_amount)

        try:
            sync_payment_ontime_coin(payment)
        except Exception:
            logging.getLogger(__name__).exception("payment ontime coin xatosi")

        pr.status = "accepted"
        pr.amount = amount
        pr.month = month
        pr.paid_at = paid_at
        pr.note = str(data.get("note") or pr.note).strip()[:255]
        pr.receipt_b64 = ""  # chek rasmini o'chiramiz
        pr.resolved_at = timezone.now()
        pr.save()

        log_action(
            request,
            "payment.request_accept",
            f"{pr.student} — {month} uchun "
            f"{amount:,} so'm chek qabul qilindi".replace(",", " "),
            target_type="payment_request",
            target_id=pr.id,
            target_name=str(pr.student),
            month=month,
            amount=amount,
        )

        # O'quvchi chekni o'zi yuborgan va menejer uni tasdiqladi —
        # javoban unga to'lov cheki boradi. Qo'lda tasdiqlashda
        # (update_payment) shunday bo'ladi, bu yo'lda esa tushib
        # qolgan edi: so'rov qabul qilinardi, lekin o'quvchi hech
        # qanday tasdiq olmasdi.
        #
        # Bu yerda "to'liq to'landimi" degan shart yo'q: o'quvchi pul
        # o'tkazgan bo'lsa, oy yopilmasa ham chek olishi kerak —
        # {summa} shu safar tushganini, {qolgan} qolgan qarzni
        # ko'rsatadi.
        try:
            from . import telegram as tg

            tg.send_receipt(payment, amount=amount)
        except Exception:  # noqa: BLE001 — chek javobni to'smasin
            logging.getLogger(__name__).exception("Chek yuborilmadi")

        wallet = compute_wallet(student)
        return JsonResponse(
            {
                "message": "To'lov qabul qilindi",
                "status": pr.status,
                "amount": pr.amount,
                "month": pr.month,
                "paid_at": str(pr.paid_at) if pr.paid_at else None,
                "wallet_balance": wallet.get("balance", 0),
                "wallet_debt": wallet.get("debt", 0),
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def reject_payment_request(request, req_id):
    """Manager so'rovni rad etadi — chek rasmi o'chiriladi."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body or "{}")
        pr = PaymentRequest.objects.filter(id=req_id).first()
        if not pr:
            return JsonResponse({"error": "So'rov topilmadi"}, status=404)
        pr.status = "rejected"
        pr.note = str(data.get("note") or pr.note).strip()[:255]
        pr.receipt_b64 = ""
        pr.resolved_at = timezone.now()
        pr.save()
        log_action(
            request,
            "payment.request_reject",
            f"{pr.student} yuborgan chek rad etildi"
            + (f" — {pr.note}" if pr.note else ""),
            target_type="payment_request",
            target_id=pr.id,
            target_name=str(pr.student),
            note=pr.note,
        )
        return JsonResponse({"message": "So'rov rad etildi", "status": pr.status})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


def get_groups(request):
    """Guruhlar ro'yxati.

    Ustoz faqat o'z guruhlarini ko'radi — boshqa ustozning guruhi
    umuman qaytarilmaydi. Menejer va admin o'quvchi hammasini ko'radi.
    Chaqiruvchi 'X-User-Phone' orqali aniqlanadi; sarlavha bo'lmasa
    (eski mijoz) eski holat — hammasi qaytariladi.
    """
    try:
        groups = Group.objects.select_related("teacher", "course").prefetch_related(
            "students"
        )

        teacher = _caller_own_teacher(request)
        if teacher:
            groups = groups.filter(teacher_id=teacher.id)
        elif request.GET.get("teacher_id"):
            try:
                groups = groups.filter(teacher_id=int(request.GET["teacher_id"]))
            except ValueError:
                return JsonResponse({"error": "Invalid teacher_id"}, status=400)

        serializer = GroupSerializer(groups, many=True)
        return JsonResponse(serializer.data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_group(request, group_id):
    """Muayyan guruh ma'lumotlari."""
    try:
        try:
            group_id = int(group_id)
        except ValueError:
            return JsonResponse({"error": "Invalid group_id"}, status=400)

        group = (
            Group.objects.select_related("teacher", "course")
            .filter(id=group_id)
            .first()
        )
        if not group:
            return JsonResponse({"error": "Guruh topilmadi"}, status=404)

        # Ustoz o'zganing guruhini ID orqali ham ocha olmasin
        teacher = _caller_own_teacher(request)
        if teacher and group.teacher_id != teacher.id:
            return JsonResponse({"error": "Guruh topilmadi"}, status=404)
        serializer = GroupSerializer(group)
        return JsonResponse(serializer.data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def create_group(request):
    """Yangi guruh yaratish.

    Body: {
        name: "Guruh nomi",
        teacher_id: <id>,
        course_id: <id>,
        lesson_time: "HH:MM" (default: "09:00"),
        room: "xona",
        schedule: "odd" | "even",
        students: [<id>, <id>, ...]
    }
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)

        # ✅ Guruh nomi majburiy
        name = data.get("name", "").strip()
        if not name:
            return JsonResponse({"error": "Guruh nomi kiritilishi shart"}, status=400)

        # ✅ O'qituvchi validation
        teacher = None
        if data.get("teacher_id"):
            try:
                teacher_id = int(data["teacher_id"])
                teacher = Teacher.objects.filter(id=teacher_id).first()
                if not teacher:
                    return JsonResponse(
                        {"error": f"O'qituvchi (ID={teacher_id}) topilmadi"}, status=404
                    )
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "teacher_id son bo'lishi kerak"}, status=400
                )

        # ✅ Kurs validation
        course = None
        if data.get("course_id"):
            try:
                course_id = int(data["course_id"])
                course = Course.objects.filter(id=course_id).first()
                if not course:
                    return JsonResponse(
                        {"error": f"Kurs (ID={course_id}) topilmadi"}, status=404
                    )
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "course_id son bo'lishi kerak"}, status=400
                )

        # ✅ Dars vaqti validation
        lesson_time = data.get("lesson_time", "09:00").strip()
        try:
            parts = lesson_time.split(":")
            if len(parts) != 2:
                raise ValueError("Invalid format")
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError("Invalid time")
        except (ValueError, IndexError):
            return JsonResponse(
                {
                    "error": "lesson_time 'HH:MM' formati bo'lishi kerak (masalan: '14:30')"
                },
                status=400,
            )

        # ✅ Xona validation
        room = data.get("room", "").strip()
        if len(room) > 50:
            return JsonResponse(
                {"error": "Xona nomi 50 belgidan ortiq bo'lishi mumkin emas"},
                status=400,
            )

        # ✅ Schedule validation
        schedule = data.get("schedule", "odd").strip()
        if schedule not in ["odd", "even", "daily"]:
            return JsonResponse(
                {"error": "schedule 'odd' yoki 'even' bo'lishi kerak"}, status=400
            )

        # ✅ Guruh ochilgan sana (ixtiyoriy) — oylik to'lov shu kundan boshlanadi
        opened_date, opened_err = parse_opened_date(data.get("opened_date"))
        if opened_err:
            return JsonResponse({"error": opened_err}, status=400)

        # ✅ Student IDs validation
        student_ids = data.get("students", [])
        validated_students = []
        if student_ids:
            if not isinstance(student_ids, list):
                return JsonResponse(
                    {"error": "students ro'yxat bo'lishi kerak"}, status=400
                )

            try:
                student_ids = [int(sid) for sid in student_ids]
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "students ichidagi ID'lar son bo'lishi kerak"}, status=400
                )

            # O'quvchilar mavjudligini check qilish
            existing_students = set(
                Student.objects.filter(id__in=student_ids).values_list("id", flat=True)
            )
            not_found = [sid for sid in student_ids if sid not in existing_students]
            if not_found:
                return JsonResponse(
                    {"error": f"Topilmagan o'quvchi ID'lari: {not_found}"}, status=404
                )
            validated_students = student_ids

        # ✅ Guruhni atomic transaction'da yaratish
        with transaction.atomic():
            group = Group.objects.create(
                name=name,
                teacher=teacher,
                course=course,
                lesson_time=lesson_time,
                room=room,
                schedule=schedule,
                opened_date=opened_date,
            )

            # Talabalar qo'shish
            if validated_students:
                group.students.set(validated_students)

        log_action(
            request,
            "group.create",
            f"«{name}» guruhi yaratildi"
            + (f" — ustoz {group.teacher.name}" if group.teacher else "")
            + (f", {len(validated_students)} o'quvchi" if validated_students else ""),
            target_type="group",
            target_id=group.id,
            target_name=name,
            teacher=group.teacher.name if group.teacher else None,
            students=len(validated_students) if validated_students else 0,
        )

        serializer = GroupSerializer(group)
        return JsonResponse(
            {"message": f"'{name}' guruh muvaffaqiyatli yaratildi", **serializer.data},
            status=201,
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON format noto'g'ri"}, status=400)
    except Exception as e:
        return JsonResponse({"error": f"Xato: {str(e)}"}, status=400)


@csrf_exempt
def update_group(request, group_id):
    """Guruh ma'lumotlarini yangilash."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
        try:
            group_id = int(group_id)
        except ValueError:
            return JsonResponse({"error": "Invalid group_id"}, status=400)

        group = Group.objects.filter(id=group_id).first()
        if not group:
            return JsonResponse({"error": "Guruh topilmadi"}, status=404)

        if "name" in data:
            group.name = data["name"].strip()
        if "teacher_id" in data:
            try:
                group.teacher = Teacher.objects.filter(
                    id=int(data["teacher_id"])
                ).first()
            except ValueError:
                group.teacher = None
        if "course_id" in data:
            try:
                group.course = Course.objects.filter(id=int(data["course_id"])).first()
            except ValueError:
                group.course = None
        if "lesson_time" in data:
            group.lesson_time = data["lesson_time"].strip()
        if "room" in data:
            group.room = data["room"].strip()
        if "schedule" in data:
            schedule = data["schedule"].strip()
            if schedule in ["odd", "even", "daily"]:
                group.schedule = schedule
        if "opened_date" in data:
            opened_date, opened_err = parse_opened_date(data.get("opened_date"))
            if opened_err:
                return JsonResponse({"error": opened_err}, status=400)
            group.opened_date = opened_date

        # Menejer guruhni tahrirlab saqladi — import qo'ygan "tekshirish kerak"
        # belgisi endi keraksiz
        if group.needs_review and ("lesson_time" in data or "schedule" in data):
            group.needs_review = False
            group.review_note = ""

        group.save()

        # ✅ TO'G'RI - ManyToMany
        if "students" in data:
            try:
                student_ids = [int(sid) for sid in data["students"]]
                group.students.set(student_ids)
            except (ValueError, TypeError) as e:
                return JsonResponse(
                    {"error": f"Invalid student IDs: {str(e)}"}, status=400
                )

        log_action(
            request,
            "group.update",
            f"«{group.name}» guruhi tahrirlandi: "
            + (", ".join(sorted(data.keys())) or "o'zgarishsiz"),
            target_type="group",
            target_id=group.id,
            target_name=group.name,
            fields=sorted(data.keys()),
        )

        serializer = GroupSerializer(group)
        return JsonResponse(serializer.data, safe=False)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def delete_group(request, group_id):
    """Guruhni o'chirish."""
    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        try:
            group_id = int(group_id)
        except ValueError:
            return JsonResponse({"error": "Invalid group_id"}, status=400)

        group = Group.objects.filter(id=group_id).first()
        if not group:
            return JsonResponse({"error": "Guruh topilmadi"}, status=404)

        # ?with_students=1 — kurs tugaganda studentlarni ham o'chirish.
        # Faqat boshqa guruhga a'zo bo'lmagan studentlar o'chiriladi.
        deleted_students = 0
        if request.GET.get("with_students") in ("1", "true"):
            for s in list(group.students.all()):
                if s.groups.count() <= 1:
                    s.delete()
                    deleted_students += 1

        group_name = group.name
        group.delete()
        log_action(
            request,
            "group.delete",
            f"«{group_name}» guruhi o'chirildi"
            + (
                f" — {deleted_students} ta o'quvchi ham o'chdi"
                if deleted_students
                else ""
            ),
            target_type="group",
            target_id=group_id,
            target_name=group_name,
            deleted_students=deleted_students,
        )
        return JsonResponse(
            {"message": "Guruh o'chirildi!", "deleted_students": deleted_students}
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# COIN OPERATIONS
# ─────────────────────────────


def get_student_coins(request, student_id):
    """O'quvchining coin balansini ko'rish."""
    try:
        try:
            student_id = int(student_id)
        except ValueError:
            return JsonResponse({"error": "Invalid student_id"}, status=400)

        student = Student.objects.filter(id=student_id).first()
        if not student:
            return JsonResponse({"error": "O'quvchi topilmadi"}, status=404)
        return JsonResponse(
            {"student_id": student.id, "coin_balance": student.coin_balance}
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_coin_transactions(request, student_id):
    """O'quvchining coin tranzaksiya tarixi."""
    try:
        try:
            student_id = int(student_id)
        except ValueError:
            return JsonResponse({"error": "Invalid student_id"}, status=400)

        qs = (
            CoinTransaction.objects.filter(student_id=student_id)
            .select_related("given_by")
            .order_by("-created_at")
        )
        data = [
            {
                "id": t.id,
                "reason": t.reason,
                "reason_display": t.get_reason_display(),
                "amount": t.amount,
                "note": t.note,
                "given_by": t.given_by.name if t.given_by else "Tizim",
                "created_at": t.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for t in qs
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def give_manual_coins(request):
    """O'quvchiga qo'lda coin berish."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        student_id = data.get("student_id")

        if not student_id:
            return JsonResponse({"error": "student_id kiritilmadi"}, status=400)

        student = Student.objects.filter(id=student_id).first()
        if not student:
            return JsonResponse({"error": "O'quvchi topilmadi"}, status=404)

        teacher = None
        if data.get("teacher_id"):
            teacher = Teacher.objects.filter(id=data["teacher_id"]).first()

        reason = data.get("reason", "manual").strip()
        amount = data.get("amount")

        if amount is None:
            amount = COIN_QUICK_AMOUNTS.get(reason)

        if amount is None:
            return JsonResponse({"error": "amount kiritilmadi"}, status=400)

        try:
            amount = int(amount)
        except (ValueError, TypeError):
            return JsonResponse({"error": "amount son bo'lishi kerak"}, status=400)

        # Oylik manual bonus cheklovi
        if reason == "manual":
            teacher_id = teacher.id if teacher else None
            if monthly_bonus_used_ids([student.id], teacher_id):
                return JsonResponse(
                    {"error": "Bu o'quvchiga bu oy allaqachon bonus berilgan"},
                    status=400,
                )

        new_balance = apply_coin_transaction(
            student,
            amount,
            reason,
            given_by=teacher,
            note=data.get("note", "").strip(),
        )

        log_action(
            request,
            "coins.give",
            f"{student}ga {amount:+d} coin ({reason}) — balans {new_balance}",
            target_type="student",
            target_id=student.id,
            target_name=str(student),
            amount=amount,
            reason=reason,
        )

        return JsonResponse(
            {
                "message": "Coin berildi!",
                "student_id": student.id,
                "coin_balance": new_balance,
                "amount": amount,
                "reason": reason,
                # Erkin bonus berilgan bo'lsa panel tugmani darhol yopadi
                "bonus_used": reason == "manual",
            },
            status=201,
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


def get_group_leaderboard(request):
    """Guruhlar reytingi — a'zolarining o'rtacha coini bo'yicha.

    O'rtacha, jami emas: aks holda 20 kishilik guruh 5 kishilikni
    doim ortda qoldirardi va kichik guruhlar umuman raqobatlasha
    olmasdi. `?limit=` bilan faqat yuqori N tasi olinadi (jadval
    ekranida 5 talik chiqadi).
    """
    try:
        try:
            limit = min(50, max(1, int(request.GET.get("limit") or 10)))
        except ValueError:
            limit = 10

        rows = []
        groups = Group.objects.select_related("teacher").prefetch_related("students")
        for g in groups:
            # Ustozlarning admin profillari o'rtachani buzmasligi kerak
            members = [
                s for s in g.students.all() if not (s.is_admin or s.is_excellence)
            ]
            if not members:
                continue
            total = sum(s.coin_balance or 0 for s in members)
            rows.append(
                {
                    "group_id": g.id,
                    "name": g.name,
                    "teacher_name": g.teacher.name if g.teacher else "",
                    "students_count": len(members),
                    "total_coins": total,
                    "average_coins": round(total / len(members), 1),
                }
            )

        rows.sort(key=lambda r: r["average_coins"], reverse=True)
        for i, r in enumerate(rows[:limit], start=1):
            r["rank"] = i

        return JsonResponse(rows[:limit], safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_receipt_settings(request):
    """Chek matni va o'rniga qo'yiladigan kalitlar ro'yxati."""
    denied = require_permission(request, "receipt.settings")
    if denied:
        return denied
    from .models import ReceiptSettings

    s = ReceiptSettings.get_settings()
    return JsonResponse(
        {
            "enabled": s.enabled,
            "template": s.template,
            "center_name": s.center_name,
            "default_template": ReceiptSettings.DEFAULT_TEMPLATE,
            "placeholders": [
                {"key": k, "label": v} for k, v in ReceiptSettings.PLACEHOLDERS
            ],
        }
    )


@csrf_exempt
def update_receipt_settings(request):
    """Chek matnini saqlaydi."""
    if request.method not in ("POST", "PATCH"):
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_permission(request, "receipt.settings")
    if denied:
        return denied
    try:
        from .models import ReceiptSettings

        data = json.loads(request.body)
        s = ReceiptSettings.get_settings()

        if "enabled" in data:
            s.enabled = bool(data["enabled"])
        if "center_name" in data:
            s.center_name = str(data["center_name"] or "").strip()[:100]
        if "template" in data:
            template = str(data["template"] or "").strip()
            if not template:
                return JsonResponse(
                    {"error": "Chek matni bo'sh bo'lishi mumkin emas"}, status=400
                )
            s.template = template[:4000]
        s.save()

        log_action(
            request,
            "receipt.settings",
            "To'lov cheki matni o'zgartirildi"
            + ("" if s.enabled else " (yuborish o'chirildi)"),
            target_type="settings",
            target_name="To'lov cheki",
            enabled=s.enabled,
        )
        return JsonResponse({"message": "Saqlandi", "enabled": s.enabled})
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def preview_receipt(request):
    """Chek qanday ko'rinishini namuna ma'lumot bilan ko'rsatadi."""
    denied = require_permission(request, "receipt.settings")
    if denied:
        return denied
    try:
        from . import telegram as tg
        from .models import ReceiptSettings

        data = json.loads(request.body or "{}") if request.body else {}
        s = ReceiptSettings.get_settings()
        template = str(data.get("template") or s.template)

        sample = {
            "{ism}": "Aliyev Vali",
            "{oy}": "Iyul 2026",
            "{summa}": "500 000 so'm",
            "{jami}": "600 000 so'm",
            "{qolgan}": "100 000 so'm",
            "{sana}": timezone.localdate().strftime("%d.%m.%Y"),
            "{markaz}": s.center_name,
            "{guruh}": "Frontend-1",
        }
        for k, v in sample.items():
            template = template.replace(k, v)
        return JsonResponse({"preview": template})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def send_message_leads(request):
    """Botga ulangan leadlarga reklama xabari. Body: {text}"""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_permission(request, "messages.leads")
    if denied:
        return denied
    try:
        data = json.loads(request.body)
        text = (data.get("text") or "").strip()
        if not text:
            return JsonResponse({"error": "text majburiy"}, status=400)

        from . import telegram as tg

        sent, failed = tg.send_to_leads(text)
        log_action(
            request,
            "message.leads",
            f"{sent} ta leadga reklama xabari: {text[:60]}",
            target_type="broadcast",
            target_name="Leadlar",
            sent=sent,
            failed=failed,
        )
        return JsonResponse({"sent": sent, "failed": failed})
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def send_message_teachers(request):
    """Ustozlarga xabar. Body: {text, teacher_ids?}"""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_permission(request, "messages.teachers")
    if denied:
        return denied
    try:
        data = json.loads(request.body)
        text = (data.get("text") or "").strip()
        if not text:
            return JsonResponse({"error": "text majburiy"}, status=400)

        ids = data.get("teacher_ids")
        from . import telegram as tg

        sent, failed = tg.send_to_teachers(text, ids)
        log_action(
            request,
            "message.teachers",
            f"{sent} ta ustozga xabar: {text[:60]}",
            target_type="broadcast",
            target_name="Ustozlar",
            sent=sent,
            failed=failed,
        )
        return JsonResponse({"sent": sent, "failed": failed})
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_leaderboard(request):
    """Eng ko'p coin to'plagan o'quvchilar reytingi."""
    try:
        teacher_id = request.GET.get("teacher_id", "").strip()
        qs = Student.objects.select_related("teacher").filter(
            is_admin=False, is_excellence=False, is_graduate=False
        )
        if teacher_id:
            try:
                qs = qs.filter(teacher_id=int(teacher_id))
            except ValueError:
                return JsonResponse({"error": "Invalid teacher_id"}, status=400)

        qs = qs.order_by("-coin_balance", "name")[:100]

        data = [
            {
                "rank": i + 1,
                "id": s.id,
                "name": s.name,
                "surname": s.surname,
                "teacher_name": s.teacher.name if s.teacher else "",
                "coin_balance": s.coin_balance,
            }
            for i, s in enumerate(qs)
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# ─────────────────────────────
# PRODUCTS (DO'KON)
# ─────────────────────────────


def get_products(request):
    """Faol mahsulotlar ro'yxati."""
    try:
        qs = Product.objects.filter(is_active=True).order_by("price_coins")
        data = [
            {
                "id": p.id,
                "name": p.name,
                "image": p.image,
                "price_coins": p.price_coins,
                "description": p.description,
                "stock": p.stock,
            }
            for p in qs
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_all_products(request):
    """Admin uchun: barcha mahsulotlar."""
    try:
        qs = Product.objects.all().order_by("-created_at")
        data = [
            {
                "id": p.id,
                "name": p.name,
                "image": p.image,
                "price_coins": p.price_coins,
                "description": p.description,
                "is_active": p.is_active,
                "stock": p.stock,
            }
            for p in qs
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def create_product(request):
    """Yangi mahsulot yaratish."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        name = data.get("name", "").strip()

        if not name:
            return JsonResponse(
                {"error": "Mahsulot nomi kiritilishi shart"}, status=400
            )

        try:
            price_coins = int(data.get("price_coins", 0))
        except (ValueError, TypeError):
            return JsonResponse({"error": "price_coins son bo'lishi kerak"}, status=400)

        product = Product.objects.create(
            name=name,
            image=data.get("image", "").strip(),
            price_coins=price_coins,
            description=data.get("description", "").strip(),
            is_active=data.get("is_active", True),
            stock=data.get("stock"),
        )

        log_action(
            request,
            "shop.product",
            f"«{product.name}» mahsuloti qo'shildi — {product.price_coins} coin",
            target_type="product",
            target_id=product.id,
            target_name=product.name,
        )

        # Botga ulangan o'quvchilarga e'lon qilamiz. Faol bo'lmagan
        # mahsulot do'konda ko'rinmaydi — u haqda xabar ham bermaymiz.
        # `notify: false` yuborilsa jim qo'shiladi.
        notified = False
        if product.is_active and data.get("notify", True):
            try:
                from . import telegram as tg

                tg.broadcast_product(product)
                notified = True
            except Exception:  # noqa: BLE001 — e'lon mahsulotni to'smasin
                logging.getLogger(__name__).exception("Mahsulot e'loni ketmadi")

        return JsonResponse(
            {
                "id": product.id,
                "message": "Mahsulot qo'shildi!",
                "notified": notified,
            },
            status=201,
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def update_product(request, product_id):
    """Mahsulotni yangilash."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        try:
            product_id = int(product_id)
        except ValueError:
            return JsonResponse({"error": "Invalid product_id"}, status=400)

        product = Product.objects.filter(id=product_id).first()
        if not product:
            return JsonResponse({"error": "Mahsulot topilmadi"}, status=404)

        if "name" in data:
            product.name = data["name"].strip()
        if "image" in data:
            product.image = data["image"].strip()
        if "price_coins" in data:
            try:
                product.price_coins = int(data["price_coins"])
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "price_coins son bo'lishi kerak"}, status=400
                )
        if "description" in data:
            product.description = data["description"].strip()
        if "is_active" in data:
            product.is_active = data["is_active"]
        if "stock" in data:
            product.stock = data["stock"]
        product.save()
        return JsonResponse({"message": "Mahsulot yangilandi!"})
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def delete_product(request, product_id):
    """Mahsulotni o'chirish."""
    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        try:
            product_id = int(product_id)
        except ValueError:
            return JsonResponse({"error": "Invalid product_id"}, status=400)

        product = Product.objects.filter(id=product_id).first()
        if not product:
            return JsonResponse({"error": "Mahsulot topilmadi"}, status=404)
        product.delete()
        return JsonResponse({"message": "Mahsulot o'chirildi!"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# ORDERS (Buyurtmalar)
# ─────────────────────────────


@csrf_exempt
def create_order(request):
    """O'quvchi buyurtma qilish."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        student_id = data.get("student_id")

        if not student_id:
            return JsonResponse({"error": "student_id kiritilmadi"}, status=400)

        student = Student.objects.filter(id=student_id).first()
        if not student:
            return JsonResponse({"error": "O'quvchi topilmadi"}, status=404)

        product_id = data.get("product_id")
        if not product_id:
            return JsonResponse({"error": "product_id kiritilmadi"}, status=400)

        product = Product.objects.filter(id=product_id, is_active=True).first()
        if not product:
            return JsonResponse(
                {"error": "Mahsulot topilmadi yoki faol emas"}, status=404
            )

        if product.stock is not None and product.stock <= 0:
            return JsonResponse({"error": "Mahsulot tugagan"}, status=400)

        if student.coin_balance < product.price_coins:
            return JsonResponse(
                {
                    "error": f"Coin yetarli emas. Kerak: {product.price_coins}, mavjud: {student.coin_balance}"
                },
                status=400,
            )

        with transaction.atomic():
            order = Order.objects.create(
                student=student,
                product=product,
                product_name=product.name,
                price_coins=product.price_coins,
                status="pending",
            )

            apply_coin_transaction(
                student,
                -product.price_coins,
                "purchase",
                note=f"Buyurtma #{order.id}: {product.name}",
            )

            if product.stock is not None:
                product.stock -= 1
                product.save(update_fields=["stock"])

        return JsonResponse(
            {
                "id": order.id,
                "message": "Buyurtma yaratildi!",
                "coin_balance": student.coin_balance,
            },
            status=201,
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


def get_student_orders(request, student_id):
    """O'quvchining buyurtmalari."""
    try:
        try:
            student_id = int(student_id)
        except ValueError:
            return JsonResponse({"error": "Invalid student_id"}, status=400)

        qs = Order.objects.filter(student_id=student_id).order_by("-created_at")
        data = [
            {
                "id": o.id,
                "product_name": o.product_name,
                "price_coins": o.price_coins,
                "status": o.status,
                "created_at": o.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for o in qs
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_all_orders(request):
    """Admin uchun: barcha buyurtmalar."""
    try:
        status = request.GET.get("status", "").strip()
        qs = Order.objects.select_related("student").order_by("-created_at")
        if status:
            if status not in ["pending", "approved", "rejected"]:
                return JsonResponse(
                    {
                        "error": "status 'pending', 'approved' yoki 'rejected' bo'lishi kerak"
                    },
                    status=400,
                )
            qs = qs.filter(status=status)

        data = [
            {
                "id": o.id,
                "student_id": o.student.id,
                "student_name": f"{o.student.name} {o.student.surname}",
                "product_name": o.product_name,
                "price_coins": o.price_coins,
                "status": o.status,
                "created_at": o.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for o in qs
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def resolve_order(request, order_id):
    """Buyurtmani tasdiqlash yoki rad etish."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        new_status = data.get("status", "").strip()

        if new_status not in ("approved", "rejected"):
            return JsonResponse(
                {"error": "status 'approved' yoki 'rejected' bo'lishi kerak"},
                status=400,
            )

        try:
            order_id = int(order_id)
        except ValueError:
            return JsonResponse({"error": "Invalid order_id"}, status=400)

        order = Order.objects.select_related("student").filter(id=order_id).first()
        if not order:
            return JsonResponse({"error": "Buyurtma topilmadi"}, status=404)

        if order.status != "pending":
            return JsonResponse(
                {
                    "error": f"Bu buyurtma allaqachon {order.status} holatida, o'zgartira olmaysiz"
                },
                status=400,
            )

        with transaction.atomic():
            if new_status == "rejected":
                apply_coin_transaction(
                    order.student,
                    order.price_coins,
                    "purchase_cancel",
                    note=f"Buyurtma #{order.id} rad etildi, coin qaytarildi",
                )
                if order.product and order.product.stock is not None:
                    order.product.stock += 1
                    order.product.save(update_fields=["stock"])

            order.status = new_status
            order.resolved_at = datetime.now()
            order.save()

        log_action(
            request,
            "order.resolve",
            f"{order.student} — «{order.product_name}» buyurtmasi "
            + ("berildi" if new_status == "approved" else "bekor qilindi"),
            target_type="order",
            target_id=order.id,
            target_name=order.product_name,
            status=new_status,
            price_coins=order.price_coins,
        )

        return JsonResponse(
            {
                "message": f"Buyurtma {new_status} qilindi!",
                "status": order.status,
                "resolved_at": order.resolved_at.strftime("%Y-%m-%d %H:%M"),
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# COURSES
# ─────────────────────────────


def get_courses(request):
    """Barcha kurslar."""
    try:
        courses = Course.objects.all().order_by("name")
        serializer = CourseSerializer(courses, many=True)
        return JsonResponse(serializer.data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_course(request, course_id):
    """Muayyan kurs ma'lumotlari."""
    try:
        try:
            course_id = int(course_id)
        except ValueError:
            return JsonResponse({"error": "Invalid course_id"}, status=400)

        course = Course.objects.filter(id=course_id).first()
        if not course:
            return JsonResponse({"error": "Kurs topilmadi"}, status=404)

        serializer = CourseSerializer(course)
        return JsonResponse(serializer.data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def create_course(request):
    """Yangi kurs yaratish."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
        name = data.get("name", "").strip()

        if not name:
            return JsonResponse({"error": "Kurs nomi kiritilishi shart"}, status=400)

        try:
            monthly_fee = int(data.get("monthly_fee", 0))
        except (ValueError, TypeError):
            return JsonResponse({"error": "monthly_fee son bo'lishi kerak"}, status=400)

        course = Course.objects.create(
            name=name,
            monthly_fee=monthly_fee,
        )
        log_action(
            request,
            "course.create",
            f"«{name}» kursi yaratildi — "
            f"oylik {monthly_fee:,} so'm".replace(",", " "),
            target_type="course",
            target_id=course.id,
            target_name=name,
            monthly_fee=monthly_fee,
        )

        serializer = CourseSerializer(course)
        return JsonResponse(serializer.data, status=201)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def update_course(request, course_id):
    """Kurs ma'lumotlarini yangilash."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
        try:
            course_id = int(course_id)
        except ValueError:
            return JsonResponse({"error": "Invalid course_id"}, status=400)

        course = Course.objects.filter(id=course_id).first()
        if not course:
            return JsonResponse({"error": "Kurs topilmadi"}, status=404)

        fee_before = course.monthly_fee

        if "name" in data:
            course.name = data["name"].strip()
        if "monthly_fee" in data:
            try:
                course.monthly_fee = int(data["monthly_fee"])
            except (ValueError, TypeError):
                return JsonResponse(
                    {"error": "monthly_fee son bo'lishi kerak"}, status=400
                )

        course.save()

        # Narx o'zgardi — shu kursdagi o'quvchilarning to'lanmagan
        # to'lovlari joriy narxga yangilanadi (reaktiv, barcha bo'limda)
        synced = 0
        if course.monthly_fee != fee_before:
            synced = resync_unpaid_amount_due(
                Student.objects.filter(groups__course=course)
                .distinct()
                .prefetch_related("payments", "groups")
            )

        # Narx o'zgarishi butun markazga ta'sir qiladi — alohida ko'rsatamiz
        if course.monthly_fee != fee_before:
            detail = f"narx {fee_before:,} → {course.monthly_fee:,} so'm".replace(
                ",", " "
            )
        else:
            detail = "ma'lumoti tahrirlandi"
        log_action(
            request,
            "course.update",
            f"«{course.name}» kursi: {detail}",
            target_type="course",
            target_id=course.id,
            target_name=course.name,
            before=fee_before,
            after=course.monthly_fee,
            payments_synced=synced,
        )

        serializer = CourseSerializer(course)
        return JsonResponse(serializer.data, safe=False)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def delete_course(request, course_id):
    """Kursni o'chirish."""
    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        try:
            course_id = int(course_id)
        except ValueError:
            return JsonResponse({"error": "Invalid course_id"}, status=400)

        course = Course.objects.filter(id=course_id).first()
        if not course:
            return JsonResponse({"error": "Kurs topilmadi"}, status=404)

        course_name = course.name
        course.delete()
        log_action(
            request,
            "course.delete",
            f"«{course_name}» kursi o'chirildi",
            target_type="course",
            target_id=course_id,
            target_name=course_name,
        )
        return JsonResponse({"message": "Kurs o'chirildi!"})
    except Exception as e:
        if "PROTECT" in str(e) or "protect" in str(e).lower():
            return JsonResponse(
                {
                    "error": "Bu kursga bog'langan guruhlar mavjud. Avval ularni o'chiring yoki boshqa kursga o'tkazing"
                },
                status=400,
            )
        return JsonResponse({"error": str(e)}, status=400)


class IsManagerOrReadOnly(permissions.BasePermission):
    """Faqat admin/excellence yozishi, o'chirishi, o'zgartirishi mumkin."""

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return request.user and request.user.is_authenticated
        return (
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "role", None) in ["admin", "excellence"]
        )


# ─────────────────────────────
# NEWS
# ─────────────────────────────


def get_news(request):
    """Barcha yangiliklar (admin/excellence panel uchun)."""
    try:
        qs = News.objects.select_related("created_by").all().order_by("-created_at")
        data = [
            {
                "id": n.id,
                "title": n.title,
                "content": n.content,
                "priority": n.priority,
                "priority_display": n.get_priority_display(),
                "is_active": n.is_active,
                "created_by_name": (
                    f"{n.created_by.name} {n.created_by.surname}".strip()
                    if n.created_by
                    else ""
                ),
                "created_at": n.created_at.strftime("%Y-%m-%d %H:%M"),
                "updated_at": n.updated_at.strftime("%Y-%m-%d %H:%M"),
                "expires_at": (
                    n.expires_at.strftime("%Y-%m-%dT%H:%M") if n.expires_at else None
                ),
            }
            for n in qs
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_active_news(request):
    """Board sahifasi uchun — faqat faol va muddati o'tmagan yangiliklar."""
    try:
        now = timezone.now()
        qs = (
            News.objects.filter(is_active=True)
            .exclude(expires_at__isnull=False, expires_at__lt=now)
            .order_by("-created_at")
        )
        data = [
            {
                "id": n.id,
                "title": n.title,
                "content": n.content,
                "priority": n.priority,
                "created_at": n.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for n in qs
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_news_detail(request, news_id):
    """Bitta yangilik ma'lumoti."""
    try:
        try:
            news_id = int(news_id)
        except ValueError:
            return JsonResponse({"error": "Invalid news_id"}, status=400)

        news = News.objects.filter(id=news_id).first()
        if not news:
            return JsonResponse({"error": "Yangilik topilmadi"}, status=404)

        return JsonResponse(
            {
                "id": news.id,
                "title": news.title,
                "content": news.content,
                "priority": news.priority,
                "is_active": news.is_active,
                "created_at": news.created_at.strftime("%Y-%m-%d %H:%M"),
                "expires_at": (
                    news.expires_at.strftime("%Y-%m-%dT%H:%M")
                    if news.expires_at
                    else None
                ),
            }
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _check_news_permission(user_id):
    """user_id — Student.id bo'lishi kerak, is_excellence yoki is_admin bo'lishi shart."""
    if not user_id:
        return None, JsonResponse({"error": "user_id kiritilishi shart"}, status=400)

    student = Student.objects.filter(id=user_id).first()
    if not student or not (student.is_excellence or student.is_admin):
        return None, JsonResponse({"error": "Ruxsat yo'q"}, status=403)

    return student, None


@csrf_exempt
def create_news(request):
    """Yangilik qo'shish — faqat is_excellence yoki is_admin studentga ruxsat."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)

        student, error_response = _check_news_permission(data.get("user_id"))
        if error_response:
            return error_response

        title = data.get("title", "").strip()
        content = data.get("content", "").strip()

        if not title:
            return JsonResponse({"error": "title kiritilishi shart"}, status=400)
        if not content:
            return JsonResponse({"error": "content kiritilishi shart"}, status=400)

        priority = data.get("priority", "normal")
        if priority not in dict(News.PRIORITY_CHOICES):
            return JsonResponse({"error": "Noto'g'ri priority qiymati"}, status=400)

        expires_at = data.get("expires_at") or None
        if expires_at:
            try:
                expires_at = datetime.strptime(expires_at[:16], "%Y-%m-%dT%H:%M")
            except ValueError:
                return JsonResponse(
                    {"error": "expires_at format 'YYYY-MM-DDTHH:MM' bo'lishi kerak"},
                    status=400,
                )

        news = News.objects.create(
            title=title,
            content=content,
            priority=priority,
            is_active=data.get("is_active", True),
            expires_at=expires_at,
            created_by=student,
        )

        log_action(
            request,
            "news.create",
            f"«{news.title}» yangiligi joylandi ({news.get_priority_display()})",
            target_type="news",
            target_id=news.id,
            target_name=news.title,
            priority=news.priority,
        )

        return JsonResponse(
            {
                "id": news.id,
                "message": "Yangilik muvaffaqiyatli qo'shildi!",
                "title": news.title,
                "priority": news.priority,
            },
            status=201,
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def update_news(request, news_id):
    """Yangilikni tahrirlash yoki holatini o'zgartirish."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        try:
            news_id = int(news_id)
        except ValueError:
            return JsonResponse({"error": "Invalid news_id"}, status=400)

        news = News.objects.filter(id=news_id).first()
        if not news:
            return JsonResponse({"error": "Yangilik topilmadi"}, status=404)

        _, error_response = _check_news_permission(data.get("user_id"))
        if error_response:
            return error_response

        if "title" in data:
            title = data["title"].strip()
            if not title:
                return JsonResponse(
                    {"error": "title bo'sh bo'lishi mumkin emas"}, status=400
                )
            news.title = title

        if "content" in data:
            content = data["content"].strip()
            if not content:
                return JsonResponse(
                    {"error": "content bo'sh bo'lishi mumkin emas"}, status=400
                )
            news.content = content

        if "priority" in data:
            if data["priority"] not in dict(News.PRIORITY_CHOICES):
                return JsonResponse({"error": "Noto'g'ri priority qiymati"}, status=400)
            news.priority = data["priority"]

        if "is_active" in data:
            news.is_active = bool(data["is_active"])

        if "expires_at" in data:
            expires_at = data["expires_at"]
            if expires_at:
                try:
                    news.expires_at = datetime.strptime(
                        expires_at[:16], "%Y-%m-%dT%H:%M"
                    )
                except ValueError:
                    return JsonResponse(
                        {
                            "error": "expires_at format 'YYYY-MM-DDTHH:MM' bo'lishi kerak"
                        },
                        status=400,
                    )
            else:
                news.expires_at = None

        news.save()

        log_action(
            request,
            "news.update",
            f"«{news.title}» yangiligi tahrirlandi"
            + ("" if news.is_active else " (yashirildi)"),
            target_type="news",
            target_id=news.id,
            target_name=news.title,
            is_active=news.is_active,
        )

        return JsonResponse(
            {
                "message": "Yangilik yangilandi!",
                "id": news.id,
                "title": news.title,
                "is_active": news.is_active,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def delete_news(request, news_id):
    """Yangilikni o'chirish."""
    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        try:
            news_id = int(news_id)
        except ValueError:
            return JsonResponse({"error": "Invalid news_id"}, status=400)

        news = News.objects.filter(id=news_id).first()
        if not news:
            return JsonResponse({"error": "Yangilik topilmadi"}, status=404)

        news_title = news.title
        news.delete()
        log_action(
            request,
            "news.delete",
            f"«{news_title}» yangiligi o'chirildi",
            target_type="news",
            target_id=news_id,
            target_name=news_title,
        )
        return JsonResponse({"message": "Yangilik o'chirildi!"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────
# EXPENSES (XARAJATLAR)
#
# Moliya bo'limi supermenejerga o'tkazildi — oddiy menejer xarajat va
# foyda/zararni ko'rmaydi. Shuning uchun quyidagi hamma endpoint
# `require_super` bilan yopilgan.
# ─────────────────────────────


def get_expenses(request):
    """Barcha xarajatlar (ixtiyoriy: oy bo'yicha filter)."""
    denied = require_super(request)
    if denied:
        return denied
    try:
        month = request.GET.get("month", "").strip()
        qs = Expense.objects.all().order_by("-date", "-created_at")

        if month:
            try:
                year, mon = month.split("-")
                qs = qs.filter(date__year=int(year), date__month=int(mon))
            except ValueError:
                return JsonResponse(
                    {"error": "month format 'YYYY-MM' bo'lishi kerak"}, status=400
                )

        data = [
            {
                "id": e.id,
                "title": e.title,
                "amount": e.amount,
                "category": e.category,
                "category_display": e.get_category_display(),
                "date": str(e.date),
                "note": e.note,
            }
            for e in qs
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def create_expense(request):
    """Yangi xarajat qo'shish."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied
    try:
        data = json.loads(request.body)
        title = data.get("title", "").strip()

        if not title:
            return JsonResponse({"error": "Nomi kiritilishi shart"}, status=400)

        try:
            amount = int(data.get("amount", 0))
        except (ValueError, TypeError):
            return JsonResponse({"error": "amount son bo'lishi kerak"}, status=400)

        if amount <= 0:
            return JsonResponse(
                {"error": "amount 0 dan katta bo'lishi kerak"}, status=400
            )

        category = data.get("category", "other")
        if category not in dict(Expense.CATEGORY_CHOICES):
            return JsonResponse({"error": "Noto'g'ri category qiymati"}, status=400)

        expense_date_str = data.get("date", "").strip()
        if expense_date_str:
            try:
                expense_date = datetime.strptime(expense_date_str, "%Y-%m-%d").date()
            except ValueError:
                return JsonResponse(
                    {"error": "date format 'YYYY-MM-DD' bo'lishi kerak"}, status=400
                )
        else:
            expense_date = timezone.now().date()

        expense = Expense.objects.create(
            title=title,
            amount=amount,
            category=category,
            date=expense_date,
            note=data.get("note", "").strip(),
        )
        log_action(
            request,
            "expense.create",
            f"Xarajat: {expense.title} — "
            f"{expense.amount:,} so'm ({expense.get_category_display()})".replace(
                ",", " "
            ),
            target_type="expense",
            target_id=expense.id,
            target_name=expense.title,
            amount=expense.amount,
            category=expense.category,
        )
        return JsonResponse(
            {
                "id": expense.id,
                "message": "Xarajat qo'shildi!",
                "title": expense.title,
                "amount": expense.amount,
                "category": expense.category,
                "category_display": expense.get_category_display(),
                "date": str(expense.date),
                "note": expense.note,
            },
            status=201,
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def update_expense(request, expense_id):
    """Xarajatni yangilash."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied
    try:
        data = json.loads(request.body)
        try:
            expense_id = int(expense_id)
        except ValueError:
            return JsonResponse({"error": "Invalid expense_id"}, status=400)

        expense = Expense.objects.filter(id=expense_id).first()
        if not expense:
            return JsonResponse({"error": "Xarajat topilmadi"}, status=404)

        if "title" in data:
            title = data["title"].strip()
            if not title:
                return JsonResponse(
                    {"error": "title bo'sh bo'lishi mumkin emas"}, status=400
                )
            expense.title = title

        if "amount" in data:
            try:
                expense.amount = int(data["amount"])
            except (ValueError, TypeError):
                return JsonResponse({"error": "amount son bo'lishi kerak"}, status=400)

        if "category" in data:
            if data["category"] not in dict(Expense.CATEGORY_CHOICES):
                return JsonResponse({"error": "Noto'g'ri category qiymati"}, status=400)
            expense.category = data["category"]

        if "date" in data:
            try:
                expense.date = datetime.strptime(data["date"], "%Y-%m-%d").date()
            except ValueError:
                return JsonResponse(
                    {"error": "date format 'YYYY-MM-DD' bo'lishi kerak"}, status=400
                )

        if "note" in data:
            expense.note = data["note"].strip()

        expense.save()
        return JsonResponse({"message": "Xarajat yangilandi!"})
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def delete_expense(request, expense_id):
    """Xarajatni o'chirish."""
    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied
    try:
        try:
            expense_id = int(expense_id)
        except ValueError:
            return JsonResponse({"error": "Invalid expense_id"}, status=400)

        expense = Expense.objects.filter(id=expense_id).first()
        if not expense:
            return JsonResponse({"error": "Xarajat topilmadi"}, status=404)
        title, amount = expense.title, expense.amount
        expense.delete()
        log_action(
            request,
            "expense.delete",
            f"Xarajat o'chirildi: {title} — {amount:,} so'm".replace(",", " "),
            target_type="expense",
            target_id=expense_id,
            target_name=title,
            amount=amount,
        )
        return JsonResponse({"message": "Xarajat o'chirildi!"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


def get_finance_summary(request):
    denied = require_super(request)
    if denied:
        return denied
    try:
        month = request.GET.get("month", datetime.now().strftime("%Y-%m")).strip()
        try:
            year, mon = month.split("-")
            int(year), int(mon)
        except ValueError:
            return JsonResponse(
                {"error": "month format 'YYYY-MM' bo'lishi kerak"}, status=400
            )

        students = Student.objects.filter(
            is_admin=False, is_excellence=False, is_graduate=False
        )
        total_students = students.count()

        month_payments = Payment.objects.filter(month=month)
        generated_count = month_payments.count()
        paid_count = month_payments.filter(is_paid=True).count()
        unpaid_count = generated_count - paid_count
        not_generated_count = total_students - generated_count

        stage_prices = {sp.stage: sp.price for sp in StagePrice.objects.all()}

        generated_student_ids = set(month_payments.values_list("student_id", flat=True))

        # Payment yaratilgan studentlar uchun ularning haqiqiy amount_due
        # qiymati ishlatiladi — chegirmadan keyin: markaz qo'liga chegirma
        # ayrilgan summa tushadi, aks holda "kutilgan" doim oshib ketardi
        # va "qolgan" hech qachon nolga tushmasdi.
        expected_from_generated = (
            month_payments.annotate(
                net_due=Greatest(
                    F("amount_due") - F("discount"),
                    Value(0),
                    output_field=IntegerField(),
                )
            ).aggregate(total=Sum("net_due"))["total"]
            or 0
        )

        # Payment yaratilmagan studentlar uchun joriy stage narxi bo'yicha hisoblanadi
        expected_from_not_generated = 0
        for s in students.exclude(id__in=generated_student_ids):
            expected_from_not_generated += stage_prices.get(s.stage, 0)

        expected_total = expected_from_generated + expected_from_not_generated

        collected_total = (
            month_payments.aggregate(total=Sum("paid_amount"))["total"] or 0
        )
        remaining_total = expected_total - collected_total

        year_int, mon_int = int(year), int(mon)
        month_expenses = Expense.objects.filter(
            date__year=year_int, date__month=mon_int
        )
        expenses_total = month_expenses.aggregate(total=Sum("amount"))["total"] or 0

        profit = collected_total - expenses_total

        return JsonResponse(
            {
                "month": month,
                "total_students": total_students,
                "generated_count": generated_count,
                "not_generated_count": not_generated_count,
                "paid_count": paid_count,
                "unpaid_count": unpaid_count,
                "expected_total": expected_total,
                "collected_total": collected_total,
                "remaining_total": remaining_total,
                "expenses_total": expenses_total,
                "profit": profit,
            }
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    """
    Berilgan oy uchun to'liq moliyaviy hisobot:
    - jami studentlar soni
    - shu oy uchun to'lov yozuvi yaratilgan studentlar soni
    - to'lagan / to'lamagan studentlar soni
    - agar hammasi to'lasa qancha pul yig'ilishi kerak (kutilayotgan summa)
    - hozircha qancha pul yig'ildi (haqiqiy tushgan pul)
    - shu oy uchun xarajatlar
    - sof foyda/zarar
    """
    try:
        month = request.GET.get("month", datetime.now().strftime("%Y-%m")).strip()
        try:
            year, mon = month.split("-")
            int(year), int(mon)
        except ValueError:
            return JsonResponse(
                {"error": "month format 'YYYY-MM' bo'lishi kerak"}, status=400
            )

        total_students = Student.objects.filter(
            is_admin=False, is_excellence=False
        ).count()

        month_payments = Payment.objects.filter(month=month)
        generated_count = month_payments.count()
        paid_count = month_payments.filter(is_paid=True).count()
        unpaid_count = generated_count - paid_count
        not_generated_count = total_students - generated_count

        expected_total = month_payments.aggregate(total=Sum("amount_due"))["total"] or 0
        collected_total = (
            month_payments.aggregate(total=Sum("paid_amount"))["total"] or 0
        )
        remaining_total = expected_total - collected_total

        year_int, mon_int = int(year), int(mon)
        month_expenses = Expense.objects.filter(
            date__year=year_int, date__month=mon_int
        )
        expenses_total = month_expenses.aggregate(total=Sum("amount"))["total"] or 0

        profit = collected_total - expenses_total

        return JsonResponse(
            {
                "month": month,
                "total_students": total_students,
                "generated_count": generated_count,
                "not_generated_count": not_generated_count,
                "paid_count": paid_count,
                "unpaid_count": unpaid_count,
                "expected_total": expected_total,
                "collected_total": collected_total,
                "remaining_total": remaining_total,
                "expenses_total": expenses_total,
                "profit": profit,
            }
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# ─────────────────────────────
# LEADS (Potensial mijozlar)
# ─────────────────────────────


def get_leads(request):
    """Barcha leadlar (potensial mijozlar). Ixtiyoriy ?sheet= filtri."""
    try:
        qs = Lead.objects.all().order_by("id")
        sheet = request.GET.get("sheet", "").strip()
        if sheet:
            qs = qs.filter(source_sheet=sheet)

        leads = [
            {
                "id": l.id,
                "name": l.name,
                "phone": l.phone,
                "phone2": l.phone2,
                "status": l.status,
                "interest": l.interest,
                "note": l.note,
                "source_sheet": l.source_sheet,
            }
            for l in qs
        ]

        # varaqlar bo'yicha guruhlash uchun statistika
        sheets = list(
            Lead.objects.values("source_sheet")
            .order_by("source_sheet")
            .annotate(count=db_models.Count("id"))
        )

        return JsonResponse({"count": len(leads), "sheets": sheets, "leads": leads})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def delete_lead(request, lead_id):
    """Leadni o'chiradi.

    Jadvaldan import qilingan ro'yxatda takror yoki keraksiz yozuvlar
    uchraydi. Diqqat: `load_sheet_data` qayta ishga tushsa, o'chirilgan
    lead jadvalda qolgan bo'lsa qaytadan paydo bo'ladi.
    """
    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied

    lead = Lead.objects.filter(id=lead_id).first()
    if not lead:
        return JsonResponse({"error": "Lead topilmadi"}, status=404)

    name, phone = lead.name, lead.phone
    lead.delete()
    log_action(
        request,
        "lead.delete",
        f"Lead o'chirildi: {name} ({phone})",
        target_type="lead",
        target_id=lead_id,
        target_name=name,
        phone=phone,
    )
    return JsonResponse({"message": f"{name} o'chirildi"})


def get_ad_channels(request):
    """Telegram reklama kanallari."""
    try:
        channels = [
            {"id": c.id, "username": c.username, "title": c.title, "note": c.note}
            for c in AdChannel.objects.all().order_by("id")
        ]
        return JsonResponse({"count": len(channels), "channels": channels})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_graduates(request):
    """Bitiruvchilar (is_graduate=True) ro'yxati."""
    try:
        qs = (
            Student.objects.select_related("teacher")
            .filter(is_graduate=True)
            .order_by("name")
        )
        data = [
            {
                "id": s.id,
                "name": s.name,
                "surname": s.surname,
                "phone": "" if (s.phone or "").startswith("—") else (s.phone or ""),
                "phone2": s.phone2,
                "teacher_name": s.teacher.name if s.teacher else "",
                "note": s.note,
            }
            for s in qs
        ]
        return JsonResponse({"count": len(data), "graduates": data})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# ─────────────────────────────
# TELEGRAM XABARLAR
# ─────────────────────────────


@csrf_exempt
def tg_webhook(request):
    """Telegram webhook — bot update'larini qabul qiladi."""
    if request.method != "POST":
        return JsonResponse({"ok": True})

    # Faqat Telegram'dan kelgan so'rovni qabul qilamiz. Bu tekshiruvsiz
    # istalgan kishi soxta update yuborib, o'ziga begona o'quvchining
    # saytga kirish ma'lumotlarini yozdirib olishi mumkin edi.
    secret = getattr(settings, "TG_WEBHOOK_SECRET", "")
    if secret:
        got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not secrets.compare_digest(got, secret):
            import logging

            logging.getLogger(__name__).warning(
                "tg_webhook: noto'g'ri secret token (IP=%s)",
                request.META.get("REMOTE_ADDR"),
            )

            # Kalit eskirgan bo'lsa bot abadiy jim qolmasin: webhook'ni
            # joriy kalit bilan qayta ro'yxatdan o'tkazamiz (5 daqiqada
            # bir martadan ko'p emas). Telegram bu update'ni qayta
            # yuboradi va u safar o'tadi.
            try:
                from . import telegram as tg

                tg.resync_webhook()
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).exception("resync_webhook xatosi")

            return HttpResponseForbidden("forbidden")

    try:
        from . import telegram as tg

        update = json.loads(request.body or "{}")
        tg.handle_update(update)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("tg_webhook xatosi")
    # Telegram har doim 200 kutadi, aks holda qayta yuboraveradi
    return JsonResponse({"ok": True})


def tg_status(request):
    """Botga ulangan o'quvchilar (frontend indikator uchun)."""
    try:
        from .models import TelegramSubscriber

        student_ids = list(
            TelegramSubscriber.objects.filter(student__isnull=False)
            .values_list("student_id", flat=True)
            .distinct()
        )
        return JsonResponse(
            {
                "count": len(student_ids),
                "student_ids": student_ids,
                # Token yo'q bo'lsa panel "ulangan" ko'rsatib turib, xabar
                # yubormasligi mumkin edi — holat ko'rinib tursin
                "configured": bool(settings.TG_BOT_TOKEN),
                "bot_username": settings.TG_BOT_USERNAME,
            }
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _do_send(students, text, kind, month=""):
    """Yuborish: kichik ro'yxat sinxron, katta ro'yxat fon oqimida."""
    from . import telegram as tg
    from .models import TelegramSubscriber

    students = list(students)
    linked = TelegramSubscriber.objects.filter(student__in=students).count()

    if len(students) <= 30:
        sent, failed, no_chat = tg.send_to_students(students, text, kind, month)
        return {
            "sent": sent,
            "failed": failed,
            "no_chat": no_chat,
            "async": False,
        }

    tg.send_to_students_async(students, text, kind, month)
    return {
        "queued": linked,
        "no_chat": len(students) - linked,
        "async": True,
    }


@csrf_exempt
def send_message_student(request):
    """Bitta o'quvchiga xabar. Body: {student_id, text, month?}"""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        student_id = data.get("student_id")
        text = (data.get("text") or "").strip()
        if not student_id or not text:
            return JsonResponse({"error": "student_id va text majburiy"}, status=400)
        student = Student.objects.filter(id=student_id).first()
        if not student:
            return JsonResponse({"error": "O'quvchi topilmadi"}, status=404)
        result = _do_send([student], text, "single", data.get("month", ""))
        log_action(
            request,
            "message.send",
            f"{student}ga telegram xabar: {text[:80]}",
            target_type="student",
            target_id=student.id,
            target_name=str(student),
            kind="single",
        )
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def send_message_group(request):
    """Guruhdagi barcha o'quvchilarga xabar. Body: {group_id, text, month?}"""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        group_id = data.get("group_id")
        text = (data.get("text") or "").strip()
        if not group_id or not text:
            return JsonResponse({"error": "group_id va text majburiy"}, status=400)
        group = Group.objects.filter(id=group_id).first()
        if not group:
            return JsonResponse({"error": "Guruh topilmadi"}, status=404)
        students = group.students.filter(is_admin=False, is_excellence=False)
        result = _do_send(students, text, "group", data.get("month", ""))
        result["group"] = group.name
        result["total"] = students.count()
        log_action(
            request,
            "message.send",
            f"«{group.name}» guruhiga ({result['total']} o'quvchi) "
            f"telegram xabar: {text[:60]}",
            target_type="group",
            target_id=group.id,
            target_name=group.name,
            kind="group",
            total=result["total"],
        )
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def build_lesson_reminder_text(group, when_date=None):
    """Dars eslatmasi matnini quradi."""
    if when_date is None:
        when_date = tashkent_today()
    weekday = WEEKDAY_NAMES_UZ[when_date.weekday()]
    time_str = group.lesson_time.strftime("%H:%M") if group.lesson_time else ""
    room = (group.room or "").strip()
    room_part = f"{room}-xonada " if room else ""
    return (
        f"⏰ Eslatma!\n\n"
        f"{weekday} kuni soat {time_str} da {room_part}"
        f"«{group.name}» darsingiz boshlanadi.\n\n"
        f"Iltimos, darsga kechikmang! 📚"
    )


@csrf_exempt
def send_lesson_reminders(request):
    """Dars boshlanishidan oldin guruh o'quvchilariga telegram eslatma.

    Board (jadval ekrani) darsga ~5 daqiqa qolganda chaqiradi.
    Body: {group_id}. Bir guruhga bir kunda faqat bir marta yuboriladi
    (LessonReminderLog orqali) — board bir necha marta chaqirsa ham takror
    bo'lmaydi.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body or "{}")
        group_id = data.get("group_id")
        if not group_id:
            return JsonResponse({"error": "group_id majburiy"}, status=400)

        group = Group.objects.filter(id=group_id).prefetch_related("students").first()
        if not group:
            return JsonResponse({"error": "Guruh topilmadi"}, status=404)

        today = tashkent_today()

        # Xavfsizlik: guruhда bugun dars bo'lmasa yubormaymiz.
        # "Har kuni" (daily) guruhlar har kuni dars qiladi.
        today_schedule = get_schedule_for_day(today.weekday())
        if group.schedule not in (today_schedule, "daily"):
            return JsonResponse(
                {"skipped": True, "reason": "Bugun bu guruhda dars yo'q"}
            )

        # Idempotentlik: log yozuvini avval yaratamiz (takrorning oldini oladi)
        log, created = LessonReminderLog.objects.get_or_create(group=group, date=today)
        if not created:
            return JsonResponse(
                {"already": True, "sent": log.sent, "no_chat": log.no_chat}
            )

        students = group.students.filter(is_admin=False, is_excellence=False)
        text = build_lesson_reminder_text(group, today)
        result = _do_send(students, text, "group")

        log.sent = result.get("sent", result.get("queued", 0)) or 0
        log.no_chat = result.get("no_chat", 0) or 0
        log.save(update_fields=["sent", "no_chat"])

        result["group"] = group.name
        result["reminded"] = True
        return JsonResponse(result)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def send_message_all(request):
    """Barcha faol o'quvchilarga xabar. Body: {text, month?}"""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        text = (data.get("text") or "").strip()
        if not text:
            return JsonResponse({"error": "text majburiy"}, status=400)
        students = Student.objects.filter(
            is_admin=False, is_excellence=False, is_graduate=False
        )
        result = _do_send(students, text, "all", data.get("month", ""))
        result["total"] = students.count()
        log_action(
            request,
            "message.send",
            f"Barcha o'quvchilarga ({result['total']} ta) telegram xabar: "
            f"{text[:60]}",
            target_type="broadcast",
            target_name="Barcha o'quvchilar",
            kind="all",
            total=result["total"],
        )
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def send_message_students(request):
    """Berilgan o'quvchilar ro'yxatiga xabar (masalan to'lov qilmaganlar).

    Body: {student_ids: [...], text, month?}
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        text = (data.get("text") or "").strip()
        ids = data.get("student_ids") or []
        if not text:
            return JsonResponse({"error": "text majburiy"}, status=400)
        if not isinstance(ids, list) or not ids:
            return JsonResponse(
                {"error": "student_ids ro'yxati kiritilishi kerak"}, status=400
            )
        try:
            ids = [int(x) for x in ids]
        except (ValueError, TypeError):
            return JsonResponse(
                {"error": "student_ids ichида son bo'lishi kerak"}, status=400
            )
        students = Student.objects.filter(
            id__in=ids, is_admin=False, is_excellence=False
        )
        result = _do_send(students, text, "group", data.get("month", ""))
        result["total"] = students.count()
        log_action(
            request,
            "message.send",
            f"Tanlangan {result['total']} o'quvchiga telegram xabar: " f"{text[:60]}",
            target_type="broadcast",
            target_name="Tanlangan o'quvchilar",
            kind="selected",
            total=result["total"],
        )
        return JsonResponse(result)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_message_history(request):
    """Yuborilgan xabarlar tarixi (oxirgi 200 ta)."""
    try:
        from .models import SentMessage

        qs = SentMessage.objects.select_related("student")[:200]
        data = [
            {
                "id": m.id,
                "student": (
                    f"{m.student.name} {m.student.surname}".strip() if m.student else ""
                ),
                "kind": m.kind,
                "text": m.text[:120],
                "status": m.status,
                "error": m.error,
                "created_at": m.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for m in qs
        ]
        return JsonResponse({"messages": data})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# ─────────────────────────────
# STUDENT O'CHIRISH + PING
# ─────────────────────────────


@csrf_exempt
def delete_student(request, student_id):
    """O'quvchini butunlay o'chiradi (to'lovlari/davomatlari bilan birga)."""
    if request.method not in ("POST", "DELETE"):
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        student = Student.objects.filter(id=student_id).first()
        if not student:
            return JsonResponse({"error": "O'quvchi topilmadi"}, status=404)
        name = f"{student.name} {student.surname}".strip()
        teacher_name = student.teacher.name if student.teacher else ""
        # Raqamlarni o'chirishdan OLDIN olamiz — keyin obyektdan o'qib
        # bo'lmaydi
        phones = [student.phone, student.phone2]
        student.delete()
        # O'quvchi qatori ketdi, lekin bot ulanishi, tasdiqlash kodi va
        # qurilma yozuvlari raqam bilan qolib ketardi (FK'lar SET_NULL) —
        # menejer "o'chirdim" desa ham raqam bazada yashirin turardi
        forget_student_phones([student_id], phones)
        log_action(
            request,
            "student.delete",
            f"{name} butunlay o'chirildi"
            + (f" (ustoz: {teacher_name})" if teacher_name else ""),
            target_type="student",
            target_id=student_id,
            target_name=name,
        )
        return JsonResponse({"success": True, "deleted": name})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def bulk_delete_students(request):
    """Bir yoki bir nechta o'quvchini o'chiradi (menejer paneli).

    Body: {student_ids: [id, id, ...]}. Faqat admin yoki menejer.
    Har bir o'quvchi to'lovlari, davomati, coinlari bilan birga o'chadi.
    """
    if request.method not in ("POST", "DELETE"):
        return JsonResponse({"error": "Method not allowed"}, status=405)

    denied = _require_manager_or_admin(request)
    if denied:
        return denied

    try:
        data = json.loads(request.body or "{}")
        ids = data.get("student_ids") or data.get("ids") or []
        if not isinstance(ids, list) or not ids:
            return JsonResponse(
                {"error": "student_ids ro'yxati kiritilishi kerak"}, status=400
            )
        try:
            ids = [int(x) for x in ids]
        except (ValueError, TypeError):
            return JsonResponse(
                {"error": "student_ids ichida faqat son bo'lishi kerak"}, status=400
            )

        qs = Student.objects.filter(id__in=ids)
        deleted = qs.count()
        if not deleted:
            return JsonResponse({"error": "O'quvchi topilmadi"}, status=404)
        phones = [p for s in qs for p in (s.phone, s.phone2)]
        qs.delete()
        forget_student_phones(ids, phones)
        return JsonResponse({"success": True, "deleted": deleted})
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def ping(request):
    """Server uyg'oqligini tekshirish / uyg'otish uchun engil endpoint."""
    return JsonResponse({"ok": True})


@csrf_exempt
def presence_ping(request):
    """Foydalanuvchi hali saytda — qurilma vaqtini yangilaydi.

    Supermenejer "kim onlayn" ro'yxatini shu signal asosida ko'radi.
    Kirmagan yoki qurilmasi bloklangan bo'lsa hech narsa yozilmaydi.
    """
    return JsonResponse({"ok": touch_presence(request)})


# ─────────────────────────────
# TELEFON TASDIQLASH (bot orqali kod)
# ─────────────────────────────

CODE_TTL_MINUTES = 10
MAX_CODE_ATTEMPTS = 5


@csrf_exempt
def send_verification_code(request):
    """Telefon raqamga bot orqali tasdiqlash kodi yuboradi.

    Body: {phone}
    Raqam botga ulanmagan bo'lsa — nima qilish kerakligi qaytariladi.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        import random

        from .models import PhoneVerification, TelegramSubscriber
        from . import telegram as tg

        data = json.loads(request.body)
        phone = (data.get("phone") or "").strip()
        target = _digits9(phone)
        if not target:
            return JsonResponse(
                {"error": "Telefon raqam to'liq kiritilmagan"}, status=400
            )

        if _find_student_by_any_phone(phone):
            return JsonResponse(
                {"error": "Bu raqam allaqachon ro'yxatda bor"}, status=400
            )

        sub = TelegramSubscriber.objects.filter(phone=target).first()
        if not sub:
            bot = settings.TG_BOT_USERNAME
            return JsonResponse(
                {
                    "sent": False,
                    "not_linked": True,
                    "bot_username": bot,
                    "error": (
                        "Bu raqam botga ulanmagan. O'quvchi avval "
                        f"@{bot} ga kirib /start bosib, "
                        "telefon raqamini yuborishi kerak."
                    ),
                },
                status=404,
            )

        code = f"{random.randint(0, 999999):06d}"
        PhoneVerification.objects.create(phone=target, code=code, chat_id=sub.chat_id)
        try:
            tg.send_text(sub.chat_id, tg.CODE_TEXT.format(code=code))
        except Exception as e:
            return JsonResponse(
                {"sent": False, "error": f"Telegramga yuborib bo'lmadi: {e}"},
                status=502,
            )

        return JsonResponse({"sent": True, "expires_in": CODE_TTL_MINUTES * 60})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def check_verification_code(request):
    """Kodni tekshiradi. Body: {phone, code}"""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        from datetime import timedelta

        from .models import PhoneVerification

        data = json.loads(request.body)
        target = _digits9(data.get("phone") or "")
        code = (data.get("code") or "").strip()
        if not target or not code:
            return JsonResponse({"error": "phone va code majburiy"}, status=400)

        deadline = timezone.now() - timedelta(minutes=CODE_TTL_MINUTES)
        pv = (
            PhoneVerification.objects.filter(
                phone=target, used_at__isnull=True, created_at__gte=deadline
            )
            .order_by("-created_at")
            .first()
        )
        if not pv:
            return JsonResponse(
                {"verified": False, "error": "Kod topilmadi yoki muddati o'tgan"},
                status=400,
            )
        if pv.attempts >= MAX_CODE_ATTEMPTS:
            return JsonResponse(
                {"verified": False, "error": "Urinishlar tugadi, yangi kod so'rang"},
                status=429,
            )

        pv.attempts += 1
        if pv.code != code:
            pv.save(update_fields=["attempts"])
            qolgan = MAX_CODE_ATTEMPTS - pv.attempts
            return JsonResponse(
                {"verified": False, "error": f"Kod noto'g'ri ({qolgan} urinish qoldi)"},
                status=400,
            )

        pv.verified_at = timezone.now()
        pv.save(update_fields=["attempts", "verified_at"])
        return JsonResponse({"verified": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# ─────────────────────────────
# PAROL O'ZGARTIRISH
# ─────────────────────────────

MIN_PASSWORD_LEN = 6


def _password_matches(obj, password):
    """Yozuvning paroli mos keladimi (parol o'rnatilmagan bo'lsa ism-familiya)."""
    if getattr(obj, "password", ""):
        return check_password(password, obj.password)
    return _name_password_matches(obj, password)


@csrf_exempt
def change_password(request):
    """Parolni o'zgartiradi. Body: {phone, old_password, new_password}

    Ustoz/adminda ikkita yozuv bor (Teacher + Student.is_admin) — ikkalasi
    ham yangilanadi, aks holda login qaysi yozuvga tushishiga qarab eski
    yoki yangi parol talab qilinardi.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        phone = (data.get("phone") or "").strip()
        old = data.get("old_password") or ""
        new = (data.get("new_password") or "").strip()

        if not phone or not old or not new:
            return JsonResponse(
                {"error": "phone, old_password va new_password majburiy"},
                status=400,
            )
        if len(new) < MIN_PASSWORD_LEN:
            return JsonResponse(
                {
                    "error": (
                        f"Yangi parol kamida {MIN_PASSWORD_LEN} ta belgidan "
                        "iborat bo'lishi kerak"
                    )
                },
                status=400,
            )
        if new in (ADMIN_PASSWORD, EXCELLENCE_PASSWORD):
            return JsonResponse(
                {"error": "Bu parolni tanlab bo'lmaydi — u tizim uchun band"},
                status=400,
            )

        # Eski parolga mos keladigan yozuvni topamiz
        matched_student = None
        for cand in _find_students_by_any_phone(phone):
            if _password_matches(cand, old):
                matched_student = cand
                break

        teacher = _find_teacher_by_any_phone(phone)
        matched_teacher = (
            teacher if teacher and _password_matches(teacher, old) else None
        )

        # ✅ Menejer ham parolini o'zgartira olsin — u faqat Manager
        # jadvalida (Student/Teacher emas), shuning uchun alohida tekshiramiz
        manager = _find_manager_by_any_phone(phone)
        matched_manager = (
            manager if manager and _password_matches(manager, old) else None
        )

        if not matched_student and not matched_teacher and not matched_manager:
            return JsonResponse(
                {"error": "Telefon yoki joriy parol noto'g'ri"}, status=401
            )

        hashed = make_password(new)
        if matched_student:
            matched_student.password = hashed
            matched_student.save(update_fields=["password"])
            # Admin/menejer bo'lsa bog'langan Teacher yozuvi ham yangilanadi
            if matched_student.teacher_id:
                Teacher.objects.filter(id=matched_student.teacher_id).update(
                    password=hashed
                )
        if matched_teacher:
            matched_teacher.password = hashed
            matched_teacher.save(update_fields=["password"])
            # Teacher'ga bog'langan admin profillari ham
            Student.objects.filter(teacher_id=matched_teacher.id, is_admin=True).update(
                password=hashed
            )
        if matched_manager:
            matched_manager.password = hashed
            matched_manager.save(update_fields=["password"])

        return JsonResponse({"message": "Parol muvaffaqiyatli o'zgartirildi"})
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def update_profile(request):
    """Profil ma'lumotlarini yangilaydi. Body: {phone, name, surname}

    Ustoz/adminda ikkita yozuv bor (Teacher + Student.is_admin) — ikkalasida
    ham ism birga yangilanadi.
    """
    if request.method not in ("POST", "PATCH"):
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = json.loads(request.body)
        phone = (data.get("phone") or "").strip()
        name = (data.get("name") or "").strip()
        surname = (data.get("surname") or "").strip()

        if not phone:
            return JsonResponse({"error": "phone majburiy"}, status=400)
        if not name:
            return JsonResponse({"error": "Ism bo'sh bo'lishi mumkin emas"}, status=400)

        students = _find_students_by_any_phone(phone)
        teacher = _find_teacher_by_any_phone(phone)

        # Menejer faqat Manager jadvalida — Student/Teacher qidiruvi uni
        # topmaydi va u profilini umuman tahrirlay olmasdi
        manager = _find_manager_by_any_phone(phone)
        if manager and not students and not teacher:
            manager.name = name[:100]
            manager.surname = surname[:100]
            manager.save(update_fields=["name", "surname"])
            return JsonResponse(
                {
                    "message": "Profil yangilandi",
                    "name": manager.name,
                    "surname": manager.surname,
                }
            )

        if not students and not teacher:
            return JsonResponse({"error": "Foydalanuvchi topilmadi"}, status=404)

        # Bitta raqamda bir nechta o'quvchi bo'lishi mumkin (aka-uka) —
        # faqat so'rov yuborgan profilni yangilaymiz
        student = None
        user_id = data.get("id")
        if user_id:
            student = next((s for s in students if s.id == int(user_id)), None)
        if student is None:
            student = next((s for s in students if s.is_admin or s.is_excellence), None)
        if student is None and students:
            student = students[0]

        full_name = f"{name} {surname}".strip()

        if student:
            student.name = name[:100]
            student.surname = surname[:100]
            student.save(update_fields=["name", "surname"])
            if student.teacher_id:
                Teacher.objects.filter(id=student.teacher_id).update(
                    name=full_name[:100]
                )
        elif teacher:
            teacher.name = full_name[:100]
            teacher.save(update_fields=["name"])

        return JsonResponse(
            {
                "message": "Profil yangilandi",
                "name": name,
                "surname": surname,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
