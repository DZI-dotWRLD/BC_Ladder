from django.core.management.base import BaseCommand

from ladder.email_notifications import deliver_event_email_notifications, eligible_deliveries
from ladder.models import EmailNotificationDelivery


class Command(BaseCommand):
    help = "Send pending notification emails, recover expired claims, and optionally retry failed or skipped deliveries."

    def add_arguments(self, parser):
        parser.add_argument(
            "--retry-failed",
            action="store_true",
            help="Retry deliveries that previously failed because of an email backend error.",
        )
        parser.add_argument(
            "--retry-skipped",
            action="store_true",
            help="Retry skipped deliveries after invalid user email addresses have been corrected.",
        )

    def handle(self, *args, **options):
        statuses = [EmailNotificationDelivery.STATUS_PENDING]
        if options["retry_failed"]:
            statuses.append(EmailNotificationDelivery.STATUS_FAILED)
        if options["retry_skipped"]:
            statuses.append(EmailNotificationDelivery.STATUS_SKIPPED)
        event_ids = list(eligible_deliveries(statuses).order_by("event_id").values_list("event_id", flat=True).distinct())
        sent_count = sum(
            deliver_event_email_notifications(
                event_id,
                include_failed=options["retry_failed"],
                include_skipped=options["retry_skipped"],
            )
            for event_id in event_ids
        )
        self.stdout.write(self.style.SUCCESS(f"Sent {sent_count} notification email(s)."))
