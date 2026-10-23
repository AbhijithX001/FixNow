import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from fixnow_app.models import Booking, ProviderProfile, ServiceCategory, ServicePincode

from .provider_discovery import (
    find_providers,
    list_available_categories,
    list_covered_pincodes,
    resolve_location,
)


User = get_user_model()


class ProviderDiscoveryTest(TestCase):
    def setUp(self):
        self.plumbing = ServiceCategory.objects.create(
            name="Plumbing Services", slug="plumbing-services", is_active=True
        )
        self.electrical = ServiceCategory.objects.create(
            name="Electrical Services", slug="electrical-services", is_active=True
        )
        self.home_cleaning = ServiceCategory.objects.create(
            name="Home Cleaning", slug="home-cleaning", is_active=True
        )
        self.house_cleaning = ServiceCategory.objects.create(
            name="House Cleaning", slug="house-cleaning", is_active=True
        )
        self.inactive = ServiceCategory.objects.create(
            name="Painting", slug="painting", is_active=False
        )
        self.covered = ServicePincode.objects.create(
            pincode="695001", area_name="Thiruvananthapuram City", district="Thiruvananthapuram"
        )
        self.other_coverage = ServicePincode.objects.create(
            pincode="678541", area_name="Puthiyankam", district="Palakkad"
        )
        self.uncovered = ServicePincode.objects.create(
            pincode="678542", area_name="Example", district="Palakkad"
        )

    def make_provider(self, category, *, status="approved", available=True, name="Test Provider"):
        user = User.objects.create_user(
            email=f"{name.lower().replace(' ', '-')}@example.test",
            password="test-password-123",
            user_type="provider",
            first_name=name.split()[0],
            last_name=name.split()[-1],
        )
        return ProviderProfile.objects.create(
            user=user,
            category=category,
            status=status,
            is_available=available,
        )

    def test_only_approved_available_active_category_matches(self):
        eligible = self.make_provider(self.plumbing, name="Eligible Plumber")
        eligible.service_pincodes.add(self.covered)
        pending = self.make_provider(self.plumbing, status="pending", name="Pending Plumber")
        pending.service_pincodes.add(self.covered)
        unavailable = self.make_provider(self.plumbing, available=False, name="Unavailable Plumber")
        unavailable.service_pincodes.add(self.covered)
        rejected = self.make_provider(self.plumbing, status="rejected", name="Rejected Plumber")
        rejected.service_pincodes.add(self.covered)
        inactive = self.make_provider(self.inactive, name="Inactive Provider")
        inactive.service_pincodes.add(self.covered)
        incomplete = self.make_provider(None, name="Incomplete Provider")
        incomplete.service_pincodes.add(self.covered)

        result = find_providers("695001", "plumbing-services")

        self.assertEqual(result["status"], "ok")
        self.assertEqual([p["display_name"] for p in result["providers"]], ["Eligible Plumber"])
        self.assertNotIn("@", result["providers"][0]["display_name"])

    def test_unknown_and_configured_uncovered_pins_are_distinct(self):
        self.assertEqual(find_providers("999999")["status"], "unknown_pincode")
        self.assertEqual(find_providers("678542")["status"], "configured_uncovered")

    def test_area_name_and_equivalent_numeric_pin_resolve_to_same_provider(self):
        tirur = ServicePincode.objects.create(
            pincode="676501", area_name="Tirur", district="Malappuram"
        )
        electrical = ServiceCategory.objects.create(
            name="Electrical", slug="electrical", is_active=True
        )
        provider = self.make_provider(electrical, name="Tirur Electrician")
        provider.service_pincodes.add(tirur)

        by_area = find_providers("Tirur", "electrical-services")
        by_pin = find_providers("676501", "electrical-services")

        self.assertEqual(by_area["status"], "ok")
        self.assertEqual(by_area["pincode"], "676501")
        self.assertEqual(by_area["providers"], by_pin["providers"])
        self.assertEqual(resolve_location("Tirur")["pincode"], "676501")

    def test_unknown_area_does_not_invent_a_pin(self):
        result = find_providers("Atlantis", "plumbing-services")

        self.assertEqual(result["status"], "unknown_location")
        self.assertEqual(result["location"], "Atlantis")
        self.assertNotIn("pincode", result)

    def test_ambiguous_area_requires_clarification(self):
        ServicePincode.objects.create(
            pincode="100001", area_name="Twin Area", district="North"
        )
        ServicePincode.objects.create(
            pincode="200001", area_name="Twin Area", district="South"
        )

        result = resolve_location("Twin Area")

        self.assertEqual(result["status"], "location_ambiguous")
        self.assertEqual({item["pincode"] for item in result["matches"]}, {"100001", "200001"})

    def test_valid_area_without_requested_category_reports_no_match(self):
        electrical = ServiceCategory.objects.create(
            name="Electrical", slug="electrical", is_active=True
        )
        provider = self.make_provider(electrical, name="Tirur Electrician")
        tirur = ServicePincode.objects.create(
            pincode="676501", area_name="Tirur", district="Malappuram"
        )
        provider.service_pincodes.add(tirur)

        result = find_providers("Tirur", "plumbing-services")

        self.assertEqual(result["status"], "no_category_match")
        self.assertEqual(result["providers"], [])

    def test_other_category_coverage_is_reported_for_no_match(self):
        provider = self.make_provider(self.electrical, name="Electrician")
        provider.service_pincodes.add(self.other_coverage)

        result = find_providers("678541", "plumbing-services")

        self.assertEqual(result["status"], "no_category_match")
        self.assertEqual(result["providers"], [])
        self.assertEqual(result["other_categories"], [{"name": "Electrical Services", "slug": "electrical-services"}])

    def test_category_fragment_does_not_merge_ambiguous_categories(self):
        result = find_providers("695001", "cleaning")

        self.assertEqual(result["status"], "category_ambiguous")
        self.assertEqual(
            {item["slug"] for item in result["categories"]},
            {"home-cleaning", "house-cleaning"},
        )

    def test_available_category_and_area_lists_are_database_backed(self):
        plumber = self.make_provider(self.plumbing, name="Plumber")
        plumber.service_pincodes.add(self.covered)

        categories = list_available_categories()
        areas = list_covered_pincodes("plumbing-services")

        self.assertEqual(categories["status"], "ok")
        self.assertEqual(categories["categories"], [{"name": "Plumbing Services", "slug": "plumbing-services", "provider_count": 1}])
        self.assertEqual(areas["pincodes"][0]["pincode"], "695001")


class ProviderDiscoveryChatIntegrationTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="discovery-customer@example.test",
            password="test-password-123",
            user_type="customer",
        )
        category = ServiceCategory.objects.create(
            name="Plumbing Services", slug="plumbing-services", is_active=True
        )
        pin = ServicePincode.objects.create(
            pincode="695001", area_name="Thiruvananthapuram City", district="Thiruvananthapuram"
        )
        provider_user = User.objects.create_user(
            email="provider@example.test",
            password="test-password-123",
            user_type="provider",
            first_name="Maya",
            last_name="Plumber",
        )
        provider = ProviderProfile.objects.create(
            user=provider_user, category=category, status="approved", is_available=True
        )
        provider.service_pincodes.add(pin)

        self.tirur = ServicePincode.objects.create(
            pincode="676501", area_name="Tirur", district="Malappuram"
        )
        self.electrical = ServiceCategory.objects.create(
            name="Electrical", slug="electrical", is_active=True
        )
        tirur_user = User.objects.create_user(
            email="tirur-provider@example.test",
            password="test-password-123",
            user_type="provider",
            first_name="Tirur",
            last_name="Electrician",
        )
        self.tirur_provider = ProviderProfile.objects.create(
            user=tirur_user, category=self.electrical, status="approved", is_available=True
        )
        self.tirur_provider.service_pincodes.add(self.tirur)

    @patch("ai_assistant.views.get_client")
    def test_provider_search_response_contains_only_database_result(self, mock_client):
        mock_client.return_value.chat.return_value = {
            "content": "",
            "parsed": {
                "intent": "provider_search",
                "category_slug": "plumbing-services",
                "problem_summary": "Find plumbers",
                "pincode": "695001",
                "missing_fields": [],
                "needs_clarification": False,
            },
            "elapsed": 0.1,
            "tok_s": 1.0,
            "error": None,
        }
        self.client.login(email=self.user.email, password="test-password-123")

        response = self.client.post(
            reverse("ai_assistant:chat"),
            data=json.dumps({"message": "List plumbers in 695001."}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["provider_discovery"]["status"], "ok")
        self.assertEqual(
            data["provider_discovery"]["providers"][0]["display_name"],
            "Maya Plumber",
        )
        self.assertIn("Maya Plumber", data["assistant_message"])

    @patch("ai_assistant.views.get_client")
    def test_electrical_provider_search_resolves_tirur_area(self, mock_client):
        mock_client.return_value.chat.return_value = {
            "content": "",
            "parsed": {
                "intent": "provider_search",
                "category_slug": "electrical-services",
                "problem_summary": "Electrical providers in Tirur",
                "pincode": None,
                "missing_fields": [],
                "needs_clarification": False,
            },
            "elapsed": 0.1,
            "tok_s": 1.0,
            "error": None,
        }
        self.client.login(email=self.user.email, password="test-password-123")

        response = self.client.post(
            reverse("ai_assistant:chat"),
            data=json.dumps({"message": "List electrical providers over Tirur."}),
            content_type="application/json",
        )

        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["provider_discovery"]["status"], "ok")
        self.assertEqual(data["provider_discovery"]["pincode"], "676501")
        self.assertEqual(
            data["provider_discovery"]["providers"][0]["display_name"],
            "Tirur Electrician",
        )

    @patch("ai_assistant.views.get_client")
    def test_leaking_tap_then_pin_follow_up_keeps_service_context(self, mock_client):
        mock_client.return_value.chat.side_effect = [
            {
                "content": "",
                "parsed": {
                    "intent": "service_request",
                    "category_slug": "plumbing-services",
                    "problem_summary": "Leaking kitchen tap",
                    "pincode": None,
                    "missing_fields": ["pincode"],
                    "needs_clarification": True,
                },
                "elapsed": 0.1,
                "tok_s": 1.0,
                "error": None,
            },
            {
                "content": "",
                "parsed": {
                    "intent": "needs_clarification",
                    "category_slug": None,
                    "problem_summary": "Location supplied",
                    "pincode": "695001",
                    "missing_fields": [],
                    "needs_clarification": True,
                },
                "elapsed": 0.1,
                "tok_s": 1.0,
                "error": None,
            },
        ]
        self.client.login(email=self.user.email, password="test-password-123")

        first = self.client.post(
            reverse("ai_assistant:chat"),
            data=json.dumps({"message": "My kitchen tap is leaking."}),
            content_type="application/json",
        )
        first_data = first.json()
        self.assertEqual(first_data["category_slug"], "plumbing-services")
        self.assertEqual(first_data["pincode"], None)

        history = [
            {"role": "user", "content": "My kitchen tap is leaking."},
            {
                "role": "assistant",
                "content": "I still need your PIN code.",
                "context": {
                    "intent": first_data["intent"],
                    "category_slug": first_data["category_slug"],
                    "pincode": first_data["pincode"],
                    "problem_summary": first_data["problem_summary"],
                },
            },
        ]
        second = self.client.post(
            reverse("ai_assistant:chat"),
            data=json.dumps({"message": "I live in 695001.", "history": history}),
            content_type="application/json",
        )

        data = second.json()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(data["intent"], "service_request")
        self.assertEqual(data["category_slug"], "plumbing-services")
        self.assertEqual(data["pincode"], "695001")
        self.assertEqual(data["provider_discovery"]["status"], "ok")
        self.assertNotIn("no specific service", data["assistant_message"].lower())

    @patch("ai_assistant.views.get_client")
    def test_follow_up_pin_without_plumbing_coverage_is_honest(self, mock_client):
        pin = ServicePincode.objects.create(
            pincode="678541", area_name="Puthiyankam", district="Palakkad"
        )
        electrical = ServiceCategory.objects.create(
            name="Electrical Services", slug="electrical-services", is_active=True
        )
        provider_user = User.objects.create_user(
            email="electrical-provider@example.test",
            password="test-password-123",
            user_type="provider",
            first_name="Electric",
            last_name="Provider",
        )
        provider = ProviderProfile.objects.create(
            user=provider_user, category=electrical, status="approved", is_available=True
        )
        provider.service_pincodes.add(pin)
        mock_client.return_value.chat.return_value = {
            "content": "",
            "parsed": {
                "intent": "needs_clarification",
                "category_slug": None,
                "problem_summary": "Location supplied",
                "pincode": "678541",
                "missing_fields": [],
                "needs_clarification": True,
            },
            "elapsed": 0.1,
            "tok_s": 1.0,
            "error": None,
        }
        self.client.login(email=self.user.email, password="test-password-123")
        response = self.client.post(
            reverse("ai_assistant:chat"),
            data=json.dumps({
                "message": "I am in 678541.",
                "history": [{
                    "role": "assistant",
                    "content": "Please share your PIN.",
                    "context": {
                        "intent": "service_request",
                        "category_slug": "plumbing-services",
                        "pincode": None,
                        "problem_summary": "Leaking kitchen tap",
                    },
                }],
            }),
            content_type="application/json",
        )

        data = response.json()
        self.assertEqual(data["provider_discovery"]["status"], "no_category_match")
        self.assertEqual(data["provider_discovery"]["providers"], [])
        self.assertIn("Electrical Services", data["assistant_message"])

    @patch("ai_assistant.views.get_client")
    def test_yes_executes_one_pending_provider_search_and_consumes_it(self, mock_client):
        self.client.login(email=self.user.email, password="test-password-123")
        history = [{
            "role": "assistant",
            "content": "I can look up electrical providers in Tirur.",
            "context": {
                "intent": "provider_search",
                "category_slug": "electrical",
                "pincode": "676501",
                "problem_summary": "Electrical providers in Tirur",
                "pending_action": {
                    "action": "provider_search",
                    "category_slug": "electrical",
                    "pincode": "676501",
                    "area_name": "Tirur",
                },
            },
        }]

        response = self.client.post(
            reverse("ai_assistant:chat"),
            data=json.dumps({"message": "yes", "history": history}),
            content_type="application/json",
        )

        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["provider_discovery"]["status"], "ok")
        self.assertEqual(data["provider_discovery"]["pincode"], "676501")
        self.assertIsNone(data["pending_action"])
        mock_client.assert_not_called()

    @patch("ai_assistant.views.get_client")
    def test_repeated_yes_has_no_consumed_action_to_execute(self, mock_client):
        self.client.login(email=self.user.email, password="test-password-123")
        history = [{
            "role": "assistant",
            "content": "I found the providers.",
            "context": {
                "intent": "provider_search",
                "category_slug": "electrical-services",
                "pincode": "676501",
                "problem_summary": "Electrical providers in Tirur",
                "pending_action": None,
            },
        }]

        response = self.client.post(
            reverse("ai_assistant:chat"),
            data=json.dumps({"message": "yes", "history": history}),
            content_type="application/json",
        )

        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(data["provider_discovery"])
        self.assertEqual(data["assistant_message"], "What would you like me to confirm?")
        mock_client.assert_not_called()

    @patch("ai_assistant.views.get_client")
    def test_stale_pending_action_is_not_revived(self, mock_client):
        self.client.login(email=self.user.email, password="test-password-123")
        history = [
            {
                "role": "assistant",
                "content": "Old provider results.",
                "context": {
                    "pending_action": {
                        "action": "provider_search",
                        "category_slug": "electrical",
                        "pincode": "676501",
                        "area_name": "Tirur",
                    },
                },
            },
            {"role": "user", "content": "Actually, I changed my mind."},
            {"role": "assistant", "content": "Okay, what would you like to do?", "context": {}},
        ]

        response = self.client.post(
            reverse("ai_assistant:chat"),
            data=json.dumps({"message": "yes", "history": history}),
            content_type="application/json",
        )

        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(data["provider_discovery"])
        self.assertEqual(data["assistant_message"], "What would you like me to confirm?")
        mock_client.assert_not_called()

    @patch("ai_assistant.views.get_client")
    def test_category_change_replaces_previous_provider_search(self, mock_client):
        mock_client.return_value.chat.return_value = {
            "content": "",
            "parsed": {
                "intent": "provider_search",
                "category_slug": "plumbing-services",
                "problem_summary": "Plumbers in Thiruvananthapuram City",
                "pincode": "695001",
                "missing_fields": [],
                "needs_clarification": False,
            },
            "elapsed": 0.1,
            "tok_s": 1.0,
            "error": None,
        }
        self.client.login(email=self.user.email, password="test-password-123")
        history = [{
            "role": "assistant",
            "content": "Electrical providers in Tirur.",
            "context": {
                "intent": "provider_search",
                "category_slug": "electrical",
                "pincode": "676501",
                "pending_action": {
                    "action": "provider_search",
                    "category_slug": "electrical",
                    "pincode": "676501",
                    "area_name": "Tirur",
                },
            },
        }]

        response = self.client.post(
            reverse("ai_assistant:chat"),
            data=json.dumps({
                "message": "Actually, show plumbers in 695001 instead.",
                "history": history,
            }),
            content_type="application/json",
        )

        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["provider_discovery"]["status"], "ok")
        self.assertEqual(data["provider_discovery"]["category"]["slug"], "plumbing-services")
        self.assertEqual(data["pending_action"]["category_slug"], "plumbing-services")

    @patch("ai_assistant.views.get_client")
    def test_explicit_booking_request_hands_off_to_existing_booking_form(self, mock_client):
        self.client.login(email=self.user.email, password="test-password-123")
        history = [{
            "role": "assistant",
            "content": "I found one eligible electrical provider in Tirur: Tirur Electrician.",
            "context": {
                "intent": "provider_search",
                "category_slug": "electrical",
                "pincode": "676501",
                "problem_summary": "Electrical providers in Tirur",
                "pending_action": {
                    "action": "provider_search",
                    "category_slug": "electrical",
                    "pincode": "676501",
                    "area_name": "Tirur",
                },
            },
        }]

        response = self.client.post(
            reverse("ai_assistant:chat"),
            data=json.dumps({
                "message": "yeah fine, can you book for me?",
                "history": history,
            }),
            content_type="application/json",
        )

        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["intent"], "booking_help")
        self.assertEqual(data["provider_discovery"]["status"], "ok")
        self.assertEqual(data["booking_url"], reverse("book_provider", args=[self.tirur_provider.pk]))
        self.assertIn("Tirur Electrician", data["assistant_message"])
        self.assertIsNone(data["pending_action"])
        self.assertEqual(Booking.objects.count(), 0)
        mock_client.assert_not_called()

    @patch("ai_assistant.views.get_client")
    def test_named_provider_booking_request_selects_only_discovered_provider(self, mock_client):
        self.client.login(email=self.user.email, password="test-password-123")
        history = [{
            "role": "assistant",
            "content": "I found one eligible electrical provider in Tirur: Tirur Electrician.",
            "context": {
                "intent": "provider_search",
                "category_slug": "electrical",
                "pincode": "676501",
                "problem_summary": "Electrical providers in Tirur",
                "pending_action": {
                    "action": "provider_search",
                    "category_slug": "electrical",
                    "pincode": "676501",
                    "area_name": "Tirur",
                },
            },
        }]

        response = self.client.post(
            reverse("ai_assistant:chat"),
            data=json.dumps({
                "message": "Book Tirur Electrician tomorrow.",
                "history": history,
            }),
            content_type="application/json",
        )

        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["booking_url"], reverse("book_provider", args=[self.tirur_provider.pk]))
        self.assertEqual(data["missing_fields"], ["preferred_date", "preferred_time", "address", "description"])
        mock_client.assert_not_called()

    @patch("ai_assistant.views.get_client")
    def test_booking_request_without_provider_selection_does_not_create_booking(self, mock_client):
        self.client.login(email=self.user.email, password="test-password-123")

        response = self.client.post(
            reverse("ai_assistant:chat"),
            data=json.dumps({"message": "Can you book a professional for me?"}),
            content_type="application/json",
        )

        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["intent"], "booking_help")
        self.assertIsNone(data["booking_url"])
        self.assertIsNone(data["provider_discovery"])
        self.assertEqual(Booking.objects.count(), 0)
        mock_client.assert_not_called()

    @patch("ai_assistant.views.get_client")
    def test_cancellation_clears_pending_provider_action(self, mock_client):
        self.client.login(email=self.user.email, password="test-password-123")
        history = [{
            "role": "assistant",
            "content": "I found the provider.",
            "context": {
                "pending_action": {
                    "action": "provider_search",
                    "category_slug": "electrical",
                    "pincode": "676501",
                    "area_name": "Tirur",
                },
            },
        }]

        response = self.client.post(
            reverse("ai_assistant:chat"),
            data=json.dumps({"message": "Never mind, cancel that." , "history": history}),
            content_type="application/json",
        )

        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(data["pending_action"])
        self.assertIn("cancelled", data["assistant_message"].lower())
        mock_client.assert_not_called()
