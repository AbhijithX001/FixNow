from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from .models import CustomUser, ProviderProfile, ServiceCategory, ServicePincode
from ai_assistant.provider_discovery import find_providers
from ai_assistant.tool_registry import execute_tool


class DemoDataCommandTests(TestCase):
    def test_seed_is_idempotent_and_creates_eligible_demo_profiles(self):
        first = StringIO()
        call_command("seed_demo_data", stdout=first)
        first_counts = {
            "users": CustomUser.objects.filter(email__endswith="@demo.fixnow.invalid").count(),
            "providers": ProviderProfile.objects.filter(user__email__endswith="@demo.fixnow.invalid").count(),
            "categories": ServiceCategory.objects.count(),
            "pincodes": ServicePincode.objects.count(),
        }

        second = StringIO()
        call_command("seed_demo_data", stdout=second)
        second_counts = {
            "users": CustomUser.objects.filter(email__endswith="@demo.fixnow.invalid").count(),
            "providers": ProviderProfile.objects.filter(user__email__endswith="@demo.fixnow.invalid").count(),
            "categories": ServiceCategory.objects.count(),
            "pincodes": ServicePincode.objects.count(),
        }
        self.assertEqual(first_counts, second_counts)
        self.assertEqual(first_counts["users"], 7)
        self.assertEqual(first_counts["providers"], 7)
        self.assertTrue(ProviderProfile.objects.filter(
            user__email="akhil.menon@demo.fixnow.invalid",
            status="approved",
            is_available=True,
            category__slug="plumbing-services",
            service_pincodes__pincode="682001",
        ).exists())
        found = find_providers("682001", "plumbing-services")
        self.assertEqual(found["status"], "ok")
        self.assertTrue(any(item["display_name"] == "Akhil Menon" for item in found["providers"]))
        self.assertEqual(find_providers("999999", "plumbing-services")["status"], "unknown_pincode")
        tool_result = execute_tool("search_providers", {"category": "plumbing-services", "location": "682001"})
        self.assertTrue(tool_result["ok"])

    def test_dry_run_does_not_write(self):
        call_command("seed_demo_data", "--dry-run", stdout=StringIO())
        self.assertEqual(CustomUser.objects.count(), 0)
        self.assertEqual(ProviderProfile.objects.count(), 0)

    def test_seeded_provider_appears_in_marketplace_view(self):
        call_command("seed_demo_data", stdout=StringIO())
        response = Client().get(reverse("service_category", args=["plumbing-services"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Akhil Menon")
