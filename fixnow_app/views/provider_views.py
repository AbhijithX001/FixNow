from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from ..models import (
    CustomUser, ProviderProfile, ServiceCategory,
    ServicePincode, Booking, BookingStatusLog, Notification
)


# ── Provider Register ─────────────────────────────────────────────────────────

def provider_register(request):
    categories = ServiceCategory.objects.filter(is_active=True)
    pincodes = ServicePincode.objects.all()

    if request.method == 'POST':
        # Basic fields
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        password = request.POST.get('password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()

        # Provider specific fields
        category_id = request.POST.get('category_id', '').strip()
        custom_category = request.POST.get('custom_category', '').strip()
        bio = request.POST.get('bio', '').strip()
        experience_years = request.POST.get('experience_years', 0)
        selected_pincodes = request.POST.getlist('pincodes')
        profile_photo = request.FILES.get('profile_photo')
        id_proof = request.FILES.get('id_proof')

        # Validations
        if password != confirm_password:
            messages.error(request, 'Passwords do not match.')
            return render(request, 'accounts/provider_register.html', {
                'categories': categories, 'pincodes': pincodes
            })

        if CustomUser.objects.filter(email=email).exists():
            messages.error(request, 'An account with this email already exists.')
            return render(request, 'accounts/provider_register.html', {
                'categories': categories, 'pincodes': pincodes
            })

        # Create user
        user = CustomUser.objects.create_user(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            user_type='provider'
        )

        # Resolve category
        category = None
        custom_category_request = None

        if category_id == 'other':
            custom_category_request = custom_category
        elif category_id:
            try:
                category = ServiceCategory.objects.get(id=category_id)
            except ServiceCategory.DoesNotExist:
                pass

        # Create provider profile
        provider = ProviderProfile.objects.create(
            user=user,
            category=category,
            custom_category_request=custom_category_request,
            bio=bio,
            experience_years=experience_years,
            profile_photo=profile_photo,
            id_proof=id_proof,
            status='pending'
        )

        # Add selected pincodes
        if selected_pincodes:
            pins = ServicePincode.objects.filter(id__in=selected_pincodes)
            provider.service_pincodes.set(pins)

        # Log in the user and redirect to pending page
        login(request, user)
        return redirect('provider_pending')

    return render(request, 'accounts/provider_register.html', {
        'categories': categories,
        'pincodes': pincodes,
    })


# ── Provider Pending Approval ─────────────────────────────────────────────────

@login_required(login_url='user_login')
def provider_pending(request):
    return render(request, 'accounts/provider_pending.html')


# ── Provider Dashboard Home ───────────────────────────────────────────────────

@login_required(login_url='user_login')
def provider_dashboard(request):
    if request.user.user_type != 'provider':
        return redirect('user_login')

    try:
        provider = request.user.provider_profile
    except ProviderProfile.DoesNotExist:
        return redirect('user_login')

    if provider.status != 'approved':
        return redirect('provider_pending')

    bookings = Booking.objects.filter(provider=provider)

    stats = {
        'total_bookings': bookings.count(),
        'pending_requests': bookings.filter(status='pending').count(),
        'confirmed': bookings.filter(status='confirmed').count(),
        'completed': bookings.filter(status='completed').count(),
    }

    new_requests = bookings.filter(
        status='pending'
    ).select_related('customer', 'category').order_by('-created_at')[:5]

    recent_completed = bookings.filter(
        status='completed'
    ).select_related('customer', 'category').order_by('-updated_at')[:5]

    unread_notifications = Notification.objects.filter(
        user=request.user, is_read=False
    ).count()

    return render(request, 'provider/dashboard.html', {
        'provider': provider,
        'stats': stats,
        'new_requests': new_requests,
        'recent_completed': recent_completed,
        'unread_notifications': unread_notifications,
    })


# ── Provider Booking List ─────────────────────────────────────────────────────

@login_required(login_url='user_login')
def provider_booking_list(request):
    if request.user.user_type != 'provider':
        return redirect('user_login')

    provider = get_object_or_404(ProviderProfile, user=request.user)

    if provider.status != 'approved':
        return redirect('provider_pending')

    status_filter = request.GET.get('status', '')
    bookings = Booking.objects.filter(
        provider=provider
    ).select_related('customer', 'category').order_by('-created_at')

    if status_filter:
        bookings = bookings.filter(status=status_filter)

    return render(request, 'provider/booking_list.html', {
        'bookings': bookings,
        'status_filter': status_filter,
        'provider': provider,
    })


# ── Provider Booking Detail ───────────────────────────────────────────────────

@login_required(login_url='user_login')
def provider_booking_detail(request, id):
    if request.user.user_type != 'provider':
        return redirect('user_login')

    provider = get_object_or_404(ProviderProfile, user=request.user)
    booking = get_object_or_404(Booking, id=id, provider=provider)

    if request.method == 'POST':
        action = request.POST.get('action')
        note = request.POST.get('note', '')

        status_map = {
            'accept':   ('confirmed',   'Booking confirmed by provider.'),
            'reject':   ('rejected',    'Booking rejected by provider.'),
            'start':    ('in_progress', 'Job started by provider.'),
            'complete': ('completed',   'Job completed by provider.'),
        }

        if action in status_map:
            new_status, default_note = status_map[action]

            # Validate transition
            valid_transitions = {
                'accept':   ['pending'],
                'reject':   ['pending'],
                'start':    ['confirmed'],
                'complete': ['in_progress'],
            }

            if booking.status in valid_transitions[action]:
                booking.status = new_status
                booking.save()

                BookingStatusLog.objects.create(
                    booking=booking,
                    status=new_status,
                    changed_by=request.user,
                    note=note or default_note
                )

                Notification.objects.create(
                    user=booking.customer,
                    message=f'Your booking #{booking.id} status has been updated to {new_status.replace("_", " ").title()}.'
                )

                messages.success(request, f'Booking status updated to {new_status.replace("_", " ").title()}.')
            else:
                messages.error(request, 'Invalid status transition.')

        return redirect('provider_booking_detail', id=id)

    status_logs = booking.status_logs.select_related('changed_by').all()

    return render(request, 'provider/booking_detail.html', {
        'booking': booking,
        'status_logs': status_logs,
        'provider': provider,
    })


# ── Provider Profile Edit ─────────────────────────────────────────────────────

@login_required(login_url='user_login')
def provider_profile_edit(request):
    if request.user.user_type != 'provider':
        return redirect('user_login')

    provider = get_object_or_404(ProviderProfile, user=request.user)
    all_pincodes = ServicePincode.objects.all()

    if request.method == 'POST':
        bio = request.POST.get('bio', '').strip()
        experience_years = request.POST.get('experience_years', 0)
        is_available = request.POST.get('is_available') == 'on'
        selected_pincodes = request.POST.getlist('pincodes')
        profile_photo = request.FILES.get('profile_photo')

        provider.bio = bio
        provider.experience_years = experience_years
        provider.is_available = is_available

        if profile_photo:
            provider.profile_photo = profile_photo

        provider.save()

        if selected_pincodes:
            pins = ServicePincode.objects.filter(id__in=selected_pincodes)
            provider.service_pincodes.set(pins)
        else:
            provider.service_pincodes.clear()

        messages.success(request, 'Profile updated successfully.')
        return redirect('provider_profile_edit')

    return render(request, 'provider/profile_edit.html', {
        'provider': provider,
        'all_pincodes': all_pincodes,
    })


# ── Provider Toggle Availability ──────────────────────────────────────────────

@login_required(login_url='user_login')
def provider_toggle_availability(request):
    if request.user.user_type != 'provider':
        return redirect('user_login')

    provider = get_object_or_404(ProviderProfile, user=request.user)

    if request.method == 'POST':
        provider.is_available = not provider.is_available
        provider.save()
        status = 'available' if provider.is_available else 'unavailable'
        messages.success(request, f'You are now marked as {status}.')

    return redirect('provider_dashboard')


# ── Provider Notifications ────────────────────────────────────────────────────

@login_required(login_url='user_login')
def provider_notifications(request):
    if request.user.user_type != 'provider':
        return redirect('user_login')

    notifications = Notification.objects.filter(
        user=request.user
    ).order_by('-created_at')

    # Mark all as read
    notifications.filter(is_read=False).update(is_read=True)

    return render(request, 'provider/notifications.html', {
        'notifications': notifications,
    })
