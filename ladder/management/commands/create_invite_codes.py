import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ladder.models import InviteCode


class Command(BaseCommand):
    help = "Create invite codes for controlled player registration."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, required=True)
        parser.add_argument("--max-uses", type=int, default=1)
        parser.add_argument("--expires-in-days", type=int)
        parser.add_argument("--created-by")

    def handle(self, *args, **options):
        count = options["count"]
        max_uses = options["max_uses"]
        expires_in_days = options["expires_in_days"]
        if count < 1 or max_uses < 1 or (expires_in_days is not None and expires_in_days < 1):
            raise CommandError("Count, max uses, and expiry days must be positive integers.")

        users = get_user_model().objects.filter(is_superuser=True).order_by("pk")
        if options["created_by"]:
            users = users.filter(username=options["created_by"])
        creator = users.first()
        if creator is None:
            raise CommandError("Create a superuser first or name one with --created-by.")

        expires_at = timezone.now() + timedelta(days=expires_in_days) if expires_in_days is not None else None
        for _ in range(count):
            invite = InviteCode.objects.create(
                code=secrets.token_urlsafe(24),
                created_by=creator,
                max_uses=max_uses,
                expires_at=expires_at,
            )
            self.stdout.write(invite.code)
