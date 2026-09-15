from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from ladder.registration import duplicate_email_user_ids


class Command(BaseCommand):
    help = "Read-only normalized email uniqueness preflight; reports IDs, not addresses."

    def handle(self, *args, **options):
        groups = duplicate_email_user_ids(get_user_model())
        if groups:
            for ids in groups:
                self.stdout.write(f"Duplicate normalized email: user IDs {ids}")
            raise CommandError("Email preflight failed; administrator remediation required. No accounts changed.")
        self.stdout.write(self.style.SUCCESS("Email uniqueness preflight passed."))
