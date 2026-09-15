import importlib
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from ladder.forms import PlayerRegistrationForm
from ladder.models import PlayerProfile
from ladder.registration import EMAIL_CONFLICT, RegistrationConflict, register_player


def payload(username="new-player", email="player@example.com"):
    return {"username": username, "email": email, "gender": "male", "password1": "StrongPass123!", "password2": "StrongPass123!"}


class RegistrationIntegrityTests(TestCase):
    def test_registration_success_normalizes_email_and_commits_profile(self):
        response = self.client.post(reverse("ladder:register"), payload(email=" Player@Example.com "))
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="new-player")
        self.assertEqual(user.email, "player@example.com")
        self.assertTrue(PlayerProfile.objects.filter(user=user, gender="male").exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

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
        self.assertEqual(get_user_model().objects.count(), 4)

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
        self.assertEqual(get_user_model().objects.count(), 0)

    def test_stale_username_is_also_a_controlled_conflict(self):
        form = PlayerRegistrationForm(payload())
        self.assertTrue(form.is_valid())
        get_user_model().objects.create_user("new-player", email="other@example.com")
        with self.assertRaises(RegistrationConflict) as caught:
            register_player(form)
        self.assertEqual(caught.exception.field, "username")
        self.assertEqual(get_user_model().objects.count(), 1)
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
        self.assertEqual(get_user_model().objects.count(), 1)


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
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(PlayerProfile.objects.count(), 1)
