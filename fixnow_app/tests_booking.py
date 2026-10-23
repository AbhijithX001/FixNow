from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Booking, ProviderProfile, ServiceCategory, ServicePincode


User = get_user_model()


class BookingWorkflowTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            email="booking-customer@example.test",
            password="test-password-123",
            user_type="customer",
        )
        provider_user = User.objects.create_user(
            email="booking-provider@example.test",
            password="test-password-123",
            user_type="provider",
            first_name="Booking",
            last_name="Provider",
        )
        category = ServiceCategory.objects.create(
            name="Plumbing Services", slug="plumbing-services", is_active=True
        )
        self.pin = ServicePincode.objects.create(
            pincode="695001", area_name="Thiruvananthapuram City", district="Thiruvananthapuram"
        )
        self.provider = ProviderProfile.objects.create(
            user=provider_user,
            category=category,
            status="approved",
            is_available=True,
        )
        self.provider.service_pincodes.add(self.pin)
        self.client.login(email=self.customer.email, password="test-password-123")

    def payload(self, **overrides):
        data = {
            "scheduled_date": (timezone.localdate() + timedelta(days=1)).isoformat(),
            "scheduled_time": "10:30",
            "address": "Flat 2B, Main Road",
            "pincode": "695001",
            "description": "Kitchen tap is leaking.",
        }
        data.update(overrides)
        return data

    def test_valid_booking_uses_existing_workflow(self):
        response = self.client.post(
            reverse("book_provider", args=[self.provider.pk]),
            data=self.payload(),
        )

        self.assertEqual(response.status_code, 302)
        booking = Booking.objects.get()
        self.assertEqual(booking.customer, self.customer)
        self.assertEqual(booking.provider, self.provider)
        self.assertEqual(booking.status, "pending")
        self.assertEqual(booking.status_logs.count(), 1)
        self.assertEqual(self.provider.user.notifications.count(), 1)

    def test_invalid_date_and_uncovered_pin_do_not_create_booking(self):
        past = timezone.localdate() - timedelta(days=1)
        response = self.client.post(
            reverse("book_provider", args=[self.provider.pk]),
            data=self.payload(scheduled_date=past.isoformat()),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Booking.objects.count(), 0)

        response = self.client.post(
            reverse("book_provider", args=[self.provider.pk]),
            data=self.payload(pincode="676501"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Booking.objects.count(), 0)

    def test_repeated_identical_submission_does_not_duplicate_booking(self):
        url = reverse("book_provider", args=[self.provider.pk])
        data = self.payload()
        first = self.client.post(url, data=data)
        second = self.client.post(url, data=data)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(self.provider.user.notifications.count(), 1)

    def test_unauthenticated_customer_booking_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("book_provider", args=[self.provider.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
