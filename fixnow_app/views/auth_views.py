import secrets
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from ..models import ProviderProfile, CustomerProfile
from ..forms import CustomerRegistrationForm, LoginForm

User = get_user_model()

# ─────────────────────────────────────────
# OTP Helpers
# ─────────────────────────────────────────

def _generate_otp():
    """Return a cryptographically secure 6-digit OTP string."""
    return str(secrets.randbelow(1000000)).zfill(6)

def _send_otp_email(email, otp):
    """Send the OTP verification email."""
    from django.template.loader import render_to_string
    
    subject = "FixNow Registration - Verification Code"
    message = (
        f"Your verification code is: {otp}\n\n"
        "Please enter this code to complete your registration. "
        "This code will expire in 10 minutes.\n\n"
        "If you did not request this, please ignore this email."
    )
    
    html_message = render_to_string('accounts/otp_email.html', {'otp': otp})
    
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [email],
        fail_silently=False,
        html_message=html_message,
    )

def _clear_otp_session(request):
    """Remove all OTP / registration keys from the session."""
    for key in ('registration_data', 'otp', 'otp_created_at', 'otp_attempts'):
        if key in request.session:
            del request.session[key]


# ── Home ──────────────────────────────────────────────────────────────────────

def home(request):
    if request.user.is_authenticated and request.user.user_type == 'provider':
        return redirect('provider_dashboard')
    
    from ..models import ServiceCategory
    categories = ServiceCategory.objects.filter(is_active=True, providers__status='approved').distinct()
    return render(request, 'home.html', {'categories': categories})


# ── Service Category Detail ───────────────────────────────────────────────────

def service_category(request, slug):
    from ..models import ServiceCategory, ProviderProfile
    category = get_object_or_404(ServiceCategory, slug=slug, is_active=True)
    pincode_filter = request.GET.get('pincode', '').strip()

    providers = ProviderProfile.objects.filter(
        category=category,
        status='approved'
    ).select_related('user')

    if pincode_filter:
        providers = providers.filter(
            service_pincodes__pincode=pincode_filter
        ).distinct()

    return render(request, 'frontend/category_detail.html', {
        'category': category,
        'providers': providers,
        'pincode_filter': pincode_filter,
    })


# ── Book Provider ─────────────────────────────────────────────────────────────

@login_required(login_url='user_login')
def book_provider(request, provider_id):
    from ..models import ProviderProfile, Booking, BookingStatusLog, Notification
    from django.utils import timezone

    provider = get_object_or_404(ProviderProfile, id=provider_id, status='approved')

    if request.user.user_type != 'customer':
        return redirect('user_login')

    if request.method == 'POST':
        scheduled_date = request.POST.get('scheduled_date')
        scheduled_time = request.POST.get('scheduled_time')
        address = request.POST.get('address', '').strip()
        pincode = request.POST.get('pincode', '').strip()
        description = request.POST.get('description', '').strip()

        if not all([scheduled_date, scheduled_time, address, pincode, description]):
            messages.error(request, 'All fields are required.')
            return render(request, 'frontend/booking_form.html', {
                'provider': provider,
                'today': timezone.now().date(),
            })

        booking = Booking.objects.create(
            customer=request.user,
            provider=provider,
            category=provider.category,
            description=description,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            address=address,
            pincode=pincode,
            status='pending'
        )

        BookingStatusLog.objects.create(
            booking=booking,
            status='pending',
            changed_by=request.user,
            note='Booking created by customer.'
        )

        Notification.objects.create(
            user=provider.user,
            message=f'New booking request #{booking.id} from {request.user.get_full_name() or request.user.email}.'
        )

        return redirect('booking_confirm', booking_id=booking.id)

    from django.utils import timezone
    return render(request, 'frontend/booking_form.html', {
        'provider': provider,
        'today': timezone.now().date(),
    })


# ── Booking Confirm ───────────────────────────────────────────────────────────

@login_required(login_url='user_login')
def booking_confirm(request, booking_id):
    from ..models import Booking
    booking = get_object_or_404(Booking, id=booking_id, customer=request.user)
    return render(request, 'frontend/booking_confirm.html', {'booking': booking})


# ── Customer Dashboard ────────────────────────────────────────────────────────

@login_required(login_url='user_login')
def customer_dashboard(request):
    if request.user.user_type != 'customer':
        return redirect('user_login')

    from ..models import Booking
    status_filter = request.GET.get('status', '')
    bookings = Booking.objects.filter(
        customer=request.user
    ).select_related('provider', 'provider__user', 'category').order_by('-created_at')

    if status_filter:
        bookings = bookings.filter(status=status_filter)

    return render(request, 'frontend/customer_dashboard.html', {
        'bookings': bookings,
        'status_filter': status_filter,
    })


