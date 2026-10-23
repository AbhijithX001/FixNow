"""
FixNow AI Assistant — Test Suite

Covers:
  - Safety guard unit tests (no network required)
  - Validator unit tests (no network required)
  - Ollama client health_check() and chat() with mocked network
  - Django view integration tests (TestClient, mocked Ollama)

Run with:
    python manage.py test ai_assistant
"""

import json
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .ollama_client import MAX_OLLAMA_RESPONSE_BYTES, OllamaClient
from .safety_guard import check_safety_hazard
from .validator import (
    ALIAS_TO_CANONICAL,
    CANONICAL_CATEGORIES,
    compute_missing_fields,
    extract_valid_iso_date,
    extract_pincode_deterministically,
    is_raw_schema_valid,
    normalize_output,
)

User = get_user_model()

# ─── Safety Guard Tests ───────────────────────────────────────────────────────


class SafetyGuardTest(TestCase):
    """Deterministic hazard detection — no LLM, no network."""

    HAZARD_CASES = [
        ("I smell strong cooking gas in my kitchen and hear a loud hissing noise from the cylinder.", "gas_leak"),
        ("Sparks and black smoke are shooting out of my main electrical meter box right now!", "electrical_fire_sparks"),
        ("A major main water pipe burst inside the wall and water is rapidly flooding my living room.", "structural_flooding"),
        ("There is a burning smell coming from the bedroom electrical wiring.", "electrical_fire_sparks"),
        ("LPG gas leak in kitchen, smell is everywhere.", "gas_leak"),
        ("I got an electric shock from the socket.", "electrical_fire_sparks"),
        ("There is a fire in my kitchen.", "electrical_fire_sparks"),
        ("Water is flooding my bathroom and rising quickly.", "structural_flooding"),
        ("The ceiling is about to collapse.", "structural_collapse"),
        ("Sparks are coming from the wall socket.", "electrical_fire_sparks"),
    ]

    NON_HAZARD_CASES = [
        "Kitchen tap is dripping slowly.",
        "Need to clean gas stove burners.",
        "Ceiling fan not working in living room.",
        "Need an electrician to install a light fixture.",
        "Water purifier filter replacement needed.",
        "Bathroom drain is clogged.",
        "Switchboard has a loose button.",
        "I want to install a decorative electric fireplace.",
        "Please clean the water stains from my bathroom floor.",
        "The ceiling has a small cosmetic crack from old paint.",
    ]

    def test_true_hazards_detected(self):
        for msg, expected_type in self.HAZARD_CASES:
            with self.subTest(msg=msg[:60]):
                result = check_safety_hazard(msg)
                self.assertTrue(result["is_hazard"], f"Expected hazard for: {msg!r}")
                self.assertEqual(result["hazard_type"], expected_type)
                self.assertEqual(result["intent"], "emergency_safety_alert")
                self.assertFalse(result["booking_allowed"])
                self.assertIn("safety_guidance", result)
                self.assertIn("emergency_contacts", result)

    def test_non_hazards_pass_through(self):
        for msg in self.NON_HAZARD_CASES:
            with self.subTest(msg=msg[:60]):
                result = check_safety_hazard(msg)
                self.assertFalse(result["is_hazard"], f"False positive for: {msg!r}")
                self.assertTrue(result["booking_allowed"])

    def test_case_insensitive(self):
        result = check_safety_hazard("LPG GAS LEAK IN MY KITCHEN")
        self.assertTrue(result["is_hazard"])
        self.assertEqual(result["hazard_type"], "gas_leak")

    def test_empty_message_is_safe(self):
        result = check_safety_hazard("")
        self.assertFalse(result["is_hazard"])


# ─── Validator Tests ──────────────────────────────────────────────────────────


class ValidatorSchemaTest(TestCase):
    """is_raw_schema_valid() and normaliser edge cases."""

    def _valid_doc(self, **overrides):
        base = {
            "intent": "service_request",
            "category_slug": "plumbing-services",
            "problem_summary": "Tap dripping",
            "pincode": None,
            "missing_fields": ["pincode"],
            "needs_clarification": True,
        }
        base.update(overrides)
        return base

    def test_valid_schema_passes(self):
        ok, err = is_raw_schema_valid(self._valid_doc())
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_missing_key_fails(self):
        doc = self._valid_doc()
        del doc["pincode"]
        ok, err = is_raw_schema_valid(doc)
        self.assertFalse(ok)
        self.assertIn("pincode", err)

    def test_invalid_intent_fails(self):
        ok, err = is_raw_schema_valid(self._valid_doc(intent="plumbing-services"))
        self.assertFalse(ok)
        self.assertIn("intent", err)

    def test_invalid_category_fails(self):
        ok, err = is_raw_schema_valid(self._valid_doc(category_slug="solar-panels"))
        self.assertFalse(ok)
        self.assertIn("category_slug", err)

    def test_malformed_pincode_fails(self):
        ok, err = is_raw_schema_valid(self._valid_doc(pincode="123"))
        self.assertFalse(ok)

    def test_null_pincode_passes(self):
        ok, _ = is_raw_schema_valid(self._valid_doc(pincode=None))
        self.assertTrue(ok)


