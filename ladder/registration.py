"""Atomic account creation and database-normalized email identity."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Value
from django.db.models.functions import Lower, NullIf, Trim

from .models import PlayerProfile


EMAIL_CONFLICT = "An account with this email address already exists."


class RegistrationConflict(Exception):
    def __init__(self, field, message):
        self.field = field
        super().__init__(message)


def normalized_email_expression():
    return NullIf(Lower(Trim("email")), Value(""))


def users_with_email(email):
    return get_user_model().objects.alias(normalized_email=normalized_email_expression()).filter(normalized_email=email.strip().lower())


def duplicate_email_user_ids(user_model, using="default"):
    """Return conflicting IDs only, never emit account addresses into logs."""
    users = user_model.objects.using(using).annotate(normalized_email=normalized_email_expression())
    duplicates = users.exclude(normalized_email=None).values("normalized_email").annotate(total=Count("pk")).filter(total__gt=1)
    return [list(users.filter(normalized_email=row["normalized_email"]).order_by("pk").values_list("pk", flat=True)) for row in duplicates]


def register_player(form):
    """A valid public form may still lose an insert race; roll back completely."""
    try:
        with transaction.atomic():
            user = form.save()
            PlayerProfile.objects.create(user=user, gender=form.cleaned_data["gender"])
        return user
    except IntegrityError:
        # Query only after the failed transaction/savepoint has rolled back.
        if users_with_email(form.cleaned_data["email"]).exists():
            raise RegistrationConflict("email", EMAIL_CONFLICT) from None
        if get_user_model().objects.filter(username=form.cleaned_data["username"]).exists():
            raise RegistrationConflict("username", "A user with that username already exists.") from None
        raise
