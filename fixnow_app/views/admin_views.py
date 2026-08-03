from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.utils import timezone
from django.utils.text import slugify
from django.contrib.auth.decorators import login_required
from ..models import (
    CustomUser, ProviderProfile, CustomerProfile,
    ServiceCategory, ServicePincode, Booking, Review, Notification
)


# ── Admin Login ───────────────────────────────────────────────────────────────

def admin_login(request):
    if request.method == 'POST':
        email = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, email=email, password=password)
        if user and user.is_staff:
            login(request, user)
            return redirect('admin_dashboard')
        messages.error(request, 'Invalid credentials or not an admin.')
    return render(request, 'authenticate/login.html', {'admin_login': True})


# ── Admin Logout ──────────────────────────────────────────────────────────────

def admin_logout(request):
    logout(request)
    return redirect('admin_login')


# ── Admin Dashboard Home ──────────────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_dashboard(request):
    if not request.user.is_staff:
        return redirect('admin_login')

    today = timezone.now().date()

    stats = {
        'total_customers': CustomerProfile.objects.count(),
        'total_providers': ProviderProfile.objects.count(),
        'pending_approvals': ProviderProfile.objects.filter(status='pending').count(),
        'total_bookings': Booking.objects.count(),
        'bookings_today': Booking.objects.filter(created_at__date=today).count(),
        'total_categories': ServiceCategory.objects.filter(is_active=True).count(),
    }

    pending_providers = ProviderProfile.objects.filter(
        status='pending'
    ).select_related('user', 'category').order_by('-created_at')[:5]

    recent_bookings = Booking.objects.select_related(
        'customer', 'provider', 'category'
    ).order_by('-created_at')[:5]

    return render(request, 'admin_pages/dashboard.html', {
        'stats': stats,
        'pending_providers': pending_providers,
        'recent_bookings': recent_bookings,
    })


# ── Admin Provider List ───────────────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_provider_list(request):
    if not request.user.is_staff:
        return redirect('admin_login')

    status_filter = request.GET.get('status', '')
    providers = ProviderProfile.objects.select_related('user', 'category').order_by('-created_at')
    if status_filter:
        providers = providers.filter(status=status_filter)

    return render(request, 'admin_pages/provider_list.html', {
        'providers': providers,
        'status_filter': status_filter,
    })


# ── Admin Provider Detail ─────────────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_provider_detail(request, id):
    if not request.user.is_staff:
        return redirect('admin_login')

    provider = get_object_or_404(ProviderProfile, id=id)
    categories = ServiceCategory.objects.filter(is_active=True)

    return render(request, 'admin_pages/provider_detail.html', {
        'provider': provider,
        'categories': categories,
    })


# ── Admin Provider Approve ────────────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_provider_approve(request, id):
    if not request.user.is_staff:
        return redirect('admin_login')

    provider = get_object_or_404(ProviderProfile, id=id)
    if request.method == 'POST':
        provider.status = 'approved'
        provider.save()
        Notification.objects.create(
            user=provider.user,
            message='Your provider account has been approved. You can now receive bookings.'
        )
        messages.success(request, f'{provider.user.email} has been approved.')
    return redirect('admin_provider_list')


# ── Admin Provider Reject ─────────────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_provider_reject(request, id):
    if not request.user.is_staff:
        return redirect('admin_login')

    provider = get_object_or_404(ProviderProfile, id=id)
    if request.method == 'POST':
        provider.status = 'rejected'
        provider.save()
        Notification.objects.create(
            user=provider.user,
            message='Your provider application has been rejected. Please contact support for more information.'
        )
        messages.error(request, f'{provider.user.email} has been rejected.')
    return redirect('admin_provider_list')


# ── Admin Provider Suspend ────────────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_provider_suspend(request, id):
    if not request.user.is_staff:
        return redirect('admin_login')

    provider = get_object_or_404(ProviderProfile, id=id)
    if request.method == 'POST':
        provider.status = 'suspended'
        provider.save()
        Notification.objects.create(
            user=provider.user,
            message='Your provider account has been suspended. Please contact support.'
        )
        messages.warning(request, f'{provider.user.email} has been suspended.')
    return redirect('admin_provider_list')


