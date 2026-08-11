from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# ─────────────────────────────────────────
# MANAGER
# ─────────────────────────────────────────


class Manager(models.Model):
    name = models.CharField(max_length=100, verbose_name="Ism")
    surname = models.CharField(max_length=100, blank=True, verbose_name="Familiya")
    phone = models.CharField(max_length=20, unique=True, verbose_name="Telefon")
    password = models.CharField(max_length=255, verbose_name="Parol (hash)")
    is_active = models.BooleanField(default=True, verbose_name="Faol")

    # Supermenejer — menejerlarni yaratadi, ularning vakolatlarini
    # belgilaydi, moliya va ustoz oyliklarini boshqaradi. Oddiy menejer
    # bu bo'limlarni umuman ko'rmaydi.
    is_super = models.BooleanField(default=False, verbose_name="Supermenejer")

    # Menejerga berilgan vakolatlar — kalitlar ro'yxati (masalan
    # ["payments.view", "groups.edit"]). Katalog views.PERMISSIONS'da.
    # Supermenejer uchun bu maydon e'tiborga olinmaydi — unda hammasi bor.
    permissions = models.JSONField(
        default=list, blank=True, verbose_name="Vakolatlar"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Menejer"
        verbose_name_plural = "Menejerlar"

    def __str__(self):
        return f"{self.name} {self.surname}"

    def has_perm(self, key):
        if self.is_super:
            return True
        return key in (self.permissions or [])


# ─────────────────────────────────────────
# TEACHER
# ─────────────────────────────────────────


class Teacher(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, blank=True, unique=True)
    password = models.CharField(max_length=255, blank=True)
    is_senior = models.BooleanField(default=False)
    penalty_limit = models.IntegerField(default=0)
    source = models.CharField(max_length=30, blank=True, default="")

    # ── Oylik hisoblash ──
    # Ikki usul bor, ustozga qarab tanlanadi:
    #   per_student — stavka × o'quvchilar soni (qat'iy summa)
    #   percent     — o'sha ustozning o'quvchilaridan shu oy yig'ilgan
    #                 pulning foizi
    # Har ikkalasi ham "default" oylik beradi; supermenejer o'sha oy
    # uchun qo'lda boshqa summa kiritsa (TeacherSalary.manual_amount)
    # o'sha ustunlik qiladi.
    SALARY_MODE_CHOICES = [
        ("per_student", "O'quvchi soniga qarab"),
        ("percent", "Yig'ilgan puldan foiz"),
    ]

    salary_mode = models.CharField(
        max_length=12,
        choices=SALARY_MODE_CHOICES,
        default="per_student",
        verbose_name="Oylik hisoblash usuli",
    )
    salary_per_student = models.IntegerField(
        default=0, verbose_name="Bir o'quvchi uchun stavka"
    )
    salary_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Yig'ilgan puldan foiz (%)",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "O'qituvchi"
        verbose_name_plural = "O'qituvchilar"

    def __str__(self):
        return self.name


# ─────────────────────────────────────────
# STUDENT
# ─────────────────────────────────────────


class Student(models.Model):
    SCHEDULE_CHOICES = [
        ("odd", "Du-Chor-Juma"),
        ("even", "Se-Pay-Shan"),
        ("daily", "Har kuni"),
    ]

    name = models.CharField(max_length=100)
    surname = models.CharField(max_length=100)
    # Bitiruvchida raqam saqlanmaydi — undan faqat ism-familiya qoladi.
    # Shuning uchun maydon bo'sh bo'la olishi kerak, lekin `unique` ham
    # saqlanishi shart: bo'sh satr ("") ikkinchi bitiruvchida darhov
    # to'qnashardi, NULL esa unikal indeksda cheklanmaydi.
    phone = models.CharField(
        max_length=20, unique=True, null=True, blank=True
    )
    phone2 = models.CharField(max_length=50, blank=True, verbose_name="Qo'shimcha telefon")
    password = models.CharField(max_length=255, blank=True)

    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
    )

    stage = models.IntegerField(default=1)
    schedule = models.CharField(
        max_length=10,
        choices=SCHEDULE_CHOICES,
        default="odd",
    )
    is_admin = models.BooleanField(default=False)
    is_excellence = models.BooleanField(default=False)
    is_graduate = models.BooleanField(default=False, verbose_name="Bitiruvchi")

    # Ustoz menejer paneli orqali qo'lda almashtirilgan. Sheet qayta
    # import qilinganda import qilingan o'quvchilar o'chirilib qayta
    # yaratiladi — shu bayroq bo'lsa biriktiruv tiklanadi, aks holda
    # menejerning ishi har deployda yo'qolardi
    manual_teacher = models.BooleanField(
        default=False, verbose_name="Ustoz qo'lda biriktirilgan"
    )

    coin_balance = models.IntegerField(default=0, verbose_name="Coin balansi")

    # Doimiy oylik chegirma — har oy to'lov yaratilganda o'sha oyning
    # chegirmasiga (Payment.discount) avtomatik nusxalanadi. Menejer bir
    # marta o'rnatadi, keyin har oyga o'zi qo'llanadi.
    monthly_discount = models.IntegerField(
        default=0, verbose_name="Doimiy oylik chegirma"
    )

    note = models.TextField(blank=True, verbose_name="Izoh")
    source = models.CharField(max_length=30, blank=True, default="")

    # Yuz tanish terminalidagi shaxs raqami (Hikvision'da "employee No").
    # Terminal yuzni tanigach shu raqamni yuboradi — biz shu orqali
    # o'quvchini topamiz. Bo'sh bo'lsa o'quvchi terminalga bog'lanmagan.
    #
    # Raqam takrorlanmasligi shart: ikki o'quvchida bir xil raqam bo'lsa
    # terminal kimni tanigani bilinmay qoladi va davomat noto'g'ri odamga
    # yozilardi. Buni Meta.constraints ta'minlaydi — bo'sh qiymat esa
    # cheklovdan tashqarida (hali bog'lanmaganlar ko'p bo'ladi).
    face_person_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        db_index=True,
        verbose_name="Terminaldagi raqami",
    )

    # ── Botdan kelgan yuz rasmi ──
    # Rasm bazada base64 (JPEG) ko'rinishida saqlanadi. Fayl tizimi
    # emas: Render'ning bepul planida disk deploydan keyin tozalanadi va
    # hamma yuz rasmlari yo'qolib, davomat to'xtab qolardi. Rasm ~30 KB.
    FACE_STATUS_CHOICES = [
        ("none", "Rasm yuborilmagan"),
        ("pending", "Terminalga yozilishi kutilmoqda"),
        ("synced", "Terminalga yozilgan"),
        ("rejected", "Rad etilgan"),
    ]

    face_photo = models.TextField(
        blank=True, default="", verbose_name="Yuz rasmi (base64 JPEG)"
    )
    face_status = models.CharField(
        max_length=10,
        choices=FACE_STATUS_CHOICES,
        default="none",
        db_index=True,
        verbose_name="Yuz holati",
    )
    face_note = models.CharField(
        max_length=255, blank=True, default="", verbose_name="Yuz izohi"
    )
    # Rasm oxirgi marta qachon almashgani. Terminalga yozilgan sanadan
    # keyin bo'lsa — o'quvchi yangi rasm yuborgan, qayta yozish kerak.
    face_updated_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Rasm yangilangan"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "O'quvchi"
        verbose_name_plural = "O'quvchilar"
        constraints = [
            models.UniqueConstraint(
                fields=["face_person_id"],
                condition=~models.Q(face_person_id=""),
                name="uniq_face_person_id",
            )
        ]

    def __str__(self):
        return f"{self.name} {self.surname}"

    @property
    def effective_schedule(self):
        """Agar group bo'lsa, guruh schedule-idan olib, aks xolda student schedule-dan olib."""
        if self.group:
            return self.group.schedule
        return self.schedule