# ── Customer Booking Detail ───────────────────────────────────────────────────

@login_required(login_url='user_login')
def customer_booking_detail(request, booking_id):
    if request.user.user_type != 'customer':
        return redirect('user_login')

    from ..models import Booking, Review
    booking = get_object_or_404(
        Booking, id=booking_id, customer=request.user
    )
    status_logs = booking.status_logs.select_related('changed_by').all()
    has_review = Review.objects.filter(booking=booking).exists()

    return render(request, 'frontend/customer_booking_detail.html', {
        'booking': booking,
        'status_logs': status_logs,
        'has_review': has_review,
    })


# ── Submit Review ─────────────────────────────────────────────────────────────

@login_required(login_url='user_login')
def submit_review(request, booking_id):
    from ..models import Booking, Review
    from django.db.models import Avg

    booking = get_object_or_404(Booking, id=booking_id, customer=request.user)

    if booking.status != 'completed':
        messages.error(request, 'You can only review completed bookings.')
        return redirect('customer_booking_detail', booking_id=booking_id)

    if Review.objects.filter(booking=booking).exists():
        messages.error(request, 'You have already reviewed this booking.')
        return redirect('customer_booking_detail', booking_id=booking_id)

    if request.method == 'POST':
        rating = int(request.POST.get('rating', 3))
        comment = request.POST.get('comment', '').strip()

        Review.objects.create(
            booking=booking,
            customer=request.user,
            provider=booking.provider,
            rating=rating,
            comment=comment
        )

        avg = Review.objects.filter(
            provider=booking.provider
        ).aggregate(Avg('rating'))['rating__avg']
        booking.provider.avg_rating = round(avg, 2)
        booking.provider.save()

        messages.success(request, 'Thank you for your review!')

    return redirect('customer_booking_detail', booking_id=booking_id)


# ── Cancel Booking ────────────────────────────────────────────────────────────

@login_required(login_url='user_login')
def cancel_booking(request, booking_id):
    from ..models import Booking, BookingStatusLog, Notification

    booking = get_object_or_404(Booking, id=booking_id, customer=request.user)

    if request.method == 'POST':
        if booking.status in ['pending', 'confirmed']:
            booking.status = 'cancelled'
            booking.save()

            BookingStatusLog.objects.create(
                booking=booking,
                status='cancelled',
                changed_by=request.user,
                note='Cancelled by customer.'
            )

            Notification.objects.create(
                user=booking.provider.user,
                message=f'Booking #{booking.id} has been cancelled by the customer.'
            )

            messages.success(request, 'Booking cancelled successfully.')
        else:
            messages.error(request, 'This booking cannot be cancelled.')

    return redirect('customer_booking_detail', booking_id=booking_id)


# ─────────────────────────────────────────
# Shared Customer / Provider Login
# ─────────────────────────────────────────

