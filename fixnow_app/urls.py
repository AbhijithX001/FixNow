from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [

    # ── Home ─────────────────────────────────────────────────────────────────
    path('', views.home, name='home'),

    # ── Shared Auth ───────────────────────────────────────────────────────────
    path('login/', views.user_login, name='user_login'),
    path('logout/', views.user_logout, name='user_logout'),

    # ── OTP ───────────────────────────────────────────────────────────────────
    path('register/verify-otp/', views.verify_otp, name='verify_otp'),
    path('register/resend-otp/', views.resend_otp, name='resend_otp'),

    # ── Customer ──────────────────────────────────────────────────────────────
    path('register/customer/', views.customer_register, name='customer_register'),

    # ── Provider ──────────────────────────────────────────────────────────────
    path('register/provider/', views.provider_register, name='provider_register'),
    path('provider/pending-approval/', views.provider_pending, name='provider_pending'),
    path('provider/dashboard/', views.provider_dashboard, name='provider_dashboard'),
    path('provider/bookings/', views.provider_booking_list, name='provider_booking_list'),
    path('provider/bookings/<int:id>/', views.provider_booking_detail, name='provider_booking_detail'),
    path('provider/profile/edit/', views.provider_profile_edit, name='provider_profile_edit'),
    path('provider/availability/toggle/', views.provider_toggle_availability, name='provider_toggle_availability'),
    path('provider/notifications/', views.provider_notifications, name='provider_notifications'),

    # ── Password Reset ────────────────────────────────────────────────────────
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='accounts/password_reset.html',
        email_template_name='accounts/password_reset_email.html',
        subject_template_name='accounts/password_reset_subject.txt',
    ), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='accounts/password_reset_done.html',
    ), name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='accounts/password_reset_confirm.html',
    ), name='password_reset_confirm'),
    path('password-reset-complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='accounts/password_reset_complete.html',
    ), name='password_reset_complete'),

    # ── Admin Auth ────────────────────────────────────────────────────────────
    path('dashboard/login/', views.admin_login, name='admin_login'),
    path('dashboard/logout/', views.admin_logout, name='admin_logout'),

    # ── Admin Dashboard ───────────────────────────────────────────────────────
    path('dashboard/', views.admin_dashboard, name='admin_dashboard'),

    # ── Admin Providers ───────────────────────────────────────────────────────
    path('dashboard/providers/', views.admin_provider_list, name='admin_provider_list'),
    path('dashboard/providers/<int:id>/', views.admin_provider_detail, name='admin_provider_detail'),
    path('dashboard/providers/<int:id>/approve/', views.admin_provider_approve, name='admin_provider_approve'),
    path('dashboard/providers/<int:id>/reject/', views.admin_provider_reject, name='admin_provider_reject'),
    path('dashboard/providers/<int:id>/suspend/', views.admin_provider_suspend, name='admin_provider_suspend'),
    path('dashboard/providers/<int:id>/delete/', views.admin_provider_delete, name='admin_provider_delete'),
    path('dashboard/providers/<int:id>/create-category/', views.admin_provider_create_category, name='admin_provider_create_category'),
    path('dashboard/providers/<int:id>/assign-category/', views.admin_provider_assign_category, name='admin_provider_assign_category'),

    # ── Admin Customers ───────────────────────────────────────────────────────
    path('dashboard/customers/', views.admin_customer_list, name='admin_customer_list'),
    path('dashboard/customers/<int:id>/block/', views.admin_customer_block, name='admin_customer_block'),

    # ── Admin Bookings ────────────────────────────────────────────────────────
    path('dashboard/bookings/', views.admin_booking_list, name='admin_booking_list'),
    path('dashboard/bookings/<int:id>/', views.admin_booking_detail, name='admin_booking_detail'),

    # ── Admin Categories ──────────────────────────────────────────────────────
    path('dashboard/categories/', views.admin_category_list, name='admin_category_list'),

    # ── Admin Pincodes ────────────────────────────────────────────────────────
    path('dashboard/pincodes/', views.admin_pincode_list, name='admin_pincode_list'),

    # ── Admin Reviews ─────────────────────────────────────────────────────────
    path('dashboard/reviews/', views.admin_review_list, name='admin_review_list'),
    path('dashboard/reviews/<int:id>/delete/', views.admin_review_delete, name='admin_review_delete'),

    # ── Frontend / Customer ───────────────────────────────────────────────────
    path('services/<slug:slug>/', views.service_category, name='service_category'),
    path('book/<int:provider_id>/', views.book_provider, name='book_provider'),
    path('booking/confirm/<int:booking_id>/', views.booking_confirm, name='booking_confirm'),
    path('my/bookings/', views.customer_dashboard, name='customer_dashboard'),
    path('my/bookings/<int:booking_id>/', views.customer_booking_detail, name='customer_booking_detail'),
    path('my/bookings/<int:booking_id>/review/', views.submit_review, name='submit_review'),
    path('my/bookings/<int:booking_id>/cancel/', views.cancel_booking, name='cancel_booking'),

]