# ─────────────────────────────────────────
# STAGE PRICE
# ─────────────────────────────────────────


class StagePrice(models.Model):
    stage = models.IntegerField(unique=True)
    price = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Etap narxi"
        verbose_name_plural = "Etap narxlari"

    def __str__(self):
        return f"{self.stage}-etap: {self.price}"


# ─────────────────────────────────────────
# LESSON
# ─────────────────────────────────────────


class Lesson(models.Model):
    title = models.CharField(max_length=200)
    teacher = models.ForeignKey(
        Teacher, on_delete=models.SET_NULL, null=True, related_name="lessons"
    )
    # Guruh + sana bo'yicha darsni aniqlash uchun. Soddalashtirilgan davomat
    # oqimida menejer/ustoz guruhni tanlaydi — dars shu guruh va sana uchun
    # avtomatik yaratiladi/topiladi (qo'lda "dars yaratish" bo'lmaydi).
    group = models.ForeignKey(
        "Group",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="lessons",
    )
    date = models.DateField()

    class Meta:
        verbose_name = "Dars"
        verbose_name_plural = "Darslar"

    def __str__(self):
        return f"{self.title} ({self.date})"


# ─────────────────────────────────────────
# ATTENDANCE
# ─────────────────────────────────────────


class Attendance(models.Model):
    STATUS_CHOICES = [
        ("present", "Keldi"),
        ("absent", "Kelmadi"),
        ("late", "Kech keldi"),
    ]

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="attendances"
    )
    lesson = models.ForeignKey(
        Lesson, on_delete=models.CASCADE, related_name="attendances"
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="absent")

    class Meta:
        unique_together = ("student", "lesson")
        verbose_name = "Davomat"
        verbose_name_plural = "Davomatlar"

    def __str__(self):
        return f"{self.student} — {self.lesson} — {self.status}"


# ─────────────────────────────────────────
# STUDENT PENALTY
# ─────────────────────────────────────────


class StudentPenalty(models.Model):
    REASON_CHOICES = [
        ("late", "Kech kelish"),
        ("absent", "Darsga kelmadi"),
        ("behavior", "Xulq-atvor"),
        ("other", "Boshqa"),
    ]
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="penalties",
    )
    given_by = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        related_name="given_penalties",
    )
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default="other")
    description = models.TextField(blank=True)
    amount = models.IntegerField(default=0)
    date = models.DateField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "O'quvchi ja'zosi"
        verbose_name_plural = "O'quvchi ja'zolari"

    def __str__(self):
        return f"{self.student} — {self.get_reason_display()} — {self.amount}"


# ─────────────────────────────────────────
# PAYMENT
# ─────────────────────────────────────────