# ── Admin Provider Delete ─────────────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_provider_delete(request, id):
    if not request.user.is_staff:
        return redirect('admin_login')

    provider = get_object_or_404(ProviderProfile, id=id)
    if request.method == 'POST':
        provider.user.delete()
        messages.success(request, 'Provider has been permanently deleted.')
    return redirect('admin_provider_list')


# ── Admin Provider Create Category & Assign ───────────────────────────────────

@login_required(login_url='admin_login')
def admin_provider_create_category(request, id):
    if not request.user.is_staff:
        return redirect('admin_login')

    provider = get_object_or_404(ProviderProfile, id=id)
    if request.method == 'POST':
        category_name = provider.custom_category_request
        if category_name:
            slug = slugify(category_name)
            base_slug = slug
            counter = 1
            while ServiceCategory.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            category = ServiceCategory.objects.create(
                name=category_name,
                slug=slug,
                is_active=True
            )
            provider.category = category
            provider.custom_category_request = None
            provider.save()
            messages.success(request, f'Category "{category_name}" created and assigned.')
    return redirect('admin_provider_detail', id=id)


# ── Admin Provider Assign Existing Category ───────────────────────────────────

@login_required(login_url='admin_login')
def admin_provider_assign_category(request, id):
    if not request.user.is_staff:
        return redirect('admin_login')

    provider = get_object_or_404(ProviderProfile, id=id)
    if request.method == 'POST':
        category_id = request.POST.get('category_id')
        category = get_object_or_404(ServiceCategory, id=category_id)
        provider.category = category
        provider.custom_category_request = None
        provider.save()
        messages.success(request, f'Category "{category.name}" assigned to provider.')
    return redirect('admin_provider_detail', id=id)


# ── Admin Customer List ───────────────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_customer_list(request):
    if not request.user.is_staff:
        return redirect('admin_login')

    customers = CustomerProfile.objects.select_related('user').order_by('-created_at')
    return render(request, 'admin_pages/customer_list.html', {
        'customers': customers,
    })


# ── Admin Customer Block / Unblock ────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_customer_block(request, id):
    if not request.user.is_staff:
        return redirect('admin_login')

    customer = get_object_or_404(CustomerProfile, id=id)
    if request.method == 'POST':
        customer.user.is_active = not customer.user.is_active
        customer.user.save()
        action = 'unblocked' if customer.user.is_active else 'blocked'
        messages.success(request, f'Customer has been {action}.')
    return redirect('admin_customer_list')


# ── Admin Booking List ────────────────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_booking_list(request):
    if not request.user.is_staff:
        return redirect('admin_login')

    status_filter = request.GET.get('status', '')
    bookings = Booking.objects.select_related(
        'customer', 'provider', 'provider__user', 'category'
    ).order_by('-created_at')
    if status_filter:
        bookings = bookings.filter(status=status_filter)

    return render(request, 'admin_pages/booking_list.html', {
        'bookings': bookings,
        'status_filter': status_filter,
    })


# ── Admin Booking Detail ──────────────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_booking_detail(request, id):
    if not request.user.is_staff:
        return redirect('admin_login')

    booking = get_object_or_404(
        Booking.objects.select_related('customer', 'provider', 'provider__user', 'category'),
        id=id
    )
    status_logs = booking.status_logs.select_related('changed_by').all()

    if request.method == 'POST':
        new_status = request.POST.get('status')
        note = request.POST.get('note', '').strip()
        valid_statuses = dict(Booking.STATUS_CHOICES)

        if new_status in valid_statuses:
            booking.status = new_status
            booking.save()

            BookingStatusLog.objects.create(
                booking=booking,
                status=new_status,
                changed_by=request.user,
                note=note or f'Status updated to {new_status} by Admin.'
            )

            Notification.objects.create(
                user=booking.customer,
                message=f'Admin updated booking #{booking.id} status to {valid_statuses[new_status]}.'
            )
            Notification.objects.create(
                user=booking.provider.user,
                message=f'Admin updated booking #{booking.id} status to {valid_statuses[new_status]}.'
            )

            messages.success(request, f'Booking status updated to {valid_statuses[new_status]}.')
            return redirect('admin_booking_detail', id=id)

    return render(request, 'admin_pages/booking_detail.html', {
        'booking': booking,
        'status_logs': status_logs,
        'status_choices': Booking.STATUS_CHOICES,
    })


