from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from ladder.models import RateLimitEvent


class Command(BaseCommand):
    help = "Delete rate-limit events older than 24 hours."

    def handle(self, *args, **options):
        deleted, _ = RateLimitEvent.objects.filter(created_at__lt=timezone.now() - timedelta(hours=24)).delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} expired rate-limit event(s)."))
