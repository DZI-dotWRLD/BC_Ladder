import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, get_connection
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from .models import EmailNotificationDelivery

logger = logging.getLogger(__name__)


def queue_email_notifications(event, recipient_user_ids, notification_type):
    deliveries = [
        EmailNotificationDelivery(event=event, user_id=user_id, notification_type=notification_type)
        for user_id in sorted(set(recipient_user_ids))
    ]
    EmailNotificationDelivery.objects.bulk_create(deliveries, ignore_conflicts=True)
    transaction.on_commit(lambda: deliver_event_email_notifications(event.id), robust=True)


def _display_name(user):
    return user.get_full_name().strip() or user.get_username()


def _local_interval(starts_at, ends_at):
    starts_at = timezone.localtime(starts_at)
    ends_at = timezone.localtime(ends_at)
    date_text = f"{starts_at:%A, %B} {starts_at.day}, {starts_at:%Y}"
    time_text = f"{starts_at:%I:%M %p}–{ends_at:%I:%M %p} {timezone.get_current_timezone_name()}"
    return date_text, time_text


def _participant_lines(participants, side):
    return ", ".join(_display_name(item.player.user) for item in participants if item.side == side)


def _match_request_message(event):
    suggestion = event.suggestion
    participants = list(suggestion.participants.select_related("player__user", "team").order_by("side", "lineup_order", "player_id"))
    date_text, time_text = _local_interval(suggestion.starts_at, suggestion.ends_at)
    subject = f"Match request: {suggestion.team_a.name} vs {suggestion.team_b.name}"
    body = "\n".join(
        [
            f"{suggestion.team_a.name} sent a match request to {suggestion.team_b.name}.",
            "",
            f"Date: {date_text}",
            f"Time: {time_text}",
            f"{suggestion.team_a.name}: {_participant_lines(participants, 'a')}",
            f"{suggestion.team_b.name}: {_participant_lines(participants, 'b')}",
            "",
            "Sign in to BC Ladder to review and accept the request.",
        ]
    )
    return subject, body


def _score_text(submission):
    return ", ".join(f"{result_set.team_a_score}-{result_set.team_b_score}" for result_set in submission.sets.all())


def _score_conflict_message(event):
    match = event.match
    starts_at = match.scheduled_starts_at
    ends_at = match.scheduled_ends_at
    if starts_at is None or ends_at is None:
        weekday_index = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }[match.scheduled_day_of_week]
        match_date = match.scheduled_week_start_date + timedelta(days=weekday_index)
        starts_at = timezone.make_aware(datetime.combine(match_date, match.scheduled_start_time))
        ends_at = timezone.make_aware(datetime.combine(match_date, match.scheduled_end_time))
    date_text, time_text = _local_interval(starts_at, ends_at)
    submissions = list(match.result_submissions.select_related("submitting_team").prefetch_related("sets").order_by("submitting_team_id"))
    score_lines = [f"{submission.submitting_team.name} submitted: {_score_text(submission)}" for submission in submissions]
    subject = f"Action required: score conflict for {match.team_a.name} vs {match.team_b.name}"
    body = "\n".join(
        [
            "The two submitted scores do not match and require administrator review.",
            "",
            f"Match: {match.team_a.name} vs {match.team_b.name}",
            f"Date: {date_text}",
            f"Time: {time_text}",
            *score_lines,
            "",
            "Review the unresolved score conflict in the BC Ladder administration site.",
        ]
    )
    return subject, body


def _event_message(event, notification_type):
    if notification_type == EmailNotificationDelivery.TYPE_MATCH_REQUEST:
        return _match_request_message(event)
    if notification_type == EmailNotificationDelivery.TYPE_SCORE_CONFLICT:
        return _score_conflict_message(event)
    raise ValueError("Unsupported email notification type.")


def deliver_event_email_notifications(event_id, include_failed=False, include_skipped=False):
    statuses = [EmailNotificationDelivery.STATUS_PENDING]
    if include_failed:
        statuses.append(EmailNotificationDelivery.STATUS_FAILED)
    if include_skipped:
        statuses.append(EmailNotificationDelivery.STATUS_SKIPPED)

    with transaction.atomic():
        deliveries = list(
            EmailNotificationDelivery.objects.select_for_update(of=("self",))
            .filter(event_id=event_id, status__in=statuses)
            .select_related(
                "user",
                "event__suggestion__team_a",
                "event__suggestion__team_b",
                "event__match__team_a",
                "event__match__team_b",
            )
            .order_by("id")
        )
        if not deliveries:
            return 0

        connection = get_connection()
        sent_count = 0
        for delivery in deliveries:
            delivery.attempts += 1
            delivery.last_attempt_at = timezone.now()
            email_address = delivery.user.email.strip()
            try:
                validate_email(email_address)
            except ValidationError:
                delivery.status = EmailNotificationDelivery.STATUS_SKIPPED
                delivery.last_error = "Recipient has no valid email address."
                delivery.save(update_fields=["attempts", "last_attempt_at", "status", "last_error"])
                logger.warning(
                    "Skipped notification email for user_id=%s because the address is missing or invalid.",
                    delivery.user_id,
                )
                continue

            try:
                subject, body = _event_message(delivery.event, delivery.notification_type)
                message = EmailMessage(
                    subject=subject,
                    body=body,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[email_address],
                    connection=connection,
                )
                if message.send() != 1:
                    raise RuntimeError("Email backend did not report a successful delivery.")
            except Exception as error:  # Email backends raise provider-specific exceptions.
                delivery.status = EmailNotificationDelivery.STATUS_FAILED
                delivery.last_error = type(error).__name__
                delivery.save(update_fields=["attempts", "last_attempt_at", "status", "last_error"])
                logger.exception("Notification email delivery failed for delivery_id=%s.", delivery.id)
                continue

            delivery.status = EmailNotificationDelivery.STATUS_SENT
            delivery.sent_at = timezone.now()
            delivery.last_error = ""
            delivery.save(update_fields=["attempts", "last_attempt_at", "status", "sent_at", "last_error"])
            sent_count += 1
        return sent_count
