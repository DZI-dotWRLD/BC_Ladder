from django.contrib.auth import get_user_model

from .models import WorkflowEvent, WorkflowEventRecipient


def active_staff_user_ids():
    return set(get_user_model().objects.filter(is_active=True, is_staff=True).values_list("id", flat=True))


def match_participant_user_ids(match):
    return set(match.participants.values_list("player__user_id", flat=True))


def suggestion_participant_user_ids(suggestion):
    return set(suggestion.participants.values_list("player__user_id", flat=True))


def record_workflow_event(
    *,
    event_type,
    dedupe_key,
    recipient_user_ids,
    actor=None,
    membership=None,
    match=None,
    suggestion=None,
    submission=None,
    score_correction_audit=None,
    previous_state="",
    new_state="",
    metadata=None,
):
    event, created = WorkflowEvent.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            "event_type": event_type,
            "actor": actor,
            "membership": membership,
            "match": match,
            "suggestion": suggestion,
            "submission": submission,
            "score_correction_audit": score_correction_audit,
            "previous_state": previous_state,
            "new_state": new_state,
            "metadata": metadata or {},
        },
    )
    if not created:
        return event, False

    WorkflowEventRecipient.objects.bulk_create(
        [WorkflowEventRecipient(event=event, user_id=user_id) for user_id in sorted(set(recipient_user_ids))]
    )
    return event, True
