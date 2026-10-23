"""Initialize a clean, disposable demo database exactly once."""

from django.core.management import CommandError, call_command
from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError

from fixnow_app.models import (
    Booking,
    CustomUser,
    CustomerProfile,
    Notification,
    ProviderProfile,
    Review,
    ServiceCategory,
    ServicePincode,
)


PROTECTED_MODELS = (
    CustomUser,
    CustomerProfile,
    ProviderProfile,
    ServiceCategory,
    ServicePincode,
    Booking,
    Review,
    Notification,
)


class Command(BaseCommand):
    help = "Migrate and seed a clean FixNow demo database; refuses populated databases."

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            default="default",
            help="Database alias to initialize (default: default).",
        )

    def handle(self, *args, **options):
        database = options["database"]
        connection = connections[database]
        existing_tables = set(connection.introspection.table_names())

        # A new SQLite database has no application tables. An already migrated
        # but empty database is also safe to complete. Any existing records
        # cause a hard stop before migrate or seed can write anything.
        for model in PROTECTED_MODELS:
            if model._meta.db_table not in existing_tables:
                continue
            try:
                if model.objects.using(database).exists():
                    raise CommandError(
                        "Refusing to initialize a populated database. "
                        f"Found existing {model._meta.label} records."
                    )
            except OperationalError as exc:
                raise CommandError(
                    "Could not safely inspect the target database; no changes were made."
                ) from exc

        call_command("migrate", database=database, interactive=False, verbosity=0)
        call_command("seed_demo_data", database=database)
        self.stdout.write(self.style.SUCCESS("Clean demo environment initialized."))
