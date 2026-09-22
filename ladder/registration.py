"""Atomic account creation and database-normalized email identity."""

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Count, Value
from django.db.models.functions import Lower, NullIf, Trim
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from .models import PlayerProfile
from .services import DomainError

EMAIL_CONFLICT = "An account with this email address already exists."


class RegistrationConflict(DomainError):
    def __init__(self, field, message):
        self.field = field
        super().__init__(message)


class VerificationFailure(DomainError):
    pass


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
            user = form.save(commit=False)
            user.is_active = False
            user.save()
            PlayerProfile.objects.create(user=user, gender=form.cleaned_data["gender"])
        return user
    except IntegrityError:
        # Query only after the failed transaction/savepoint has rolled back.
        if users_with_email(form.cleaned_data["email"]).exists():
            raise RegistrationConflict("email", EMAIL_CONFLICT) from None
        if get_user_model().objects.filter(username=form.cleaned_data["username"]).exists():
            raise RegistrationConflict("username", "A user with that username already exists.") from None
        raise


def send_verification_email(request, user):
    """Send an account-activation link directly without workflow/outbox records."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse("ladder:verify_account", kwargs={"uidb64": uid, "token": token})
    verification_url = request.build_absolute_uri(path)
    send_mail(
        "Verify your BC Tennis Ladder account",
        f"Verify your account using this secure, one-time link:\n\n{verification_url}\n",
        None,
        [user.email],
    )


def activate_user_from_token(uidb64, token):
    """Activate one inactive account exactly once under a row lock."""
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
    except TypeError, ValueError, OverflowError:
        raise VerificationFailure("This verification link is no longer valid.") from None
    with transaction.atomic():
        try:
            user = get_user_model().objects.select_for_update().get(pk=user_id)
        except get_user_model().DoesNotExist:
            raise VerificationFailure("This verification link is no longer valid.") from None
        if user.is_active or not default_token_generator.check_token(user, token):
            raise VerificationFailure("This verification link is no longer valid.")
        user.is_active = True
        user.save(update_fields=["is_active"])
    return user
