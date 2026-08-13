from django.urls import path
from . import views
from . import super_views
from .views import (
    get_news,
    get_active_news,
    get_news_detail,
    create_news,
    update_news,
    delete_news,
)

urlpatterns = [
    # ───────────────────────────────
    # MANAGER (eng yuqori daraja)
    # ───────────────────────────────
    path("manager/register/", views.manager_register),
    path("manager/login/", views.manager_login),
    path("excellence/login/", views.excellence_login),
    path("excellence/", views.excellence_info),
    path("managers/", views.get_managers),
    path("manager/<int:manager_id>/update/", views.update_manager),
    path("manager/<int:manager_id>/delete/", views.delete_manager),
    # ───────────────────────────────
    # SUPERMENEJER (faqat is_super)
    # ───────────────────────────────
    # Terminal shu manzilga hodisa yuboradi — kalit URL ichida,
    # chunki Hikvision qo'shimcha sarlavha yubora olmaydi
    path("faceid/event/<str:secret>/", super_views.faceid_event),
    # Terminal yonidagi agent yuz navbatini shu manzildan oladi
    path("faceid/sync/<str:secret>/", super_views.faceid_sync_queue),
    path("presence/ping/", views.presence_ping),
    path("super/online/", super_views.get_online),
    path("super/faceid/devices/", super_views.get_face_devices),
    path("super/faceid/devices/create/", super_views.save_face_device),
    path("super/faceid/devices/<int:device_id>/", super_views.save_face_device),
    path(
        "super/faceid/devices/<int:device_id>/delete/",
        super_views.delete_face_device,
    ),
    path("super/faceid/events/", super_views.get_face_events),
    path("super/faceid/link/<int:student_id>/", super_views.link_student_face),
    path(
        "super/faceid/devices/<int:device_id>/push/<int:student_id>/",
        super_views.push_student_to_device,
    ),
    # Botdan kelgan yuz rasmlari
    path("super/faceid/enrollments/", super_views.get_face_enrollments),
    path(
        "super/faceid/enrollments/<int:student_id>/photo/",
        super_views.get_face_photo,
    ),
    path(
        "super/faceid/enrollments/<int:student_id>/status/",
        super_views.set_face_status,
    ),
    path(
        "super/faceid/devices/<int:device_id>/sync/",
        super_views.sync_face_device,
    ),
    path("super/overview/", super_views.get_overview),
    path("super/activity/", super_views.get_activity),
    path("super/activity/summary/", super_views.get_activity_summary),
    path("super/permissions/", super_views.get_permission_catalog),
    path("super/managers/", super_views.get_super_managers),
    path("super/managers/create/", super_views.create_super_managed_manager),
    path(
        "super/managers/<int:manager_id>/permissions/",
        super_views.update_manager_permissions,
    ),
    path(
        "super/managers/<int:manager_id>/password/",
        super_views.set_manager_password,
    ),
    path("super/devices/", super_views.get_devices),
    path("super/devices/<int:device_pk>/block/", super_views.set_device_blocked),
    path("super/salaries/", super_views.get_salaries),
    path("super/salaries/bulk/", super_views.bulk_salary_settings),
    path("super/salaries/<int:teacher_id>/rate/", super_views.update_salary_rate),
    path("super/salaries/<int:teacher_id>/amount/", super_views.set_salary_amount),
    path("super/salaries/<int:teacher_id>/pay/", super_views.pay_salary),
    path("super/salaries/<int:teacher_id>/unpay/", super_views.unpay_salary),
    path("super/salaries/<int:teacher_id>/advance/", super_views.create_advance),
    path("super/advances/<int:advance_id>/delete/", super_views.delete_advance),
    # ───────────────────────────────
    # TEACHERS
    # ───────────────────────────────
    path("teachers/", views.get_teachers),
    path("teachers/create/", views.create_teacher),
    path("teachers/delete/<int:teacher_id>/", views.delete_teacher),
    path("teachers/update/<int:teacher_id>/", views.update_teacher),
    path(
        "teachers/<int:teacher_id>/penalty-limit/", views.update_teacher_penalty_limit
    ),
    path("teachers/reassign/", views.reassign_students),
    path("teachers/<int:teacher_id>/history/", views.get_teacher_history),
    # ───────────────────────────────
    # MENEJER PANELI
    # ───────────────────────────────
    path("sheet-import-status/", views.sheet_import_status),
    path("teachers/overview/", views.get_teachers_overview),
    path("students/overview/", views.get_students_overview),
    path("students/transfer/", views.transfer_students),
    # ───────────────────────────────
    # STAGE PRICES
    # ───────────────────────────────
    path("stage-prices/", views.get_stage_prices),
    path("stage-prices/update/<int:stage>/", views.update_stage_price),
    # ───────────────────────────────
    # STUDENTS
    # ───────────────────────────────
    path("students/", views.get_students),
    path("students/update/<int:student_id>/", views.update_student),
    path("students/delete/<int:student_id>/", views.delete_student),
    path("students/bulk-delete/", views.bulk_delete_students),
    path("ping/", views.ping),
    path("register/", views.register_student),
    path("login/", views.login_student),
    path("logout/", views.logout_view),
    # ───────────────────────────────
    # LESSONS
    # ───────────────────────────────
    path("lessons/", views.get_lessons),
    path("lessons/create/", views.create_lesson),
    # ───────────────────────────────
    # ATTENDANCE
    # ───────────────────────────────
    path("attendance/group-day/", views.attendance_group_day),
    path("attendance/group-month/", views.attendance_group_month),
    path("attendance/<int:lesson_id>/", views.get_attendance),
    path("attendance/update/<int:attendance_id>/", views.update_attendance),
    path("student-attendance/<int:student_id>/", views.get_student_attendance),
    path("monthly-absences/", views.get_monthly_absences),
    # ───────────────────────────────
    # ATTENDANCE COIN SETTINGS
    # ───────────────────────────────
    path("attendance-coin-settings/", views.get_attendance_coin_settings),
    path("attendance-coin-settings/update/", views.update_attendance_coin_settings),
    # ───────────────────────────────
    # PAYMENTS
    # ───────────────────────────────
    path("payments/<int:student_id>/", views.get_payments),
    path("students/<int:student_id>/wallet/", views.get_student_wallet),
    path("payments/", views.get_all_payments),
    path("payments/generate/", views.generate_payments),
    path("payments/confirm/<int:payment_id>/", views.confirm_payment),
    path("payments/update/<int:payment_id>/", views.update_payment_amount),
    # Bo'lib to'lash — jami emas, shu safar tushgan summa yuboriladi
    path("payments/<int:payment_id>/pay/", views.add_payment_installment),
    path("payments/<int:payment_id>/history/", views.get_payment_installments),
    # To'lov kartasi + chek so'rovlari
    path("payment-settings/", views.get_payment_settings),
    path("payment-settings/update/", views.update_payment_settings),
    path("payment-requests/create/", views.create_payment_request),
    path("payment-requests/", views.get_payment_requests),
    path("payment-requests/pending-count/", views.pending_requests_count),
    path("payment-requests/student/<int:student_id>/", views.get_student_payment_requests),
    path("payment-requests/<int:req_id>/accept/", views.accept_payment_request),
    path("payment-requests/<int:req_id>/reject/", views.reject_payment_request),
    # ───────────────────────────────
    # STUDENT PENALTIES
    # ───────────────────────────────
    path("penalties/student/<int:student_id>/", views.get_student_penalties),
    path(
        "penalties/by-teacher/<int:teacher_id>/", views.get_teacher_students_penalties
    ),
    path("penalties/create/", views.create_student_penalty),
    path("penalties/<int:penalty_id>/delete/", views.delete_student_penalty),
    # ───────────────────────────────
    # COINS (Keng va mufassal)
    # ───────────────────────────────
    # Balans ko'rish
    path("coins/balance/<int:student_id>/", views.get_coin_balance),
    path("coins/balances/all/", views.get_all_coin_balances),
    # O'quvchi coins
    path("coins/student/<int:student_id>/", views.get_student_coins),
    path("coins/transactions/<int:student_id>/", views.get_coin_transactions),
    # Coin berish/olish
    path("coins/add/", views.add_coin),
    path("coins/give/", views.give_manual_coins),
    path("coins/set-balance/<int:student_id>/", views.set_coin_balance),
    # Coin tranzaksiya boshqarish
    path("coins/transaction/<int:txn_id>/delete/", views.delete_coin_transaction),
    # Reytingi
    path("leaderboard/", views.get_leaderboard),
    path("leaderboard/groups/", views.get_group_leaderboard),
    # ───────────────────────────────
    # PRODUCTS (DO'KON)
    # ───────────────────────────────
    path("products/", views.get_products),
    path("products/all/", views.get_all_products),
    path("products/create/", views.create_product),
    path("products/update/<int:product_id>/", views.update_product),
    path("products/delete/<int:product_id>/", views.delete_product),
    # ───────────────────────────────
    # ORDERS (Buyurtmalar)
    # ───────────────────────────────
    path("orders/create/", views.create_order),
    path("orders/student/<int:student_id>/", views.get_student_orders),
    path("orders/", views.get_all_orders),
    path("orders/resolve/<int:order_id>/", views.resolve_order),
    # ───────────────────────────────
    # GROUPS (Guruhlar)
    # ───────────────────────────────
    path("groups/", views.get_groups),
    path("groups/<int:group_id>/", views.get_group),
    path("groups/create/", views.create_group),
    path("groups/update/<int:group_id>/", views.update_group),
    path("groups/delete/<int:group_id>/", views.delete_group),
    # ───────────────────────────────
    # COURSES (Kurslar)
    # ───────────────────────────────
    path("courses/", views.get_courses),
    path("courses/<int:course_id>/", views.get_course),
    path("courses/create/", views.create_course),
    path("courses/update/<int:course_id>/", views.update_course),
    path("courses/delete/<int:course_id>/", views.delete_course),
    # news
    path("news/", get_news, name="news-list"),
    path("news/active/", get_active_news, name="news-active"),
    path("news/create/", create_news, name="news-create"),
    path("news/<int:news_id>/", get_news_detail, name="news-detail"),
    path("news/<int:news_id>/update/", update_news, name="news-update"),
    path("news/<int:news_id>/delete/", delete_news, name="news-delete"),
    
    # ───────────────────────────────
    # LEADS / REKLAMA (import qilingan baza)
    # ───────────────────────────────
    path("leads/", views.get_leads),
    path("leads/<int:lead_id>/delete/", views.delete_lead),
    path("ad-channels/", views.get_ad_channels),
    path("graduates/", views.get_graduates),
    # ───────────────────────────────
    # TELEGRAM XABARLAR
    # ───────────────────────────────
    path("tg/webhook/", views.tg_webhook),
    path("tg/status/", views.tg_status),
    path("messages/send/", views.send_message_student),
    path("messages/send-group/", views.send_message_group),
    path("messages/send-all/", views.send_message_all),
    path("messages/send-students/", views.send_message_students),
    path("messages/send-leads/", views.send_message_leads),
    path("messages/send-teachers/", views.send_message_teachers),
    path("receipt-settings/", views.get_receipt_settings),
    path("receipt-settings/update/", views.update_receipt_settings),
    path("receipt-settings/preview/", views.preview_receipt),
    path("lessons/send-reminders/", views.send_lesson_reminders),
    path("messages/history/", views.get_message_history),
    # ───────────────────────────────
    # TELEFON TASDIQLASH (bot orqali kod)
    # ───────────────────────────────
    path("change-password/", views.change_password),
    path("profile/update/", views.update_profile),
    path("verify/send-code/", views.send_verification_code),
    path("verify/check-code/", views.check_verification_code),
    path("expenses/", views.get_expenses),
    path("expenses/create/", views.create_expense),
    path("expenses/<int:expense_id>/update/", views.update_expense),
    path("expenses/<int:expense_id>/delete/", views.delete_expense),
    path("finance-summary/", views.get_finance_summary),
    # KASSA — kunlik smena + oylik hisobot
    path("cash/current/", views.get_cash_current),
    path("cash/close/", views.close_cash_session),
    path("cash/sessions/", views.get_cash_sessions),
    path("cash/settings/", views.get_cash_settings),
    path("cash/settings/update/", views.update_cash_settings),
]

app_name = "register_withvue"
