"""Create clearly labelled, repeatable marketplace demo data."""

from django.core.management.base import BaseCommand
from django.db import transaction

from fixnow_app.models import CustomUser, ProviderProfile, ServiceCategory, ServicePincode


DEMO_CATEGORIES = {
    "plumbing-services": "Plumbing Services",
    "electrical-services": "Electrical Services",
    "ac-repair-servicing": "AC Repair & Servicing",
    "carpentry": "Carpentry",
    "house-cleaning": "House Cleaning",
    "painting": "Painting",
    "pest-control": "Pest Control",
}

DEMO_AREAS = {
    "682001": ("Ernakulam City", "Ernakulam"),
    "682016": ("Edappally", "Ernakulam"),
    "682024": ("Kakkanad", "Ernakulam"),
    "695001": ("Thiruvananthapuram City", "Thiruvananthapuram"),
    "673001": ("Calicut", "Kozhikode"),
    "680001": ("Thrissur City", "Thrissur"),
}

DEMO_PROVIDERS = (
    ("plumbing-services", "Akhil", "Menon", "akhil.menon", 7, "Demo provider profile for plumbing discovery. Akhil handles taps, leaks, fixtures, and household water-line repairs.", ("682001", "682016", "682024")),
    ("electrical-services", "Meera", "Nair", "meera.nair", 6, "Demo provider profile for electrical discovery. Meera focuses on safe residential lighting, fans, sockets, and wiring repairs.", ("682001", "695001")),
    ("ac-repair-servicing", "Faisal", "Rahman", "faisal.rahman", 9, "Demo provider profile for AC-service discovery. Faisal provides split and window AC servicing, cooling checks, and maintenance.", ("682016", "682024")),
    ("carpentry", "Nithin", "Varma", "nithin.varma", 11, "Demo provider profile for carpentry discovery. Nithin repairs doors, cabinets, hinges, latches, and household woodwork.", ("695001", "673001")),
    ("house-cleaning", "Ananya", "Pillai", "ananya.pillai", 5, "Demo provider profile for cleaning discovery. Ananya handles kitchens, bathrooms, floors, balconies, and scheduled deep cleaning.", ("682001", "680001")),
    ("painting", "Joseph", "George", "joseph.george", 10, "Demo provider profile for painting discovery. Joseph works on interior touch-ups, exterior walls, and waterproof coating preparation.", ("680001", "695001")),
    ("pest-control", "Sanjay", "Babu", "sanjay.babu", 8, "Demo provider profile for pest-control discovery. Sanjay handles household cockroach, termite, and bedbug treatment requests.", ("673001", "682024")),
)


class Command(BaseCommand):
    help = "Create idempotent, clearly labelled FixNow marketplace demo data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be created without writing to the database.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        created = {"categories": 0, "pincodes": 0, "users": 0, "providers": 0, "provider_area_links": 0}
        skipped = []

        categories = {}
        for slug, name in DEMO_CATEGORIES.items():
            category = ServiceCategory.objects.filter(slug=slug).first()
            if category is None:
                if not dry_run:
                    category = ServiceCategory.objects.create(
                        slug=slug,
                        name=name,
                        description=f"Demo marketplace category for {name.lower()}.",
                        is_active=True,
                    )
                created["categories"] += 1
            categories[slug] = category

        areas = {}
        for pincode, (area_name, district) in DEMO_AREAS.items():
            pin = ServicePincode.objects.filter(pincode=pincode).first()
            if pin is None:
                if not dry_run:
                    pin = ServicePincode.objects.create(
                        pincode=pincode, area_name=area_name, district=district
                    )
                created["pincodes"] += 1
            areas[pincode] = pin

        for slug, first_name, last_name, local_part, experience, bio, pincodes in DEMO_PROVIDERS:
            email = f"{local_part}@demo.fixnow.invalid"
            user = CustomUser.objects.filter(email=email).first()
            if user is None:
                if not dry_run:
                    user = CustomUser.objects.create_user(
                        email=email,
                        password=None,
                        first_name=first_name,
                        last_name=last_name,
                        user_type="provider",
                    )
                created["users"] += 1
            elif user.user_type != "provider":
                skipped.append(f"{email}: existing account is not a provider")
                continue

            profile = ProviderProfile.objects.filter(user=user).first() if user else None
            if profile is None:
                if not dry_run:
                    profile = ProviderProfile.objects.create(
                        user=user,
                        category=categories[slug],
                        bio=bio,
                        experience_years=experience,
                        status="approved",
                        is_available=True,
                    )
                created["providers"] += 1
            elif categories[slug] is not None and profile.category_id != categories[slug].id:
                skipped.append(f"{email}: existing profile has a different category")
                continue

            if profile:
                existing_ids = set(profile.service_pincodes.values_list("id", flat=True))
                missing = [areas[pin] for pin in pincodes if areas[pin] is not None and areas[pin].id not in existing_ids]
                if not dry_run and missing:
                    profile.service_pincodes.add(*missing)
                created["provider_area_links"] += len(missing)

        verb = "would create" if dry_run else "created or linked"
        self.stdout.write(self.style.SUCCESS(f"Demo data {verb}: {created}"))
        if skipped:
            self.stdout.write(self.style.WARNING("Skipped existing records: " + "; ".join(skipped)))
