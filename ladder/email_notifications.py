import logging
import threading
from datetime import datetime, timedelta
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, get_connection
from django.core.validators import validate_email
from django.db import close_old_connections, connection, transaction
from django.db.models import Q
from django.utils import timezone

from .models import EmailNotificationDelivery

logger = logging.getLogger(__name__)


def queue_email_notifications(event, recipient_user_ids, notification_type):
    deliveries = [
        EmailNotificationDelivery(event=event, user_id=user_id, notification_type=notification_type)
        for user_id in sorted(set(recipient_user_ids))
    ]
    EmailNotificationDelivery.objects.bulk_create(deliveries, ignore_conflicts=True)
    transaction.on_commit(lambda: _trigger_event_delivery(event.id), robust=True)


def _trigger_event_delivery(event_id):
    mode = settings.NOTIFICATION_DELIVERY_MODE
    if mode == "inline":
        deliver_event_email_notifications(event_id)
    elif mode == "thread":
        _start_delivery_thread(event_id)


def _start_delivery_thread(event_id):
    thread = threading.Thread(target=_deliver_event_in_thread, args=(event_id,), daemon=True, name=f"notification-event-{event_id}")
    thread.start()
    return thread


def _deliver_event_in_thread(event_id):
    close_old_connections()
    try:
        deliver_event_email_notifications(event_id)
    except Exception as error:
        logger.error("Notification delivery thread failed for event_id=%s (%s).", event_id, type(error).__name__)
    finally:
        connection.close()


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


def eligible_deliveries(statuses):
    return EmailNotificationDelivery.objects.filter(
        Q(status__in=statuses) | Q(status=EmailNotificationDelivery.STATUS_SENDING, claim_expires_at__lte=timezone.now())
    )


def _claim_delivery(delivery_id, statuses):
    with transaction.atomic():
        delivery = EmailNotificationDelivery.objects.select_for_update(of=("self",)).filter(pk=delivery_id).first()
        if delivery is None:
            return None
        now = timezone.now()
        expired = (
            delivery.status == EmailNotificationDelivery.STATUS_SENDING
            and delivery.claim_expires_at is not None
            and delivery.claim_expires_at <= now
        )
        if delivery.status not in statuses and not expired:
            return None
        delivery.status = EmailNotificationDelivery.STATUS_SENDING
        delivery.claim_token = uuid4()
        # Claim one message at a time; the lease must exceed the SMTP timeout.
        delivery.claim_expires_at = now + timedelta(seconds=max(300, (settings.EMAIL_TIMEOUT or 10) * 3))
        delivery.attempts += 1
        delivery.last_attempt_at = now
        delivery.save(update_fields=["status", "claim_token", "claim_expires_at", "attempts", "last_attempt_at"])
        return delivery


def _finalize_delivery(delivery, status, error=""):
    return bool(
        EmailNotificationDelivery.objects.filter(
            pk=delivery.pk, status=EmailNotificationDelivery.STATUS_SENDING, claim_token=delivery.claim_token
        ).update(
            status=status,
            last_error=error,
            claim_token=None,
            claim_expires_at=None,
            sent_at=timezone.now() if status == EmailNotificationDelivery.STATUS_SENT else None,
        )
    )


def deliver_event_email_notifications(event_id, include_failed=False, include_skipped=False):
    if connection.in_atomic_block or not connection.get_autocommit():
        raise RuntimeError("Notification delivery must run after the caller's transaction commits.")
    statuses = [EmailNotificationDelivery.STATUS_PENDING]
    if include_failed:
        statuses.append(EmailNotificationDelivery.STATUS_FAILED)
    if include_skipped:
        statuses.append(EmailNotificationDelivery.STATUS_SKIPPED)

    delivery_ids = list(eligible_deliveries(statuses).filter(event_id=event_id).order_by("id").values_list("id", flat=True))
    sent_count = 0
    for delivery_id in delivery_ids:
        delivery = _claim_delivery(delivery_id, statuses)
        if delivery is None:
            continue
        # All network I/O and message rendering happen after releasing the claim lock.
        email_address = delivery.user.email.strip()
        try:
            validate_email(email_address)
        except ValidationError:
            _finalize_delivery(delivery, EmailNotificationDelivery.STATUS_SKIPPED, "Recipient has no valid email address.")
            logger.warning("Skipped notification email for user_id=%s because the address is missing or invalid.", delivery.user_id)
            continue
        try:
            subject, body = _event_message(delivery.event, delivery.notification_type)
            message = EmailMessage(
                subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=[email_address], connection=get_connection()
            )
            if message.send() != 1:
                raise RuntimeError("Email backend did not report a successful delivery.")
        except Exception as error:  # Email backends raise provider-specific exceptions.
            _finalize_delivery(delivery, EmailNotificationDelivery.STATUS_FAILED, type(error).__name__)
            # Provider exception messages may contain addresses or credentials.
            logger.error("Notification email delivery failed for delivery_id=%s (%s).", delivery.id, type(error).__name__)
            continue
        if _finalize_delivery(delivery, EmailNotificationDelivery.STATUS_SENT):
            sent_count += 1
    return sent_count