class Payment(models.Model):
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="payments"
    )
    month = models.CharField(max_length=7)
    stage = models.IntegerField()
    amount_due = models.IntegerField()
    # Shu oyga berilgan chegirma. To'lanishi kerak bo'lgan sof summa =
    # amount_due - discount. Doimiy oylik chegirma (Student.monthly_discount)
    # to'lov yaratilganda shu yerga nusxalanadi, menejer o'zgartira oladi.
    discount = models.IntegerField(default=0, verbose_name="Chegirma")
    is_paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_amount = models.IntegerField(default=0)
    source = models.CharField(max_length=30, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("student", "month")
        verbose_name = "To'lov"
        verbose_name_plural = "To'lovlar"

    def __str__(self):
        return f"{self.student} — {self.month} — {self.amount_due}"


# ─────────────────────────────────────────
# COURSE
# ─────────────────────────────────────────


class PaymentSettings(models.Model):
    """O'quv markazning to'lov qabul qiladigan kartasi (singleton).

    Student to'lov qilishda shu karta raqami va egasining ismini ko'radi.
    """

    card_number = models.CharField(max_length=32, blank=True, verbose_name="Karta raqami")
    card_holder = models.CharField(max_length=100, blank=True, verbose_name="Karta egasi")
    note = models.CharField(max_length=255, blank=True, verbose_name="Izoh")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "To'lov sozlamasi"
        verbose_name_plural = "To'lov sozlamalari"

    def __str__(self):
        return self.card_number or "Karta kiritilmagan"

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ReceiptSettings(models.Model):
    """To'lov cheki matni (singleton).

    Menejer to'lovni tasdiqlaganda o'quvchiga telegram orqali shu
    matn yuboriladi. Matn panelda tahrirlanadi — markaz o'z uslubida
    yozsin, kodga tegish shart bo'lmasin.

    Matndagi {kalit}lar yuborishdan oldin haqiqiy qiymatga
    almashtiriladi (ro'yxat `PLACEHOLDERS` da).
    """

    PLACEHOLDERS = [
        ("{ism}", "O'quvchining ism-familiyasi"),
        ("{oy}", "To'lov oyi (masalan: Iyul 2026)"),
        ("{summa}", "Shu safar to'langan summa"),
        ("{jami}", "Oylik to'lov (chegirmadan keyin)"),
        ("{qolgan}", "Qolgan qarz"),
        ("{sana}", "Bugungi sana"),
        ("{markaz}", "O'quv markaz nomi"),
        ("{guruh}", "O'quvchining guruhi"),
    ]

    # Chek haqiqiy kassa chekiga o'xshab tursin: sarlavha, ajratuvchi
    # chiziqlar, so'ngida "qabul qilindi" muhri. Summalar <code> ichida —
    # Telegram uni bir xil kenglikdagi shrift bilan chizadi, shu sababli
    # raqamlar ustma-ust tik turadi.
    _LINE = "━━━━━━━━━━━━━━━━━━━━"

    DEFAULT_TEMPLATE = (
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

    enabled = models.BooleanField(
        default=True, verbose_name="Chek yuborilsinmi"
    )
    template = models.TextField(
        default=DEFAULT_TEMPLATE, verbose_name="Chek matni"
    )
    center_name = models.CharField(
        max_length=100,
        default="ITLINE o'quv markazi",
        verbose_name="Markaz nomi",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Chek sozlamasi"
        verbose_name_plural = "Chek sozlamalari"

    def __str__(self):
        return "Chek matni" + ("" if self.enabled else " (o'chirilgan)")

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class PaymentRequest(models.Model):
    """Student yuborgan to'lov so'rovi (chek rasmi bilan).

    Student chekni yuklaydi -> pending. Manager qabul qilganda chekdagi
    miqdor va to'langan sanani kiritadi -> tegishli Payment yangilanadi,
    chek rasmi (receipt_b64) o'chiriladi, faqat manager ma'lumoti qoladi.
    """

    STATUS_CHOICES = [
        ("pending", "Kutilmoqda"),
        ("accepted", "Qabul qilindi"),
        ("rejected", "Rad etildi"),
    ]

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="payment_requests"
    )
    # Siqilgan chek rasmi (data URL / base64). Qabul yoki rad etilgach tozalanadi.
    receipt_b64 = models.TextField(blank=True, verbose_name="Chek rasmi (base64)")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")

    # Manager qabul qilganda kiritadigan ma'lumotlar (tarix uchun)
    amount = models.IntegerField(default=0, verbose_name="Miqdor")
    month = models.CharField(max_length=7, blank=True, verbose_name="Oy (YYYY-MM)")
    paid_at = models.DateField(null=True, blank=True, verbose_name="To'langan sana")
    note = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "To'lov so'rovi"
        verbose_name_plural = "To'lov so'rovlari"

    def __str__(self):
        return f"{self.student} — {self.status}"


class Course(models.Model):
    name = models.CharField(max_length=100)
    # ✅ FIX: IntegerField faqat bitta ta'rif
    monthly_fee = models.IntegerField(default=0)
    source = models.CharField(max_length=30, blank=True, default="")

    class Meta:
        verbose_name = "Kurs"
        verbose_name_plural = "Kurslar"

    def __str__(self):
        return self.name


# ─────────────────────────────────────────
# GROUP
# ─────────────────────────────────────────


class Group(models.Model):
    SCHEDULE_CHOICES = [
        ("odd", "Du-Chor-Juma"),
        ("even", "Se-Pay-Shan"),
        ("daily", "Har kuni"),
    ]

    name = models.CharField(max_length=100)
    teacher = models.ForeignKey(
        Teacher, on_delete=models.SET_NULL, null=True, related_name="groups"
    )
    students = models.ManyToManyField(Student, related_name="groups")
    lesson_time = models.TimeField(null=False, blank=False)
    room = models.CharField(max_length=50, blank=True, default="", verbose_name="Xona")

    # Guruh ochilgan (birinchi dars boshlangan) sana. Oylik to'lov shu
    # kundan boshlab hisoblanadi — masalan 25-kuni ochilgan guruhning
    # to'lov muddati har oyning 25-kuni bo'ladi (1-kuni emas).
    opened_date = models.DateField(
        null=True, blank=True, verbose_name="Guruh ochilgan sana"
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="groups",
        verbose_name=_("Course"),
    )

    schedule = models.CharField(
        max_length=10,
        choices=SCHEDULE_CHOICES,
        default="odd",
    )
    source = models.CharField(max_length=30, blank=True, default="")

    # Import vaqtida jadval sarlavhasida dars kuni yoki soati ko'rsatilmagan
    # bo'lsa true — qiymat taxminiy, menejer tekshirib qo'yishi kerak
    needs_review = models.BooleanField(
        default=False, verbose_name="Tekshirish kerak"
    )
    review_note = models.CharField(
        max_length=200, blank=True, verbose_name="Nima aniq emas"
    )

    class Meta:
        verbose_name = "Guruh"
        verbose_name_plural = "Guruhlar"

    def __str__(self):
        return self.name


# ─────────────────────────────────────────
# COIN TRANSACTION
# ─────────────────────────────────────────


class CoinTransaction(models.Model):
    REASON_CHOICES = [
        ("exam_pass", "Imtihondan o'tdi"),
        ("homework_done", "Vazifa qilingan"),
        ("homework_partial", "Vazifa chala qilingan"),
        ("homework_missed", "Vazifa qilinmagan"),
        ("present", "Darsga keldi"),
        ("late", "Kech keldi"),
        ("absent", "Darsga kelmadi"),
        ("manual", "Teacher tomonidan qo'lda"),
        ("purchase", "Magazindan xarid"),
        ("purchase_cancel", "Xarid bekor qilindi (qaytarildi)"),
        ("payment_ontime", "Oylik to'lov vaqtida"),
    ]

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="coin_transactions"
    )
    given_by = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coin_transactions_given",
    )
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default="manual")
    amount = models.IntegerField(default=0)
    note = models.CharField(max_length=255, blank=True)
    attendance = models.ForeignKey(
        Attendance,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coin_transactions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Coin tranzaksiyasi"
        verbose_name_plural = "Coin tranzaksiyalari"

    def __str__(self):
        return f"{self.student} — {self.get_reason_display()} — {self.amount}"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            Student.objects.filter(pk=self.student_id).update(
                coin_balance=models.F("coin_balance") + self.amount
            )

    def delete(self, *args, **kwargs):
        Student.objects.filter(pk=self.student_id).update(
            coin_balance=models.F("coin_balance") - self.amount
        )
        super().delete(*args, **kwargs)


# ─────────────────────────────────────────
# ATTENDANCE COIN SETTINGS
# ─────────────────────────────────────────


class AttendanceCoinSettings(models.Model):
    present = models.IntegerField(default=5, verbose_name="Keldi")
    late = models.IntegerField(default=2, verbose_name="Kech keldi")
    absent = models.IntegerField(default=-10, verbose_name="Kelmadi")

    # Oylik to'lovni vaqtida (yoki grace kunlar ichida) qilgan o'quvchiga
    # beriladigan coin. Miqdor va muddat menejer tomonidan o'zgartiriladi.
    payment_ontime = models.IntegerField(
        default=120, verbose_name="Vaqtida to'lov uchun coin"
    )
    payment_grace_days = models.IntegerField(
        default=5, verbose_name="Kechikishga ruxsat (kun)"
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Davomat coin sozlamasi"
        verbose_name_plural = "Davomat coin sozlamalari"

    def __str__(self):
        return f"Keldi:{self.present} Kech:{self.late} Kelmadi:{self.absent}"

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                "present": 5,
                "late": 2,
                "absent": -10,
                "payment_ontime": 120,
                "payment_grace_days": 5,
            },
        )
        return obj