class PincodeExtractionTest(TestCase):
    def test_extracts_valid_pin(self):
        self.assertEqual(extract_pincode_deterministically("My pin is 682001"), "682001")

    def test_rejects_short_number(self):
        self.assertIsNone(extract_pincode_deterministically("Call 112 for help"))

    def test_rejects_zero_leading(self):
        self.assertIsNone(extract_pincode_deterministically("area code 012345"))

    def test_no_pin_returns_none(self):
        self.assertIsNone(extract_pincode_deterministically("Fix my tap please"))


class MissingFieldsTest(TestCase):
    def test_non_service_request_returns_empty(self):
        result = compute_missing_fields("provider_search", None, None, "test")
        self.assertEqual(result, [])

    def test_service_request_full_missing(self):
        result = compute_missing_fields("service_request", None, None, "fix tap")
        self.assertIn("category", result)
        self.assertIn("pincode", result)
        self.assertIn("preferred_date", result)
        self.assertIn("preferred_time", result)
        self.assertIn("address", result)

    def test_service_request_with_iso_date(self):
        msg = "fix tap on 2026-10-21"
        result = compute_missing_fields(
            "service_request", "plumbing-services", "682001", msg,
            reference_date=date(2026, 10, 20),
        )
        self.assertNotIn("preferred_date", result)

    def test_impossible_iso_date_is_missing(self):
        self.assertIsNone(extract_valid_iso_date("fix tap on 2026-02-30"))
        result = compute_missing_fields(
            "service_request", "plumbing-services", "682001",
            "fix tap on 2026-02-30",
            reference_date=date(2026, 1, 1),
        )
        self.assertIn("preferred_date", result)

    def test_past_iso_date_is_missing(self):
        result = compute_missing_fields(
            "service_request", "plumbing-services", "682001",
            "fix tap on 2026-10-19",
            reference_date=date(2026, 10, 20),
        )
        self.assertIn("preferred_date", result)

    def test_service_request_with_time(self):
        msg = "fix tap at 10:00 AM, flat 5"
        result = compute_missing_fields("service_request", "plumbing-services", "682001", msg)
        self.assertNotIn("preferred_time", result)
        self.assertNotIn("address", result)


class NormalizeOutputTest(TestCase):
    def test_unparseable_returns_safe_fallback(self):
        result = normalize_output(None, "help me")
        self.assertEqual(result["intent"], "needs_clarification")
        self.assertTrue(result["normalization_applied"])
        self.assertTrue(result["post_schema_valid"])

    def test_alias_category_normalised(self):
        raw = {
            "intent": "service_request",
            "category_slug": "plumbing",  # alias
            "problem_summary": "Dripping tap",
            "pincode": None,
            "missing_fields": [],
            "needs_clarification": False,
        }
        result = normalize_output(raw, "dripping tap")
        self.assertEqual(result["category_slug"], "plumbing-services")
        self.assertTrue(result["normalization_applied"])

    def test_category_in_intent_field_normalised(self):
        raw = {
            "intent": "plumbing-services",  # wrong field
            "category_slug": None,
            "problem_summary": "Pipe leak",
            "pincode": None,
            "missing_fields": [],
            "needs_clarification": False,
        }
        result = normalize_output(raw, "pipe leak")
        self.assertEqual(result["intent"], "service_request")
        self.assertEqual(result["category_slug"], "plumbing-services")

    def test_pincode_extracted_when_missing(self):
        raw = {
            "intent": "service_request",
            "category_slug": "plumbing-services",
            "problem_summary": "Dripping tap",
            "pincode": None,
            "missing_fields": [],
            "needs_clarification": False,
        }
        result = normalize_output(raw, "fix tap in 682001")
        self.assertEqual(result["pincode"], "682001")

    def test_malformed_pincode_replaced(self):
        raw = {
            "intent": "service_request",
            "category_slug": "plumbing-services",
            "problem_summary": "Dripping tap",
            "pincode": "ABC",
            "missing_fields": [],
            "needs_clarification": False,
        }
        result = normalize_output(raw, "fix tap in 682001")
        self.assertEqual(result["pincode"], "682001")
        self.assertTrue(result["normalization_applied"])


