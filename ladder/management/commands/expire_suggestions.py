from django.core.management.base import BaseCommand

from ladder.services import expire_open_suggestions


class Command(BaseCommand):
    help = "Expire overdue proposed or partially accepted match suggestions."

    def handle(self, *args, **options):
        count = expire_open_suggestions()
        self.stdout.write(self.style.SUCCESS(f"Expired {count} suggestion(s)."))