# ─────────────────────────────────────────
# PRODUCT (DO'KON)
# ─────────────────────────────────────────


class Product(models.Model):
    name = models.CharField(max_length=200)
    image = models.URLField(max_length=500, blank=True)
    price_coins = models.IntegerField(default=0)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    stock = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Mahsulot"
        verbose_name_plural = "Mahsulotlar"

    def __str__(self):
        return f"{self.name} — {self.price_coins} coin"


# ─────────────────────────────────────────
# ORDER
# ─────────────────────────────────────────


class Order(models.Model):
    STATUS_CHOICES = [
        ("pending", "Kutilmoqda"),
        ("approved", "Berildi"),
        ("rejected", "Bekor qilindi"),
    ]

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="orders"
    )
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, related_name="orders"
    )
    product_name = models.CharField(max_length=200)
    price_coins = models.IntegerField(default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Buyurtma"
        verbose_name_plural = "Buyurtmalar"

    def __str__(self):
        return f"{self.student} — {self.product_name} — {self.status}"


class News(models.Model):
    PRIORITY_CHOICES = [
        ("normal", "Oddiy"),
        ("important", "Muhim"),
        ("urgent", "Shoshilinch"),
    ]

    title = models.CharField(max_length=200, verbose_name="Sarlavha")
    content = models.TextField(verbose_name="Matn")
    priority = models.CharField(
        max_length=20,
        choices=PRIORITY_CHOICES,
        default="normal",
        verbose_name="Muhimlik",
    )
    is_active = models.BooleanField(default=True, verbose_name="Faol")

    # ✅ Manager emas — Student (is_excellence=True bo'lgan)
    created_by = models.ForeignKey(
        "Student",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="news_posts",
        verbose_name="Kim yaratdi",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="Muddati")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Yangilik"
        verbose_name_plural = "Yangiliklar"

    def __str__(self):
        return self.title


# ─────────────────────────────────────────
# LEAD (Potensial mijozlar — Google Sheets'dan)
# ─────────────────────────────────────────


class Lead(models.Model):
    name = models.CharField(max_length=200, verbose_name="Ism")
    phone = models.CharField(max_length=50, blank=True, verbose_name="Telefon")
    phone2 = models.CharField(
        max_length=50, blank=True, verbose_name="Qo'shimcha telefon"
    )
    status = models.CharField(max_length=200, blank=True, verbose_name="Holat")
    interest = models.CharField(
        max_length=200, blank=True, verbose_name="Qiziqish / Kurs"
    )
    note = models.TextField(blank=True, verbose_name="Izoh")
    source_sheet = models.CharField(
        max_length=100, blank=True, verbose_name="Manba varaq"
    )
    source = models.CharField(max_length=30, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Lead (potensial mijoz)"
        verbose_name_plural = "Leadlar"

    def __str__(self):
        return f"{self.name} — {self.phone}"


# ─────────────────────────────────────────
# AD CHANNEL (Telegram reklama kanallari)
# ─────────────────────────────────────────


class AdChannel(models.Model):
    username = models.CharField(max_length=150, verbose_name="Kanal (@username)")
    title = models.CharField(max_length=200, blank=True, verbose_name="Nomi")
    note = models.TextField(blank=True, verbose_name="Izoh")
    source = models.CharField(max_length=30, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Reklama kanali"
        verbose_name_plural = "Reklama kanallari"

    def __str__(self):
        return self.username


class SheetImportMeta(models.Model):
    """Sheet importining versiyasi — mapping o'zgarganda avto qayta import uchun."""

    version = models.CharField(max_length=20, default="")
    imported_at = models.DateTimeField(auto_now=True)
    # Import server ko'tarilganda fon oqimida ishlaydi — u yerdagi xato
    # faqat logga tushib, tashqaridan ko'rinmay qolardi. Endi shu yerda
    # saqlanadi va /api/sheet-import-status/ orqali ko'rsa bo'ladi.
    last_error = models.TextField(blank=True, verbose_name="Oxirgi xato")

    class Meta:
        verbose_name = "Sheet import holati"
        verbose_name_plural = "Sheet import holati"

    def __str__(self):
        return f"v{self.version} ({self.imported_at:%Y-%m-%d %H:%M})"


# ─────────────────────────────────────────
# TELEGRAM XABAR TIZIMI
# ─────────────────────────────────────────


class TelegramSubscriber(models.Model):
    """Botga ulangan (telefonini yuborgan) foydalanuvchi.

    Bot avval faqat o'quvchilarni tanigan. Endi ustoz, menejer va lead
    ham ulanadi — har biriga o'ziga tegishli xabar boradi: ustozga o'z
    guruhlari, menejerga panel bildirishnomalari, leadga reklama.

    Bitta raqam bir nechta jadvalda uchrashi mumkin (ustozning o'quvchi
    profili ham bo'ladi), shuning uchun rol qidiruv tartibi bo'yicha
    aniqlanadi va `role` maydonida saqlanadi.
    """

    ROLE_CHOICES = [
        ("student", "O'quvchi"),
        ("teacher", "Ustoz"),
        ("manager", "Menejer"),
        ("lead", "Potensial mijoz"),
        ("unknown", "Bazada topilmadi"),
    ]

    chat_id = models.BigIntegerField(unique=True, verbose_name="Telegram chat ID")

    student = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tg_subscribers",
        verbose_name="O'quvchi",
    )
    teacher = models.ForeignKey(
        "Teacher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tg_subscribers",
        verbose_name="Ustoz",
    )
    manager = models.ForeignKey(
        "Manager",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tg_subscribers",
        verbose_name="Menejer",
    )
    lead = models.ForeignKey(
        "Lead",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tg_subscribers",
        verbose_name="Lead",
    )

    role = models.CharField(
        max_length=10,
        choices=ROLE_CHOICES,
        default="unknown",
        db_index=True,
        verbose_name="Roli",
    )

    phone = models.CharField(max_length=20, blank=True, verbose_name="Telefon")
    tg_name = models.CharField(max_length=200, blank=True, verbose_name="TG ismi")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Telegram obunachi"
        verbose_name_plural = "Telegram obunachilar"

    def __str__(self):
        who = self.student or self.teacher or self.manager or self.lead
        return f"{self.tg_name or self.chat_id} → {who or 'ulanmagan'}"


class PhoneVerification(models.Model):
    """Student qo'shishda telefon raqamni bot orqali tasdiqlash kodi."""

    phone = models.CharField(max_length=20, db_index=True, verbose_name="Telefon")
    code = models.CharField(max_length=6, verbose_name="Kod")
    chat_id = models.BigIntegerField(null=True, blank=True)
    attempts = models.IntegerField(default=0, verbose_name="Urinishlar")
    verified_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Telefon tasdiqlash"
        verbose_name_plural = "Telefon tasdiqlashlar"

    def __str__(self):
        return f"{self.phone} — {self.code}"


class SentMessage(models.Model):
    KIND_CHOICES = [
        ("single", "Bitta o'quvchi"),
        ("group", "Guruh"),
        ("all", "Barcha o'quvchilar"),
    ]
    STATUS_CHOICES = [
        ("sent", "Yuborildi"),
        ("failed", "Xato"),
        ("no_chat", "Botga ulanmagan"),
    ]

    student = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_messages",
    )
    chat_id = models.BigIntegerField(null=True, blank=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="single")
    text = models.TextField(verbose_name="Matn")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="sent")
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Yuborilgan xabar"
        verbose_name_plural = "Yuborilgan xabarlar"

    def __str__(self):
        return f"{self.student} — {self.status}"