def user_login(request):
    if request.user.is_authenticated:
        if request.user.user_type == 'provider':
            return redirect('provider_dashboard')
        return redirect('home')

    form = LoginForm(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            email = form.cleaned_data['email']
            password = form.cleaned_data['password']
            user = authenticate(request, email=email, password=password)

            if user is not None:
                if user.user_type == 'provider':
                    try:
                        profile = user.provider_profile
                    except ProviderProfile.DoesNotExist:
                        messages.error(request, 'Provider profile not found.')
                        return render(request, 'authenticate/login.html', {})

                    if profile.status == 'pending':
                        login(request, user)
                        return redirect('provider_pending')
                    elif profile.status == 'approved':
                        login(request, user)
                        return redirect('provider_dashboard')
                    elif profile.status == 'rejected':
                        messages.error(request, 'Your provider application has been rejected. Please contact support.')
                    elif profile.status == 'suspended':
                        messages.error(request, 'Your account has been suspended. Please contact support.')
                    return render(request, 'authenticate/login.html', {})

                elif user.user_type == 'customer':
                    login(request, user)
                    return redirect('home')

                else:
                    messages.error(request, 'Invalid account type.')
            else:
                messages.error(request, 'Invalid email or password.')

    return render(request, 'authenticate/login.html', {})


# ─────────────────────────────────────────
# Shared Logout
# ─────────────────────────────────────────

def user_logout(request):
    logout(request)
    return redirect('home')


# ─────────────────────────────────────────
# Customer Registration
# ─────────────────────────────────────────

def customer_register(request):
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        password = request.POST.get('password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()

        if not first_name or not email or not phone or not password:
            messages.error(request, 'First name, email, phone and password are required.')
            return render(request, 'accounts/customer_register.html', {})

        if password != confirm_password:
            messages.error(request, 'Passwords do not match.')
            return render(request, 'accounts/customer_register.html', {})

        if User.objects.filter(email=email).exists():
            messages.error(request, 'An account with this email already exists.')
            return render(request, 'accounts/customer_register.html', {})

        # Store data in session for OTP verification
        request.session['registration_data'] = {
            'email': email,
            'password': password,
            'first_name': first_name,
            'last_name': last_name,
            'phone': phone,
            'user_type': 'customer',
        }

        otp = _generate_otp()
        request.session['otp'] = otp
        request.session['otp_created_at'] = timezone.now().isoformat()
        request.session['otp_attempts'] = 0

        _send_otp_email(email, otp)
        return redirect('verify_otp')

    return render(request, 'accounts/customer_register.html', {})


# ─────────────────────────────────────────
# OTP Verification Views
# ─────────────────────────────────────────

MAX_OTP_ATTEMPTS = 5
OTP_EXPIRY_MINUTES = 10

def verify_otp(request):
    reg_data = request.session.get('registration_data')
    if not reg_data:
        messages.error(request, "No active registration session found. Please register again.")
        return redirect('home')

    email = reg_data.get('email')
    masked_email = f"{email[:2]}***@{email.split('@')[1]}"

    if request.method == 'POST':
        entered_otp = request.POST.get('otp', '').strip()
        stored_otp = request.session.get('otp')
        created_at_str = request.session.get('otp_created_at')
        attempts = request.session.get('otp_attempts', 0)

        if attempts >= MAX_OTP_ATTEMPTS:
            _clear_otp_session(request)
            messages.error(request, "Maximum OTP attempts exceeded. Please register again.")
            return redirect('home')

        try:
            created_at = timezone.datetime.fromisoformat(created_at_str)
            elapsed = (timezone.now() - created_at).total_seconds()
            if elapsed > OTP_EXPIRY_MINUTES * 60:
                messages.error(request, "OTP has expired. Please request a new one.")
                return render(request, 'accounts/verify_otp.html', {'masked_email': masked_email})
        except (ValueError, TypeError):
            messages.error(request, "Invalid session data. Please register again.")
            _clear_otp_session(request)
            return redirect('home')

        if entered_otp == stored_otp:
            # OTP Correct - Create User
            try:
                user = User.objects.create_user(
                    email=reg_data['email'],
                    password=reg_data['password'],
                    first_name=reg_data['first_name'],
                    last_name=reg_data['last_name'],
                    phone=reg_data['phone'],
                    user_type=reg_data['user_type'],
                )

                if reg_data['user_type'] == 'customer':
                    CustomerProfile.objects.create(user=user)
                    messages.success(request, 'Registration successful! You can now log in.')
                elif reg_data['user_type'] == 'provider':
                    ProviderProfile.objects.create(user=user, status='pending')
                    messages.success(request, 'Registration successful! Your account is pending approval.')

                _clear_otp_session(request)
                return redirect('user_login')

            except Exception as e:
                messages.error(request, f"An error occurred creating your account: {str(e)}")
                return render(request, 'accounts/verify_otp.html', {'masked_email': masked_email})

        else:
            # OTP Incorrect
            attempts += 1
            request.session['otp_attempts'] = attempts
            remaining = MAX_OTP_ATTEMPTS - attempts

            if remaining <= 0:
                _clear_otp_session(request)
                messages.error(request, "Maximum OTP attempts exceeded. Please register again.")
                return redirect('home')
            else:
                messages.error(request, f"Incorrect OTP. You have {remaining} attempt(s) remaining.")

    return render(request, 'accounts/verify_otp.html', {'masked_email': masked_email})


def resend_otp(request):
    if request.method == 'POST':
        reg_data = request.session.get('registration_data')
        if not reg_data:
            messages.error(request, "No active registration session found. Please register again.")
            return redirect('home')

        email = reg_data.get('email')
        otp = _generate_otp()
        
        request.session['otp'] = otp
        request.session['otp_created_at'] = timezone.now().isoformat()
        request.session['otp_attempts'] = 0

        _send_otp_email(email, otp)
        messages.success(request, "A new verification code has been sent to your email.")
        
    return redirect('verify_otp')
