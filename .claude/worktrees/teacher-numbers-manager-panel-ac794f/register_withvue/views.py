import json
from datetime import datetime

from django.db import transaction
from django.db.models import Sum, F
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.hashers import make_password, check_password
from django.db import models as db_models
from rest_framework import serializers


from .models import (
    Group, Student, Teacher, Lesson, Attendance, Payment, StagePrice,
    StudentPenalty, Manager, CoinTransaction, Product, Order,
    AttendanceCoinSettings, Course, News, Expense, Lead, AdChannel,
)

from django.utils import timezone
from rest_framework import generics, permissions
from .serializers import NewsSerializer

ADMIN_PASSWORD = "excel2024"
EXCELLENCE_PASSWORD = "excellence2024"

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


# ─────────────────────────────────────────
# MANAGER (eng yuqori daraja)
# ─────────────────────────────────────────


@csrf_exempt
def manager_register(request):
    """Yangi menejer yaratish."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
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
        )
        return JsonResponse(
            {
                "id": manager.id,
                "name": manager.name,
                "surname": manager.surname,
                "phone": manager.phone,
                "role": "manager",
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
            return JsonResponse(
                {"error": "Telefon kiritilishi shart"}, status=400
            )

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

        return JsonResponse(
            {
                "id": manager.id,
                "name": manager.name,
                "surname": manager.surname,
                "phone": manager.phone,
                "role": "manager",
                "is_active": manager.is_active,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


def get_managers(request):
    """Menejerlar ro'yxati. ?all=1 — o'chirilganlari (is_active=False) ham."""
    try:
        qs = Manager.objects.all()
        if request.GET.get("all") not in ("1", "true", "yes"):
            qs = qs.filter(is_active=True)
        managers = list(
            qs.order_by("name", "surname").values(
                "id", "name", "surname", "phone", "is_active", "created_at"
            )
        )
        return JsonResponse(managers, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def update_manager(request, manager_id):
    """Menejer ma'lumotlarini yangilash."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = _require_staff(request)
    if denied:
        return denied
    try:
        data = json.loads(request.body)
        manager = Manager.objects.filter(id=manager_id).first()
        if not manager:
            return JsonResponse({"error": "Menejer topilmadi"}, status=404)

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
    denied = _require_staff(request)
    if denied:
        return denied
    try:
        manager = Manager.objects.filter(id=manager_id).first()
        if not manager:
            return JsonResponse({"error": "Menejer topilmadi"}, status=404)
        manager.is_active = False
        manager.save()
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

        teacher = Teacher.objects.create(
            name=name,
            phone=phone,
            password=make_password(ADMIN_PASSWORD),
            is_senior=data.get("is_senior", False),
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
                    {"error": "O'quvchilarni o'sha ustozning o'ziga o'tkazib bo'lmaydi"},
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
                        else "Telefon raqam yo'q — tizimga kira olmaydi"
                        if len(key) < MIN_PHONE_KEY_LEN
                        else f"Raqam to'liq emas ({len(key)} xonali) — tekshiring"
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

        data = [
            {
                "id": s.id,
                "name": s.name,
                "surname": s.surname,
                # Import paytida raqami band bo'lgan o'quvchilarga '—0001'
                # kabi shartli kod berilgan — uni ko'rsatmaymiz
                "phone": "" if s.phone.startswith("—") else s.phone,
                "phone2": s.phone2,
                "teacher_id": s.teacher_id,
                "teacher_name": s.teacher.name if s.teacher else "",
                "stage": s.stage,
                "schedule": s.schedule,
                "is_graduate": s.is_graduate,
                "coin_balance": s.coin_balance,
            }
            for s in rows
        ]
        return JsonResponse({"count": len(data), "students": data})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


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
            return JsonResponse({"error": "to_teacher_id kiritilishi shart"}, status=400)

        to_teacher = Teacher.objects.filter(id=to_teacher_id).first()
        if not to_teacher:
            return JsonResponse({"error": "Yangi o'qituvchi topilmadi"}, status=404)

        try:
            ids = [int(i) for i in ids]
        except (ValueError, TypeError):
            return JsonResponse({"error": "student_ids butun son bo'lishi kerak"}, status=400)

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
            moved = _real_students().filter(id__in=found).update(
                teacher_id=to_teacher.id, manual_teacher=True
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
        sp.price = price
        sp.save()
        return JsonResponse({"stage": stage, "price": sp.price})
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

        data = [
            {
                "id": s.id,
                "name": s.name,
                "surname": s.surname,
                "phone": "" if s.phone.startswith("—") else s.phone,
                "phone2": s.phone2,
                "teacher_id": s.teacher_id,
                "teacher_name": s.teacher.name if s.teacher else "Biriktirilmagan",
                "stage": s.stage,
                "schedule": s.schedule,
                "coin_balance": s.coin_balance,
            }
            for s in qs
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
            if data["schedule"] not in ["odd", "even"]:
                return JsonResponse(
                    {"error": "schedule 'odd' yoki 'even' bo'lishi kerak"}, status=400
                )
            student.schedule = data["schedule"]

        student.save()
        return JsonResponse(
            {
                "message": "O'quvchi yangilandi!",
                "stage": student.stage,
                "schedule": student.schedule,
                "teacher_id": student.teacher_id,
                "teacher_name": student.teacher.name if student.teacher else "",
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
    qs = Manager.objects.filter(is_active=True) if active_only else Manager.objects.all()
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


# Jadvalda ismlar turk/nemis harflari bilan kelgan ('Möydınov',
# 'Damırov') — egasi ularni klaviaturada tera olmaydi. NFKD ajratmaydigan
# harflarni qo'lda moslashtiramiz, qolganini NFKD hal qiladi.
_LOOKALIKE = str.maketrans(
    {
        "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
        "ç": "c", "Ç": "c", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
        "ə": "a", "æ": "a", "ø": "o", "ß": "s",
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
    return "".join(
        ch for ch in s if ch.isalnum() and not unicodedata.combining(ch)
    )


def _name_password_matches(student, password):
    """Import qilingan studentlar uchun parol — ism va familiya.

    'Abdulloh Ibrohimov', 'abdullohibrohimov', 'Ibrohimov Abdulloh' —
    hammasi to'g'ri. Jadvaldan ismga qo'shilib kelgan bir-ikki harfli
    qoldiqlar ("Abdurovuf Möydınov y" dagi 'y') ham talab qilinmaydi.
    """
    typed = _fold_name(password)
    if not typed:
        return False

    tokens = [
        t
        for t in (_fold_name(p) for p in f"{student.name} {student.surname}".split())
        if t
    ]
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

        if _find_student_by_any_phone(phone):
            return JsonResponse(
                {
                    "error": (
                        "Bu telefon raqam allaqachon ro'yxatda bor. "
                        "Kirish uchun parol sifatida ism va familiyangizni yozing."
                    )
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

        # Oddiy o'quvchi uchun telefon bot orqali tasdiqlangan bo'lishi shart.
        # Admin/excellence qo'shishda tasdiqlash talab qilinmaydi.
        if not is_admin and not is_excellence:
            from datetime import timedelta

            from .models import PhoneVerification

            deadline = timezone.now() - timedelta(minutes=30)
            pv = (
                PhoneVerification.objects.filter(
                    phone=_digits9(phone),
                    verified_at__isnull=False,
                    used_at__isnull=True,
                    verified_at__gte=deadline,
                )
                .order_by("-verified_at")
                .first()
            )
            if not pv:
                return JsonResponse(
                    {
                        "error": (
                            "Telefon raqam tasdiqlanmagan. "
                            "Avval bot orqali kod yuborib tasdiqlang."
                        ),
                        "need_verification": True,
                    },
                    status=403,
                )
            pv.used_at = timezone.now()
            pv.save(update_fields=["used_at"])

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
        if teacher and teacher.password and check_password(password, teacher.password):
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
                students_qs = students_qs.filter(schedule=schedule_for_day)

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
        return JsonResponse({"present": s.present, "late": s.late, "absent": s.absent})
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

        s.save()
        return JsonResponse(
            {
                "message": "Sozlamalar yangilandi!",
                "present": s.present,
                "late": s.late,
                "absent": s.absent,
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

        payments = Payment.objects.filter(student_id=student_id).order_by("-month")
        data = [
            {
                "id": p.id,
                "month": p.month,
                "stage": p.stage,
                "amount_due": p.amount_due,
                "paid_amount": p.paid_amount,
                "is_paid": p.is_paid,
                "paid_at": p.paid_at.strftime("%Y-%m-%d") if p.paid_at else None,
            }
            for p in payments
        ]
        return JsonResponse(data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_all_payments(request):
    """Barcha to'lovlar."""
    try:
        month = request.GET.get("month", "").strip()
        teacher_id = request.GET.get("teacher_id", "").strip()
        qs = Payment.objects.select_related("student", "student__teacher").order_by(
            "-month", "student__name"
        )

        if month:
            qs = qs.filter(month=month)
        if teacher_id:
            try:
                qs = qs.filter(student__teacher_id=int(teacher_id))
            except ValueError:
                return JsonResponse({"error": "Invalid teacher_id"}, status=400)

        data = [
            {
                "id": p.id,
                "student_id": p.student.id,
                "student_name": f"{p.student.name} {p.student.surname}",
                "student_phone": p.student.phone,
                "teacher_name": p.student.teacher.name if p.student.teacher else "",
                "month": p.month,
                "stage": p.stage,
                "amount_due": p.amount_due,
                "paid_amount": p.paid_amount,
                "is_paid": p.is_paid,
                "paid_at": str(p.paid_at) if p.paid_at else None,
            }
            for p in qs
        ]
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
            year, mon = month.split("-")
            int(year), int(mon)
        except ValueError:
            return JsonResponse(
                {"error": "month format 'YYYY-MM' bo'lishi kerak"}, status=400
            )

        students = Student.objects.filter(is_admin=False, is_excellence=False)
        created_count = 0
        skipped_count = 0

        for student in students:
            price = get_stage_price(student.stage)
            _, created = Payment.objects.get_or_create(
                student=student,
                month=month,
                defaults={"stage": student.stage, "amount_due": price},
            )
            if created:
                created_count += 1
            else:
                skipped_count += 1

        return JsonResponse(
            {
                "message": f"{created_count} ta yangi to'lov yaratildi, {skipped_count} ta allaqachon mavjud edi.",
                "month": month,
                "created": created_count,
                "skipped": skipped_count,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


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

        payment.is_paid = data.get("is_paid", payment.is_paid)
        payment.paid_at = datetime.now() if payment.is_paid else None
        payment.save()

        return JsonResponse(
            {
                "message": "To'lov yangilandi!",
                "is_paid": payment.is_paid,
                "amount_due": payment.amount_due,
                "paid_amount": payment.paid_amount,  # ✅ QO'SHILDI
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

        if "is_paid" in data:
            payment.is_paid = bool(data["is_paid"])
            payment.paid_at = datetime.now() if payment.is_paid else None

        payment.save()
        return JsonResponse(
            {
                "message": "Summa yangilandi!",
                "amount_due": payment.amount_due,
                "paid_amount": payment.paid_amount,  # ✅ QO'SHILDI
                "is_paid": payment.is_paid,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


def get_groups(request):
    """Barcha guruhlar."""
    try:
        groups = Group.objects.select_related("teacher", "course").prefetch_related(
            "students"
        )
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
        serializer = GroupSerializer(group)
        return JsonResponse(serializer.data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def create_group(request):
    """Yangi guruh yaratish."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
        name = data.get("name", "").strip()

        if not name:
            return JsonResponse({"error": "Guruh nomi kiritilishi shart"}, status=400)

        teacher = None
        if data.get("teacher_id"):
            try:
                teacher = Teacher.objects.filter(id=int(data.get("teacher_id"))).first()
            except ValueError:
                pass

        course = None
        if data.get("course_id"):
            try:
                course = Course.objects.filter(id=int(data.get("course_id"))).first()
            except ValueError:
                pass

        lesson_time = data.get("lesson_time", "09:00").strip()
        schedule = data.get("schedule", "odd").strip()

        if schedule not in ["odd", "even"]:
            return JsonResponse(
                {"error": "schedule 'odd' yoki 'even' bo'lishi kerak"}, status=400
            )

        group = Group.objects.create(
            name=name,
            teacher=teacher,
            course=course,
            lesson_time=lesson_time,
            room=data.get("room", "").strip(),
            schedule=schedule,
        )

        # ✅ TO'G'RI - ManyToMany
        student_ids = data.get("students", [])
        if student_ids:
            try:
                student_ids = [int(sid) for sid in student_ids]
                group.students.set(student_ids)
            except (ValueError, TypeError):
                pass

        serializer = GroupSerializer(group)
        return JsonResponse(serializer.data, status=201)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


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
            if schedule in ["odd", "even"]:
                group.schedule = schedule

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

        group.delete()
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

        defaults = {
            "exam_pass": EXAM_PASS_COINS,
            "homework_done": HOMEWORK_DONE_COINS,
            "homework_partial": HOMEWORK_PARTIAL_COINS,
            "homework_missed": HOMEWORK_MISSED_COINS,
        }

        if amount is None:
            amount = defaults.get(reason)

        if amount is None:
            return JsonResponse({"error": "amount kiritilmadi"}, status=400)

        try:
            amount = int(amount)
        except (ValueError, TypeError):
            return JsonResponse({"error": "amount son bo'lishi kerak"}, status=400)

        # Oylik manual bonus cheklovi
        if reason == "manual":
            now = datetime.now()
            already_used = CoinTransaction.objects.filter(
                student=student,
                reason="manual",
                given_by=teacher,
                created_at__year=now.year,
                created_at__month=now.month,
            ).exists()
            if already_used:
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

        return JsonResponse(
            {
                "message": "Coin berildi!",
                "student_id": student.id,
                "coin_balance": new_balance,
            },
            status=201,
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


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
        return JsonResponse(
            {"id": product.id, "message": "Mahsulot qo'shildi!"}, status=201
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

        course.delete()
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

        news.delete()
        return JsonResponse({"message": "Yangilik o'chirildi!"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

# ─────────────────────────────
# EXPENSES (XARAJATLAR)
# ─────────────────────────────


def get_expenses(request):
    """Barcha xarajatlar (ixtiyoriy: oy bo'yicha filter)."""
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
            return JsonResponse({"error": "amount 0 dan katta bo'lishi kerak"}, status=400)

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
    try:
        try:
            expense_id = int(expense_id)
        except ValueError:
            return JsonResponse({"error": "Invalid expense_id"}, status=400)

        expense = Expense.objects.filter(id=expense_id).first()
        if not expense:
            return JsonResponse({"error": "Xarajat topilmadi"}, status=404)
        expense.delete()
        return JsonResponse({"message": "Xarajat o'chirildi!"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


def get_finance_summary(request):
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

        generated_student_ids = set(
            month_payments.values_list("student_id", flat=True)
        )

        # Payment yaratilgan studentlar uchun ularning haqiqiy amount_due qiymati ishlatiladi
        expected_from_generated = (
            month_payments.aggregate(total=Sum("amount_due"))["total"] or 0
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

        expected_total = (
            month_payments.aggregate(total=Sum("amount_due"))["total"] or 0
        )
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
        qs = Student.objects.select_related("teacher").filter(
            is_graduate=True
        ).order_by("name")
        data = [
            {
                "id": s.id,
                "name": s.name,
                "surname": s.surname,
                "phone": s.phone if not s.phone.startswith("—") else "",
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
            {"count": len(student_ids), "student_ids": student_ids}
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
            return JsonResponse(
                {"error": "student_id va text majburiy"}, status=400
            )
        student = Student.objects.filter(id=student_id).first()
        if not student:
            return JsonResponse({"error": "O'quvchi topilmadi"}, status=404)
        result = _do_send([student], text, "single", data.get("month", ""))
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
        return JsonResponse(result)
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
        return JsonResponse(result)
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
                    f"{m.student.name} {m.student.surname}".strip()
                    if m.student
                    else ""
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
        student.delete()
        return JsonResponse({"success": True, "deleted": name})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def ping(request):
    """Server uyg'oqligini tekshirish / uyg'otish uchun engil endpoint."""
    return JsonResponse({"ok": True})


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
            return JsonResponse(
                {
                    "sent": False,
                    "not_linked": True,
                    "bot_username": "itline_test_2026bot",
                    "error": (
                        "Bu raqam botga ulanmagan. O'quvchi avval "
                        "@itline_test_2026bot ga kirib /start bosib, "
                        "telefon raqamini yuborishi kerak."
                    ),
                },
                status=404,
            )

        code = f"{random.randint(0, 999999):06d}"
        PhoneVerification.objects.create(
            phone=target, code=code, chat_id=sub.chat_id
        )
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

        if not matched_student and not matched_teacher:
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
            Student.objects.filter(
                teacher_id=matched_teacher.id, is_admin=True
            ).update(password=hashed)

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