class Expense(models.Model):
    CATEGORY_CHOICES = [
        ("salary", "Maosh"),
        ("utility", "Kommunal"),
        ("equipment", "Qurilmalar"),
        ("rent", "Ijara"),
        ("marketing", "Marketing"),
        ("other", "Boshqa"),
    ]

    title = models.CharField(max_length=200, verbose_name="Nomi")
    amount = models.IntegerField(default=0, verbose_name="Summa")
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default="other",
        verbose_name="Kategoriya",
    )
    date = models.DateField(default=timezone.now, verbose_name="Sana")
    note = models.TextField(blank=True, verbose_name="Izoh")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Xarajat"
        verbose_name_plural = "Xarajatlar"

    def __str__(self):
        return f"{self.title} — {self.amount}"


class TeacherSalary(models.Model):
    """Ustozning bir oylik oyligi.

    Summa ikki yo'l bilan aniqlanadi: default — stavka × o'quvchilar
    soni, yoki supermenejer qo'lda kiritgan `manual_amount`. "To'landi"
    bosilganda xarajatlarga (Expense, kategoriya "salary") avtomatik
    yoziladi — shu sababli `expense` bog'lanishi saqlanadi: yozuv bekor
    qilinsa xarajat ham o'chiriladi.
    """

    teacher = models.ForeignKey(
        Teacher, on_delete=models.CASCADE, related_name="salaries"
    )
    month = models.CharField(max_length=7, verbose_name="Oy (YYYY-MM)")

    # Bo'sh bo'lsa stavka × o'quvchilar soni ishlatiladi
    manual_amount = models.IntegerField(
        null=True, blank=True, verbose_name="Qo'lda kiritilgan summa"
    )
    # To'lov paytidagi holat — keyin o'quvchilar soni o'zgarsa ham
    # to'langan summa o'zgarmasligi uchun saqlanadi
    students_count = models.IntegerField(default=0, verbose_name="O'quvchilar soni")
    paid_amount = models.IntegerField(default=0, verbose_name="To'langan summa")

    is_paid = models.BooleanField(default=False, verbose_name="To'landi")
    paid_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, verbose_name="Izoh")

    expense = models.ForeignKey(
        "Expense",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teacher_salaries",
        verbose_name="Xarajat yozuvi",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("teacher", "month")
        ordering = ["-month", "teacher__name"]
        verbose_name = "Ustoz oyligi"
        verbose_name_plural = "Ustoz oyliklari"

    def __str__(self):
        return f"{self.teacher} — {self.month}"


class TeacherAdvance(models.Model):
    """Ustoz oyligidan oldindan olgan pul (avans).

    Ustozga oy o'rtasida pul kerak bo'lib qolsa shu yerga yoziladi:
    darhol xarajatlarga tushadi va oy oxirida qoladigan oylikdan
    ayiriladi — ya'ni bir pul ikki marta xarajat bo'lib ketmaydi.
    """

    teacher = models.ForeignKey(
        Teacher, on_delete=models.CASCADE, related_name="advances"
    )
    month = models.CharField(max_length=7, verbose_name="Oy (YYYY-MM)")
    amount = models.IntegerField(default=0, verbose_name="Summa")
    note = models.CharField(max_length=255, blank=True, verbose_name="Izoh")
    date = models.DateField(default=timezone.now, verbose_name="Sana")

    expense = models.ForeignKey(
        "Expense",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teacher_advances",
        verbose_name="Xarajat yozuvi",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Ustoz avansi"
        verbose_name_plural = "Ustoz avanslari"

    def __str__(self):
        return f"{self.teacher} — {self.month} — {self.amount}"


class LoginDevice(models.Model):
    """Panelga kirgan qurilma.

    Loyihada sessiya/token yo'q, shuning uchun qurilma brauzerda bir
    marta yaratiladigan `device_id` (localStorage) bilan aniqlanadi.
    Supermenejer kirishlar tarixini ko'radi va shubhali qurilmani
    bloklab qo'yishi mumkin — bloklangan qurilmadan login o'tmaydi.
    """

    ROLE_CHOICES = [
        ("manager", "Menejer"),
        ("super", "Supermenejer"),
        ("teacher", "Ustoz"),
        ("student", "O'quvchi"),
    ]

    device_id = models.CharField(max_length=64, db_index=True, verbose_name="Qurilma ID")
    phone = models.CharField(max_length=20, db_index=True, verbose_name="Telefon")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="manager")
    user_name = models.CharField(max_length=200, blank=True, verbose_name="Kim")

    manager = models.ForeignKey(
        Manager,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="devices",
    )

    user_agent = models.CharField(max_length=400, blank=True, verbose_name="Brauzer")
    ip = models.CharField(max_length=64, blank=True, verbose_name="IP manzil")
    login_count = models.IntegerField(default=0, verbose_name="Kirishlar soni")

    is_blocked = models.BooleanField(default=False, verbose_name="Bloklangan")
    blocked_at = models.DateTimeField(null=True, blank=True)

    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("device_id", "phone")
        ordering = ["-last_seen"]
        verbose_name = "Kirgan qurilma"
        verbose_name_plural = "Kirgan qurilmalar"

    def __str__(self):
        return f"{self.user_name or self.phone} — {self.device_id[:8]}"


class FaceDevice(models.Model):
    """Yuz tanish terminali (Hikvision DS-K1T3xx kabi).

    Terminal odatda lokal tarmoqda turadi va bulutdagi serverga
    hodisa yuboradi ("HTTP listening"). Terminal serverga chiqa oladi,
    lekin server terminalga kira olmaydi — shuning uchun `host`
    ixtiyoriy: faqat terminalga tashqaridan kirish sozlangan bo'lsa
    to'ldiriladi va o'shanda saytdan yuz yuborish ham ishlaydi.
    """

    name = models.CharField(max_length=100, verbose_name="Nomi")
    serial = models.CharField(
        max_length=64, blank=True, db_index=True, verbose_name="Seriya raqami"
    )
    location = models.CharField(max_length=100, blank=True, verbose_name="Joyi")

    # Terminal shu manzilga hodisa yuboradi; manzildagi maxfiy kalit
    # orqali begona so'rovlar ajratiladi (terminal qo'shimcha sarlavha
    # yubora olmaydi, shuning uchun kalit URL ichida)
    secret = models.CharField(max_length=64, unique=True, verbose_name="Maxfiy kalit")

    # ── Ixtiyoriy: saytdan terminalga yuborish uchun ──
    host = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Terminal manzili (http://ip:port)",
    )
    username = models.CharField(max_length=64, blank=True, verbose_name="Login")
    password = models.CharField(max_length=128, blank=True, verbose_name="Parol")

    is_active = models.BooleanField(default=True, verbose_name="Faol")
    last_event_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Oxirgi hodisa"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Yuz tanish terminali"
        verbose_name_plural = "Yuz tanish terminallari"

    def __str__(self):
        return self.name

    @property
    def can_push(self):
        """Saytdan terminalga yuborish sozlanganmi."""
        return bool(self.host and self.username)


class FaceEvent(models.Model):
    """Terminaldan kelgan bitta hodisa.

    Muvaffaqiyatlisi ham, tanilmagani ham yoziladi — supermenejer
    nima kelayotganini ko'rib, bog'lanmagan raqamlarni topa olsin.
    """

    STATUS_CHOICES = [
        ("marked", "Davomat belgilandi"),
        ("already", "Allaqachon belgilangan"),
        ("no_lesson", "Bugun dars yo'q"),
        ("no_group", "Guruhga biriktirilmagan"),
        ("unknown", "O'quvchi topilmadi"),
        ("ignored", "E'tiborsiz qoldirildi"),
    ]

    device = models.ForeignKey(
        FaceDevice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    person_id = models.CharField(
        max_length=32, db_index=True, verbose_name="Terminaldagi raqam"
    )
    person_name = models.CharField(max_length=200, blank=True)

    student = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="face_events",
    )
    attendance = models.ForeignKey(
        Attendance,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="face_events",
    )

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="unknown")
    note = models.CharField(max_length=255, blank=True)

    happened_at = models.DateTimeField(verbose_name="Terminal vaqti")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Yuz tanish hodisasi"
        verbose_name_plural = "Yuz tanish hodisalari"

    def __str__(self):
        return f"{self.person_id} — {self.status}"