# ─── Ollama Client Tests (mocked network) ─────────────────────────────────────


class OllamaClientHealthTest(TestCase):
    """Test OllamaClient.health_check() without a live Ollama server."""

    def _make_client(self):
        return OllamaClient(base_url="http://localhost:11434", model="qwen2.5:3b")

    @patch("ai_assistant.ollama_client.urllib.request.urlopen")
    def test_health_check_ok(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.headers = {}
        mock_resp.read.return_value = json.dumps(
            {"models": [{"name": "qwen2.5:3b"}]}
        ).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        client = self._make_client()
        result = client.health_check()

        self.assertTrue(result["ok"])
        self.assertTrue(result["model_available"])
        self.assertIsNone(result["error"])

    @patch("ai_assistant.ollama_client.urllib.request.urlopen")
    def test_health_check_model_not_found(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.headers = {}
        mock_resp.read.return_value = json.dumps({"models": []}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        client = self._make_client()
        result = client.health_check()

        self.assertTrue(result["ok"])
        self.assertFalse(result["model_available"])

    @patch("ai_assistant.ollama_client.urllib.request.urlopen")
    def test_health_check_unreachable(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        client = self._make_client()
        result = client.health_check()

        self.assertFalse(result["ok"])
        self.assertIn("error", result)


class OllamaClientChatTest(TestCase):
    """Test OllamaClient.chat() without a live Ollama server."""

    def _make_mock_ollama_response(self, content: str):
        envelope = {
            "model": "qwen2.5:3b",
            "message": {"role": "assistant", "content": content},
            "eval_count": 50,
            "eval_duration": 1_000_000_000,
        }
        mock_resp = MagicMock()
        mock_resp.headers = {}
        mock_resp.read.return_value = json.dumps(envelope).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    @patch("ai_assistant.ollama_client.urllib.request.urlopen")
    def test_chat_returns_parsed_json(self, mock_urlopen):
        payload = json.dumps({
            "intent": "service_request",
            "category_slug": "plumbing-services",
            "problem_summary": "Dripping tap",
            "pincode": None,
            "missing_fields": ["pincode"],
            "needs_clarification": True,
        })
        mock_urlopen.return_value = self._make_mock_ollama_response(payload)

        client = OllamaClient()
        result = client.chat("My kitchen tap is dripping")

        self.assertIsNone(result["error"])
        self.assertIsNotNone(result["parsed"])
        self.assertEqual(result["parsed"]["intent"], "service_request")
        self.assertGreater(result["tok_s"], 0)

    @patch("ai_assistant.ollama_client.urllib.request.urlopen")
    def test_chat_handles_invalid_json_from_model(self, mock_urlopen):
        mock_urlopen.return_value = self._make_mock_ollama_response("not json at all")

        client = OllamaClient()
        result = client.chat("My kitchen tap is dripping")

        self.assertIsNone(result["error"])
        self.assertIsNone(result["parsed"])
        self.assertEqual(result["content"], "not json at all")

    @patch("ai_assistant.ollama_client.urllib.request.urlopen")
    def test_chat_handles_malformed_response_envelope(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.headers = {}
        mock_resp.read.return_value = b"[]"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = OllamaClient().chat("My kitchen tap is dripping")

        self.assertIsNone(result["parsed"])
        self.assertIn("invalid response envelope", result["error"])

    @patch("ai_assistant.ollama_client.urllib.request.urlopen")
    def test_chat_handles_network_error(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        client = OllamaClient()
        result = client.chat("My kitchen tap is dripping")

        self.assertIsNotNone(result["error"])
        self.assertIn("Network error", result["error"])

    @patch("ai_assistant.ollama_client.urllib.request.urlopen")
    def test_chat_rejects_oversized_response(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.headers = {}
        mock_resp.read.return_value = b"x" * (MAX_OLLAMA_RESPONSE_BYTES + 1)
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = OllamaClient().chat("My kitchen tap is dripping")

        self.assertIsNone(result["parsed"])
        self.assertIn("size limit", result["error"])


# ─── Django View Integration Tests ────────────────────────────────────────────


class HealthViewTest(TestCase):
    """GET /ai/health/ — customer-only authenticated endpoint."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="customer@test.com",
            password="testpassword123",
            user_type="customer",
        )
        self.provider = User.objects.create_user(
            email="provider@test.com",
            password="testpassword123",
            user_type="provider",
        )
        self.admin = User.objects.create_user(
            email="admin@test.com",
            password="testpassword123",
            is_staff=True,
            is_superuser=True,
        )
        self.url = reverse("ai_assistant:health")

    def test_redirects_if_not_logged_in(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 301))

    def test_provider_gets_403_without_checking_ollama(self):
        self.client.login(email="provider@test.com", password="testpassword123")
        with patch("ai_assistant.views.get_client") as mock_get_client:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        mock_get_client.assert_not_called()

    def test_admin_gets_403_without_checking_ollama(self):
        self.client.login(email="admin@test.com", password="testpassword123")
        with patch("ai_assistant.views.get_client") as mock_get_client:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        mock_get_client.assert_not_called()

    @patch("ai_assistant.views.get_client")
    def test_returns_200_when_ollama_ok(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.model = "qwen2.5:3b"
        mock_client.health_check.return_value = {
            "ok": True,
            "model_available": True,
            "available_models": ["qwen2.5:3b"],
            "error": None,
        }
        mock_get_client.return_value = mock_client

        self.client.login(email="customer@test.com", password="testpassword123")
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["model_available"])

    @patch("ai_assistant.views.get_client")
    def test_returns_503_when_ollama_unreachable(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.model = "qwen2.5:3b"
        mock_client.health_check.return_value = {
            "ok": False,
            "model_available": False,
            "available_models": [],
            "error": "Connection refused",
        }
        mock_get_client.return_value = mock_client

        self.client.login(email="customer@test.com", password="testpassword123")
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])


class ChatViewTest(TestCase):
    """POST /ai/chat/ — authentication, validation, safety, and LLM path."""

    def setUp(self):
        self.client = Client()
        self.customer = User.objects.create_user(
            email="customer@test.com",
            password="testpassword123",
            user_type="customer",
        )
        self.provider = User.objects.create_user(
            email="provider@test.com",
            password="testpassword123",
            user_type="provider",
        )
        self.url = reverse("ai_assistant:chat")

    def _post(self, payload, user=None):
        if user is None:
            user = self.customer
        self.client.login(email=user.email, password="testpassword123")
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_redirects_unauthenticated(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"message": "hello"}),
            content_type="application/json",
        )
        self.assertIn(response.status_code, (302, 301))

    def test_provider_gets_403(self):
        response = self._post({"message": "fix my tap"}, user=self.provider)
        self.assertEqual(response.status_code, 403)

    def test_empty_message_gets_400(self):
        response = self._post({"message": "  "})
        self.assertEqual(response.status_code, 400)

    def test_missing_message_key_gets_400(self):
        response = self._post({})
        self.assertEqual(response.status_code, 400)

    def test_message_too_long_gets_400(self):
        response = self._post({"message": "x" * 2001})
        self.assertEqual(response.status_code, 400)

    def test_safety_hazard_intercepted(self):
        """Gas leak message must be blocked before LLM is invoked."""
        with patch("ai_assistant.views.get_client") as mock_get_client:
            response = self._post({
                "message": "I smell strong cooking gas and hear hissing from my cylinder"
            })
            mock_get_client.assert_not_called()

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["is_hazard"])
        self.assertEqual(data["intent"], "emergency_safety_alert")
        self.assertIn("safety_guidance", data)
        self.assertIn("emergency_contacts", data)

    @patch("ai_assistant.views.get_client")
    def test_successful_service_request(self, mock_get_client):
        """Happy path: dripping tap → service_request for plumbing."""
        llm_payload = json.dumps({
            "intent": "service_request",
            "category_slug": "plumbing-services",
            "problem_summary": "Kitchen tap dripping",
            "pincode": None,
            "missing_fields": ["pincode", "preferred_date", "preferred_time", "address"],
            "needs_clarification": True,
        })
        mock_client = MagicMock()
        mock_client.chat.return_value = {
            "content": llm_payload,
            "parsed": json.loads(llm_payload),
            "elapsed": 1.23,
            "tok_s": 45.0,
            "error": None,
        }
        mock_get_client.return_value = mock_client

        response = self._post({"message": "My kitchen tap is dripping"})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["is_hazard"])
        self.assertEqual(data["intent"], "service_request")
        self.assertEqual(data["category_slug"], "plumbing-services")
        self.assertIn("missing_fields", data)

    @patch("ai_assistant.views.get_client")
    def test_ollama_down_returns_503(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = {
            "content": "",
            "parsed": None,
            "elapsed": 0.0,
            "tok_s": 0.0,
            "error": "Connection refused",
        }
        mock_get_client.return_value = mock_client

        response = self._post({"message": "fix my tap"})

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])

    @patch("ai_assistant.views.get_client")
    def test_unsupported_request(self, mock_get_client):
        llm_payload = json.dumps({
            "intent": "unsupported_or_out_of_scope",
            "category_slug": None,
            "problem_summary": "iPhone screen repair",
            "pincode": None,
            "missing_fields": [],
            "needs_clarification": False,
        })
        mock_client = MagicMock()
        mock_client.chat.return_value = {
            "content": llm_payload,
            "parsed": json.loads(llm_payload),
            "elapsed": 0.9,
            "tok_s": 50.0,
            "error": None,
        }
        mock_get_client.return_value = mock_client

        response = self._post({"message": "Fix my iPhone screen please"})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["intent"], "unsupported_or_out_of_scope")
        self.assertIsNone(data["category_slug"])

    def test_chat_requires_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(email=self.customer.email, password="testpassword123")

        response = csrf_client.post(
            self.url,
            data=json.dumps({"message": "fix my tap"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    @patch("ai_assistant.views.get_client")
    def test_chat_accepts_csrf_cookie_and_header(self, mock_get_client):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(email=self.customer.email, password="testpassword123")
        csrf_token = "a" * 64
        csrf_client.cookies["csrftoken"] = csrf_token
        mock_client = MagicMock()
        mock_client.chat.return_value = {
            "content": "",
            "parsed": {
                "intent": "needs_clarification",
                "category_slug": None,
                "problem_summary": "Unclear request",
                "pincode": None,
                "missing_fields": ["problem_details"],
                "needs_clarification": True,
            },
            "elapsed": 0.1,
            "tok_s": 1.0,
            "error": None,
        }
        mock_get_client.return_value = mock_client

        response = csrf_client.post(
            self.url,
            data=json.dumps({"message": "help"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, 200)

    @patch("ai_assistant.views.get_client")
    def test_chat_does_not_require_database_cache(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = {
            "content": "",
            "parsed": {
                "intent": "needs_clarification",
                "category_slug": None,
                "problem_summary": "Unclear request",
                "pincode": None,
                "missing_fields": ["problem_details"],
                "needs_clarification": True,
            },
            "elapsed": 0.1,
            "tok_s": 1.0,
            "error": None,
        }
        mock_get_client.return_value = mock_client

        self.client.login(email=self.customer.email, password="testpassword123")
        with patch("django.core.cache.cache.get", side_effect=RuntimeError("cache unavailable")):
            response = self._post({"message": "help"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])


class ChatWidgetIntegrationTest(TestCase):
    """Template and static-contract checks for the customer chat widget."""

    def setUp(self):
        self.customer = User.objects.create_user(
            email="widget-customer@test.com",
            password="testpassword123",
            user_type="customer",
        )

    def test_authenticated_customer_home_includes_widget_and_csrf_token(self):
        self.client.login(email=self.customer.email, password="testpassword123")
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-fixnow-ai')
        self.assertContains(response, 'data-chat-endpoint="/ai/chat/"')
        self.assertContains(response, 'fixnow_chat.css')
        self.assertContains(response, 'fixnow_chat.js')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_anonymous_home_does_not_render_customer_widget(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-fixnow-ai')

    def test_widget_pages_use_the_shared_include(self):
        project_root = Path(__file__).resolve().parent.parent
        page_paths = (
            "templates/home.html",
            "templates/frontend/category_detail.html",
            "templates/frontend/booking_form.html",
            "templates/frontend/booking_confirm.html",
            "templates/frontend/customer_dashboard.html",
            "templates/frontend/customer_booking_detail.html",
        )
        for page_path in page_paths:
            with self.subTest(page_path=page_path):
                source = (project_root / page_path).read_text()
                self.assertIn('frontend/ai_chat_widget.html', source)

    def test_widget_renders_assistant_text_as_text_content(self):
        script = (Path(__file__).resolve().parent.parent / "static/js/fixnow_chat.js").read_text()

        self.assertIn("message.textContent = text", script)
        self.assertNotIn("innerHTML", script)
        self.assertIn("X-CSRFToken", script)
