from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from .models import CustomUser, ProviderProfile


class DemoInitializationCommandTests(TestCase):
    def test_initialization_is_repeatable_on_empty_migrated_test_database(self):
        output = StringIO()
        call_command("initialize_demo_environment", stdout=output)
        self.assertEqual(
            CustomUser.objects.filter(email__endswith="@demo.fixnow.invalid").count(),
            7,
        )
        self.assertEqual(
            ProviderProfile.objects.filter(user__email__endswith="@demo.fixnow.invalid").count(),
            7,
        )

        with self.assertRaises(CommandError):
            call_command("initialize_demo_environment", stdout=StringIO())

    def test_initialization_refuses_existing_user_data(self):
        CustomUser.objects.create_user(
            email="existing@example.test",
            password="test-password-123",
            user_type="customer",
        )
        with self.assertRaises(CommandError):
            call_command("initialize_demo_environment", stdout=StringIO())