class FaceSync(models.Model):
    """Qaysi o'quvchi qaysi terminalga yozilgani.

    Terminal bittadan ko'p bo'lishi mumkin (kirish va chiqish eshigi),
    shuning uchun "yozildi" degan bayroq o'quvchida emas, shu yerda
    turadi — bir terminalga yozilgani ikkinchisiga yozilganini
    anglatmaydi.

    `photo_at` — yozilgan paytdagi `Student.face_updated_at`. O'quvchi
    yangi rasm yuborsa u sanadan katta bo'lib qoladi va yozuv "eskirgan"
    hisoblanadi: terminal yangi rasmni qayta oladi.
    """

    device = models.ForeignKey(
        FaceDevice, on_delete=models.CASCADE, related_name="syncs"
    )
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="face_syncs"
    )

    photo_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Yozilgan rasm sanasi"
    )
    ok = models.BooleanField(default=True, verbose_name="Muvaffaqiyatli")
    error = models.CharField(max_length=255, blank=True, verbose_name="Xato")
    synced_at = models.DateTimeField(auto_now=True, verbose_name="Yozilgan vaqt")

    class Meta:
        unique_together = ("device", "student")
        ordering = ["-synced_at"]
        verbose_name = "Terminalga yozilgan yuz"
        verbose_name_plural = "Terminalga yozilgan yuzlar"

    def __str__(self):
        return f"{self.student} → {self.device}"