# ── Admin Category List ───────────────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_category_list(request):
    if not request.user.is_staff:
        return redirect('admin_login')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            name = request.POST.get('name', '').strip()
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active') == 'on'
            if name:
                slug = slugify(name)
                base_slug = slug
                counter = 1
                while ServiceCategory.objects.filter(slug=slug).exists():
                    slug = f"{base_slug}-{counter}"
                    counter += 1
                ServiceCategory.objects.create(
                    name=name, slug=slug,
                    description=description, is_active=is_active
                )
                messages.success(request, f'Category "{name}" created.')

        elif action == 'edit':
            cat_id = request.POST.get('category_id')
            category = get_object_or_404(ServiceCategory, id=cat_id)
            category.name = request.POST.get('name', '').strip()
            category.description = request.POST.get('description', '').strip()
            category.is_active = request.POST.get('is_active') == 'on'
            category.save()
            messages.success(request, f'Category "{category.name}" updated.')

        elif action == 'delete':
            cat_id = request.POST.get('category_id')
            category = get_object_or_404(ServiceCategory, id=cat_id)
            name = category.name
            category.delete()
            messages.success(request, f'Category "{name}" deleted.')

        return redirect('admin_category_list')

    categories = ServiceCategory.objects.all().order_by('name')
    return render(request, 'admin_pages/category_list.html', {
        'categories': categories,
    })


# ── Admin Pincode List ────────────────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_pincode_list(request):
    if not request.user.is_staff:
        return redirect('admin_login')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            pincode = request.POST.get('pincode', '').strip()
            area_name = request.POST.get('area_name', '').strip()
            district = request.POST.get('district', '').strip()
            if pincode and area_name and district:
                if ServicePincode.objects.filter(pincode=pincode).exists():
                    messages.error(request, f'Pincode {pincode} already exists.')
                else:
                    ServicePincode.objects.create(
                        pincode=pincode, area_name=area_name, district=district
                    )
                    messages.success(request, f'Pincode {pincode} added.')

        elif action == 'edit':
            pin_id = request.POST.get('pincode_id')
            pin = get_object_or_404(ServicePincode, id=pin_id)
            pin.pincode = request.POST.get('pincode', '').strip()
            pin.area_name = request.POST.get('area_name', '').strip()
            pin.district = request.POST.get('district', '').strip()
            pin.save()
            messages.success(request, f'Pincode {pin.pincode} updated.')

        elif action == 'delete':
            pin_id = request.POST.get('pincode_id')
            pin = get_object_or_404(ServicePincode, id=pin_id)
            pin.delete()
            messages.success(request, f'Pincode deleted.')

        return redirect('admin_pincode_list')

    pincodes = ServicePincode.objects.all().order_by('pincode')
    return render(request, 'admin_pages/pincode_list.html', {
        'pincodes': pincodes,
    })


# ── Admin Review List ─────────────────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_review_list(request):
    if not request.user.is_staff:
        return redirect('admin_login')

    reviews = Review.objects.select_related(
        'customer', 'provider', 'provider__user', 'booking'
    ).order_by('-created_at')

    return render(request, 'admin_pages/review_list.html', {
        'reviews': reviews,
    })


# ── Admin Review Delete ───────────────────────────────────────────────────────

@login_required(login_url='admin_login')
def admin_review_delete(request, id):
    if not request.user.is_staff:
        return redirect('admin_login')

    review = get_object_or_404(Review, id=id)
    if request.method == 'POST':
        review.delete()
        messages.success(request, 'Review deleted.')
    return redirect('admin_review_list')
