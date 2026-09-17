from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from ladder.services import DomainError, anonymize_user_account


class Command(BaseCommand):
    help = "Anonymize and deactivate a user while preserving historical records."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, **options):
        try:
            user = get_user_model().objects.get(username=options["username"])
        except get_user_model().DoesNotExist as error:
            raise CommandError("User does not exist.") from error

        try:
            anonymized = anonymize_user_account(user)
        except DomainError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(self.style.SUCCESS(f"Anonymized user ID {anonymized.pk}."))