class ActivityLog(models.Model):
    """Panelda kim nima qilgani — supermenejer uchun.

    Har bir o'zgartiruvchi amal (to'lov tasdiqlash, chegirma, o'quvchi
    o'chirish, guruh tahriri, xabar yuborish ...) shu yerga yoziladi.
    Maqsad — supermenejer menejerlar nima qilayotganini ko'rib tura
    olsin va kerak bo'lsa kim qachon nima qilganini topa olsin.

    Yozuv o'chirilgan obyektdan keyin ham qolishi kerak, shuning uchun
    ForeignKey emas, `target_type` + `target_id` + `target_name`
    saqlanadi.
    """

    # Kim
    actor_phone = models.CharField(max_length=20, db_index=True, blank=True)
    actor_name = models.CharField(max_length=200, blank=True, verbose_name="Kim")
    actor_role = models.CharField(max_length=10, blank=True, verbose_name="Roli")
    manager = models.ForeignKey(
        Manager,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity",
    )

    # Nima
    action = models.CharField(max_length=50, db_index=True, verbose_name="Amal")
    description = models.CharField(max_length=300, verbose_name="Tavsif")

    # Nimaga
    target_type = models.CharField(max_length=30, blank=True)
    target_id = models.IntegerField(null=True, blank=True)
    target_name = models.CharField(max_length=200, blank=True)

    # Qo'shimcha tafsilot (eski/yangi qiymat kabi)
    meta = models.JSONField(default=dict, blank=True)

    ip = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Harakat yozuvi"
        verbose_name_plural = "Harakatlar jurnali"

    def __str__(self):
        return f"{self.actor_name} — {self.action}"


class LessonReminderLog(models.Model):
    """Darsdan oldingi telegram eslatmasi yuborilganini belgilaydi.

    Bir guruhga bir kunda faqat bitta eslatma yuboriladi — board (jadval
    ekrani) bir necha marta so'rov yuborsa ham takror ketmasligi uchun.
    """

    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="reminder_logs"
    )
    date = models.DateField(verbose_name="Sana")
    sent = models.IntegerField(default=0, verbose_name="Yuborildi")
    no_chat = models.IntegerField(default=0, verbose_name="Botga ulanmagan")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("group", "date")
        verbose_name = "Dars eslatmasi"
        verbose_name_plural = "Dars eslatmalari"

    def __str__(self):
        return f"{self.group} — {self.date}"


