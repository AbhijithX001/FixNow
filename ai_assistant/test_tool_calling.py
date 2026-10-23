import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from fixnow_app.models import ProviderProfile, ServiceCategory, ServicePincode, CustomUser

from .model_provider import ToolCallingClient
from .tool_registry import execute_tool


class ControlledToolTests(TestCase):
    def setUp(self):
        category = ServiceCategory.objects.create(name="Plumbing Services", slug="plumbing-services")
        pin = ServicePincode.objects.create(pincode="682001", area_name="Kochi", district="Ernakulam")
        user = CustomUser.objects.create_user(email="tool-provider@example.test", user_type="provider", first_name="Tool", last_name="Provider")
        self.provider = ProviderProfile.objects.create(user=user, category=category, status="approved", is_available=True)
        self.provider.service_pincodes.add(pin)

    def test_search_tool_returns_database_provider_only(self):
        result = execute_tool("search_providers", {"category": "plumbing-services", "location": "682001"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["providers"][0]["provider_id"], self.provider.pk)

    def test_detail_tool_revalidates_current_eligibility(self):
        result = execute_tool("get_provider_details", {"provider_id": self.provider.pk, "category": "plumbing-services", "location": "682001"})
        self.assertTrue(result["ok"])
        self.provider.status = "suspended"
        self.provider.save(update_fields=["status"])
        self.assertFalse(execute_tool("get_provider_details", {"provider_id": self.provider.pk, "category": "plumbing-services", "location": "682001"})["ok"])

    def test_unknown_tool_and_bad_arguments_are_rejected(self):
        self.assertFalse(execute_tool("delete_everything", {})["ok"])
        self.assertFalse(execute_tool("search_providers", {"category": [], "location": "682001"})["ok"])


class GeminiAdapterTests(TestCase):
    @override_settings(GEMINI_API_KEY=None)
    def test_missing_credentials_fails_closed(self):
        client = ToolCallingClient("gemini", api_key=None)
        result = client.chat_with_tools("find plumbing in 682001")
        self.assertIn("not configured", result["error"])

    @patch("ai_assistant.model_provider.ToolCallingClient._gemini_request")
    def test_tool_result_is_returned_to_model_and_final_text_is_parsed(self, request):
        request.side_effect = [
            {"candidates": [{"content": {"parts": [{"functionCall": {"name": "search_providers", "args": {"category": "plumbing-services", "location": "682001"}}}]}}]},
            {"candidates": [{"content": {"parts": [{"text": json.dumps({"intent": "provider_search", "category_slug": "plumbing-services", "pincode": "682001", "problem_summary": "Plumbing providers", "missing_fields": [], "needs_clarification": False})}]}}]},
        ]
        client = ToolCallingClient("gemini", api_key="test-key")
        result = client.chat_with_tools("find a plumber in 682001")
        self.assertEqual(result["tool_rounds"], 1)
        self.assertIn("provider_search", result["content"])
        self.assertEqual(request.call_count, 2)
        sent = request.call_args_list[1].args[0]["contents"]
        self.assertIn("functionResponse", json.dumps(sent))
