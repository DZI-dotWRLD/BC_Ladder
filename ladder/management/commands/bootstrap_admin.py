import os

from django.core.management.base import BaseCommand, CommandError

from ladder.services import ProvisioningError, provision_first_administrator


class Command(BaseCommand):
    help = "Provision the first administrator with a password-reset email."

    def handle(self, *args, **options):
        try:
            user = provision_first_administrator(
                os.environ.get("DJANGO_BOOTSTRAP_ADMIN_USERNAME"),
                os.environ.get("DJANGO_BOOTSTRAP_ADMIN_EMAIL"),
            )
        except ProvisioningError as error:
            raise CommandError(str(error)) from error

        if user is None:
            self.stdout.write(self.style.SUCCESS("Administrator already provisioned."))
            return
        self.stdout.write(self.style.SUCCESS("Administrator provisioned; password-reset email sent."))