# ─────────────────────────────────────────
# KASSA (kunlik smena + tranzaksiya jurnali)
# ─────────────────────────────────────────
#
# Muammo: `Payment.paid_amount` — bitta o'zgaruvchan maydon. Kassir bir
# kuni 200 000 kiritib kassani topshiradi, ertasi o'sha maydonni 400 000
# qilib o'zgartirsa — bugun +200 000 tushgani hech qayerda yozilmaydi va
# kunlik topshiruv noto'g'ri chiqadi.
#
# Yechim: har pul harakati o'chirilmaydigan `CashEntry` sifatida
# yoziladi (ActivityLog kabi — obyekt o'chsa ham qoladi). To'lov
# tahrirlanganda maydon ustiga yozilmaydi, FARQ (delta) yangi yozuv
# bo'lib bugungi ochiq smenaga tushadi. Kunlik smena `CashSession` —
# kassir kun oxirida fizik pulni sanab topshiradi, tizim kutilgan bilan
# solishtiradi. Yopilgan smena qulflanadi; keyingi to'lov yangi smena
# ochadi, kechagi topshirilgan hisob buzilmaydi.


class CashRegisterSettings(models.Model):
    """Kassa tizimining supermenejer sozlamasi (singleton).

    Supermenejer butun modulni o'chirib-yoqa oladi. O'chirilganda kunlik
    smena/topshiruv ishlamaydi (eski tartib), lekin jurnal baribir
    yoziladi — keyin yoqilsa tarix to'liq bo'lsin.
    """

    enabled = models.BooleanField(
        default=True, verbose_name="Kunlik kassa yoqilgan"
    )
    # Topshirishda kassir fizik sanagan pulni kiritishi majburiymi
    require_counted = models.BooleanField(
        default=True, verbose_name="Fizik sanoq majburiy"
    )
    # Topshirilgan smena qulflansinmi (undagi yozuvlar o'zgarmasin)
    lock_after_close = models.BooleanField(
        default=True, verbose_name="Topshirilgan smena qulflanadi"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Kassa sozlamasi"
        verbose_name_plural = "Kassa sozlamalari"

    def __str__(self):
        return "Kassa yoqilgan" if self.enabled else "Kassa o'chirilgan"

    @classmethod
    def get_settings(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj


class CashSession(models.Model):
    """Bir kunlik umumiy kassa (smena).

    Kassa bitta — barcha to'lovlar bitta accountdan kiritiladi. Har
    kunga bitta smena (date unikal). Kunning birinchi to'lovida
    avtomatik ochiladi, har kuni o'zi yangilanadi. Kassir kun oxirida
    fizik pulni sanab topshiradi; har kun tarixda alohida qator bo'lib
    qoladi. Topshirilgandan keyin o'sha kuni yana pul tushsa smena qayta
    ochiladi (kun tugamaguncha yakuniy emas)."""

    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Ochiq"),
        (STATUS_CLOSED, "Topshirilgan"),
    ]

    cashier = models.ForeignKey(
        Manager,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cash_sessions",
        verbose_name="Kassir",
    )
    # Oxirgi amalni bajargan kassir (kim topshirgani ko'rinib tursin)
    cashier_name = models.CharField(max_length=200, blank=True)

    # Toshkent sanasi — har kunga bitta smena
    date = models.DateField(unique=True, db_index=True, verbose_name="Sana")
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True
    )

    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    # Topshirish paytida muhrlanadigan qiymatlar
    expected_total = models.IntegerField(
        default=0, verbose_name="Tizim hisobi"
    )
    counted_total = models.IntegerField(
        null=True, blank=True, verbose_name="Sanalgan pul"
    )
    # counted - expected: manfiy = kamomad, musbat = ortiqcha
    difference = models.IntegerField(default=0, verbose_name="Farq")
    note = models.CharField(max_length=255, blank=True, verbose_name="Izoh")

    class Meta:
        ordering = ["-date", "-opened_at"]
        verbose_name = "Kassa smenasi"
        verbose_name_plural = "Kassa smenalari"

    def __str__(self):
        return f"{self.date} — {self.get_status_display()}"

    @property
    def is_open(self):
        return self.status == self.STATUS_OPEN

    def live_total(self):
        """Smenaga tushgan pul (jurnal bo'yicha jonli yig'indi)."""
        return self.entries.aggregate(s=models.Sum("amount"))["s"] or 0

    @classmethod
    def for_date(cls, day):
        """O'sha kunning smenasini qaytaradi, yo'q bo'lsa ochadi."""
        obj, _created = cls.objects.get_or_create(date=day)
        return obj

    @classmethod
    def on_date(cls, day):
        """O'sha kunning smenasi (yo'q bo'lsa None — o'qish uchun, ochmaydi)."""
        return cls.objects.filter(date=day).first()


class CashEntry(models.Model):
    """Kassa jurnalining bitta yozuvi — o'chirilmaydi.

    Har to'lov qabuli yoki tuzatilishi shu yerga yoziladi. `amount`
    ishorali: +200 000 pul tushdi, tuzatishda farq (delta) yoziladi
    (masalan 200 000 dan 400 000 ga o'zgarsa +200 000, aksincha −).
    """

    KIND_PAYMENT = "payment"
    KIND_ADJUST = "adjust"
    KIND_CHOICES = [
        (KIND_PAYMENT, "To'lov"),
        (KIND_ADJUST, "Tuzatish"),
    ]

    session = models.ForeignKey(
        CashSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="entries",
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cash_entries",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cash_entries",
    )
    student_name = models.CharField(max_length=200, blank=True)

    cashier = models.ForeignKey(
        Manager,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cash_entries",
    )
    cashier_name = models.CharField(max_length=200, blank=True)

    amount = models.IntegerField(verbose_name="Summa (ishorali)")
    month = models.CharField(max_length=7, blank=True, verbose_name="To'lov davri")
    kind = models.CharField(
        max_length=10, choices=KIND_CHOICES, default=KIND_PAYMENT
    )
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Kassa yozuvi"
        verbose_name_plural = "Kassa yozuvlari"

    def __str__(self):
        return f"{self.student_name or '—'} — {self.amount:+} ({self.month})"
