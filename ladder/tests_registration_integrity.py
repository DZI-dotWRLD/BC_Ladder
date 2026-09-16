import importlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from axes.models import AccessLog

from ladder.forms import PlayerRegistrationForm
from ladder.models import EmailNotificationDelivery, InviteCode, PlayerProfile, WorkflowEvent
from ladder.registration import EMAIL_CONFLICT, RegistrationConflict, register_player


def payload(username="new-player", email="player@example.com", invite_code="test-invite"):
    return {
        "username": username,
        "email": email,
        "invite_code": invite_code,
        "gender": "male",
        "password1": "StrongPass123!",
        "password2": "StrongPass123!",
    }


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RegistrationIntegrityTests(TestCase):
    def setUp(self):
        self.inviter = get_user_model().objects.create_superuser("invite-admin", "admin@example.com", "AdminPass123!")
        self.invite = InviteCode.objects.create(code="test-invite", created_by=self.inviter, max_uses=20)

    def test_registration_success_normalizes_email_and_commits_profile(self):
        response = self.client.post(reverse("ladder:register"), payload(email=" Player@Example.com "))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("ladder:verification_sent"))
        user = get_user_model().objects.get(username="new-player")
        self.assertEqual(user.email, "player@example.com")
        self.assertFalse(user.is_active)
        self.assertTrue(PlayerProfile.objects.filter(user=user, gender="male").exists())
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(len(mail.outbox), 1)

    def test_database_rejects_normalized_duplicate_and_update(self):
        user = get_user_model().objects.create(username="existing", email=" Player@Example.com ")
        with self.assertRaises(IntegrityError), transaction.atomic():
            get_user_model().objects.create_user("duplicate", email="player@example.com")
        other = get_user_model().objects.create_user("other", email="other@example.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            get_user_model().objects.filter(pk=other.pk).update(email=" PLAYER@example.com ")
        user.refresh_from_db()
        self.assertEqual(user.email, " Player@Example.com ")

    def test_blank_legacy_emails_are_not_unique(self):
        for index, email in enumerate(["", "", " ", "   "]):
            get_user_model().objects.create(username=f"legacy-{index}", email=email)
        self.assertEqual(get_user_model().objects.exclude(pk=self.inviter.pk).count(), 4)

    def test_registration_requires_invite_code(self):
        registration = payload()
        registration.pop("invite_code")

        response = self.client.post(reverse("ladder:register"), registration)

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "invite_code", "This field is required.")
        self.assertFalse(get_user_model().objects.filter(username="new-player").exists())

    def test_registration_rejects_exhausted_revoked_and_expired_codes(self):
        now = timezone.now()
        codes = {
            "exhausted": InviteCode.objects.create(code="exhausted", created_by=self.inviter, max_uses=1, uses=1),
            "revoked": InviteCode.objects.create(code="revoked", created_by=self.inviter, revoked_at=now),
            "expired": InviteCode.objects.create(code="expired", created_by=self.inviter, expires_at=now - timedelta(seconds=1)),
        }
        expected = {
            "exhausted": "This invite code has already been used.",
            "revoked": "This invite code has been revoked.",
            "expired": "This invite code has expired.",
        }

        for index, (name, invite) in enumerate(codes.items()):
            with self.subTest(name=name):
                response = self.client.post(
                    reverse("ladder:register"),
                    payload(username=f"blocked-{index}", email=f"blocked-{index}@example.com", invite_code=invite.code),
                )
                self.assertEqual(response.status_code, 200)
                self.assertFormError(response.context["form"], "invite_code", expected[name])
        self.assertFalse(get_user_model().objects.filter(username__startswith="blocked-").exists())

    def test_verification_link_activates_once_and_inactive_login_fails(self):
        self.client.post(reverse("ladder:register"), payload())
        user = get_user_model().objects.get(username="new-player")
        verification_url = re.search(r"https?://[^\s]+", mail.outbox[0].body).group(0)

        login_response = self.client.post(
            reverse("ladder:login"),
            {"username": "new-player", "password": "StrongPass123!"},
        )
        self.assertContains(login_response, "Please enter a correct username and password")

        first = self.client.get(verification_url)
        user.refresh_from_db()
        second = self.client.get(verification_url)

        self.assertRedirects(first, reverse("ladder:dashboard"), fetch_redirect_response=False)
        self.assertTrue(user.is_active)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(WorkflowEvent.objects.count(), 0)
        self.assertEqual(EmailNotificationDelivery.objects.count(), 0)
        self.assertEqual(list(AccessLog.objects.values_list("path_info", flat=True)), ["/accounts/verify/"])
        self.assertFalse(AccessLog.objects.filter(path_info__contains=verification_url.rsplit("/", 2)[-2]).exists())

    def test_create_invite_codes_command_uses_superuser_attribution(self):
        output = StringIO()

        call_command("create_invite_codes", count=2, max_uses=3, expires_in_days=7, stdout=output)

        created = InviteCode.objects.exclude(pk=self.invite.pk).order_by("pk")
        self.assertEqual(created.count(), 2)
        self.assertTrue(all(invite.created_by_id == self.inviter.pk for invite in created))
        self.assertTrue(all(invite.max_uses == 3 for invite in created))
        self.assertTrue(all(invite.expires_at > timezone.now() for invite in created))
        self.assertEqual(len([line for line in output.getvalue().splitlines() if line]), 2)

    def test_verification_resend_is_throttled_without_disclosing_account(self):
        self.client.post(reverse("ladder:register"), payload())
        mail.outbox.clear()
        response = None
        for _ in range(4):
            response = self.client.post(
                reverse("ladder:resend_verification"),
                {"email": "PLAYER@example.com"},
                REMOTE_ADDR="198.51.100.30",
            )

        self.assertRedirects(response, reverse("ladder:verification_sent"))
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(WorkflowEvent.objects.count(), 0)
        self.assertEqual(EmailNotificationDelivery.objects.count(), 0)

    def test_prevalidation_handles_legacy_whitespace(self):
        get_user_model().objects.create(username="existing", email=" Player@Example.com ")
        form = PlayerRegistrationForm(payload())
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["email"], [EMAIL_CONFLICT])

    def test_stale_valid_form_gets_controlled_conflict(self):
        form = PlayerRegistrationForm(payload())
        self.assertTrue(form.is_valid())
        get_user_model().objects.create_user("winner", email="PLAYER@example.com")
        with self.assertRaises(RegistrationConflict) as caught:
            register_player(form)
        self.assertEqual(caught.exception.field, "email")
        self.assertFalse(get_user_model().objects.filter(username="new-player").exists())
        self.assertEqual(PlayerProfile.objects.count(), 0)

    def test_profile_failure_rolls_back_account_and_is_not_disguised(self):
        form = PlayerRegistrationForm(payload())
        self.assertTrue(form.is_valid())
        with patch("ladder.registration.PlayerProfile.objects.create", side_effect=IntegrityError("unrelated")):
            with self.assertRaises(IntegrityError):
                register_player(form)
        self.assertEqual(get_user_model().objects.exclude(pk=self.inviter.pk).count(), 0)

    def test_stale_username_is_also_a_controlled_conflict(self):
        form = PlayerRegistrationForm(payload())
        self.assertTrue(form.is_valid())
        get_user_model().objects.create_user("new-player", email="other@example.com")
        with self.assertRaises(RegistrationConflict) as caught:
            register_player(form)
        self.assertEqual(caught.exception.field, "username")
        self.assertEqual(get_user_model().objects.exclude(pk=self.inviter.pk).count(), 1)
        self.assertEqual(PlayerProfile.objects.count(), 0)

    def test_request_race_is_rendered_without_login(self):
        # Insert winner before the service transaction, so it survives rollback.
        from ladder.registration import register_player as original_register

        def competing_register(form):
            get_user_model().objects.create_user("winner", email="PLAYER@example.com")
            return original_register(form)

        with patch("ladder.views.register_player", side_effect=competing_register):
            response = self.client.post(reverse("ladder:register"), payload())
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "email", EMAIL_CONFLICT)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(get_user_model().objects.exclude(pk=self.inviter.pk).count(), 1)


class EmailMigrationPreflightTests(TransactionTestCase):
    def test_preflight_and_migration_fail_without_changing_accounts(self):
        migration = importlib.import_module("ladder.migrations.0016_normalized_user_email_unique")
        with connection.schema_editor() as editor:
            migration.remove_constraint(apps, editor)
        try:
            first = get_user_model().objects.create(username="first", email=" Player@Example.com ")
            second = get_user_model().objects.create_user("second", email="player@example.com")
            output = StringIO()
            from django.core.management.base import CommandError

            with self.assertRaises(CommandError):
                call_command("audit_user_emails", stdout=output)
            self.assertIn(str(first.pk), output.getvalue())
            self.assertIn(str(second.pk), output.getvalue())
            self.assertNotIn("example.com", output.getvalue())
            with self.assertRaisesRegex(RuntimeError, "administrator remediation"):
                with connection.schema_editor() as editor:
                    migration.add_constraint(apps, editor)
            self.assertEqual(get_user_model().objects.count(), 2)
            first.refresh_from_db()
            self.assertEqual(first.email, " Player@Example.com ")
        finally:
            get_user_model().objects.all().delete()
            with connection.schema_editor() as editor:
                migration.add_constraint(apps, editor)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL insert contention requires separate production-engine connections")
class PostgreSQLRegistrationRaceTests(TransactionTestCase):
    def setUp(self):
        self.inviter = get_user_model().objects.create_superuser("race-admin", "race-admin@example.com", "AdminPass123!")
        InviteCode.objects.create(code="test-invite", created_by=self.inviter, max_uses=2)

    def test_competing_requests_create_exactly_one_account_and_profile(self):
        barrier = Barrier(2)
        original = PlayerRegistrationForm.clean_email

        def synchronized_clean(form):
            email = original(form)
            barrier.wait(timeout=15)
            return email

        def submit(index):
            close_old_connections()
            try:
                response = Client().post(reverse("ladder:register"), payload(f"player-{index}", "Player@example.com"))
                return response.status_code, response.content.decode()
            finally:
                connection.close()

        with patch.object(PlayerRegistrationForm, "clean_email", synchronized_clean), ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(submit, range(2)))
        self.assertEqual(sorted(status for status, _ in outcomes), [200, 302])
        self.assertTrue(any(EMAIL_CONFLICT in body for _, body in outcomes))
        self.assertEqual(get_user_model().objects.exclude(pk=self.inviter.pk).count(), 1)
        self.assertEqual(PlayerProfile.objects.count(), 1)

    def test_single_use_invite_creates_exactly_one_account(self):
        invite = InviteCode.objects.get(code="test-invite")
        invite.max_uses = 1
        invite.save(update_fields=["max_uses"])
        barrier = Barrier(2)
        original = PlayerRegistrationForm.clean_email

        def synchronized_clean(form):
            email = original(form)
            barrier.wait(timeout=15)
            return email

        def submit(index):
            close_old_connections()
            try:
                return (
                    Client()
                    .post(
                        reverse("ladder:register"),
                        payload(f"invite-player-{index}", f"invite-player-{index}@example.com"),
                    )
                    .status_code
                )
            finally:
                connection.close()

        with patch.object(PlayerRegistrationForm, "clean_email", synchronized_clean), ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(submit, range(2)))

        invite.refresh_from_db()
        self.assertEqual(sorted(outcomes), [200, 302])
        self.assertEqual(get_user_model().objects.filter(username__startswith="invite-player-").count(), 1)
        self.assertEqual(PlayerProfile.objects.count(), 1)
        self.assertEqual(invite.uses, 1)
