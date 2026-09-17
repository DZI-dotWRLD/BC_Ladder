from datetime import date, datetime, time, timedelta
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.management import call_command
from django.db import connection, transaction
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from config.settings import DEVELOPMENT_SECRET_KEY, build_allowed_hosts, build_database_config, production_settings_errors

from .admin import PlayerProfileAdmin, TeamAdmin
from .email_notifications import deliver_event_email_notifications
from .models import (
    AdminNotification,
    AvailabilitySlot,
    EmailNotificationDelivery,
    Match,
    MatchParticipant,
    MatchSuggestion,
    PlayerProfile,
    SuggestionParticipant,
    Team,
    TeamMembership,
    WorkflowEvent,
)
from .services import (
    create_admin_notification_for_conflict,
    create_match_suggestion,
    find_opponent_suggestions,
    request_membership_change,
    save_availability,
    submit_match_result,
)


class AdminInvariantTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/admin/")
        self.request.user = get_user_model().objects.create_superuser(
            username="admin-invariants",
            email="admin@example.com",
            password="test-password",
        )
        self.team_admin = TeamAdmin(Team, admin.site)
        self.profile_admin = PlayerProfileAdmin(PlayerProfile, admin.site)
        self.team = Team.objects.create(name="Admin Team", division=Team.DIVISION_MENS)
        self.user = get_user_model().objects.create_user(username="admin-player")
        self.profile = PlayerProfile.objects.create(user=self.user, gender=PlayerProfile.GENDER_MALE)

    def test_team_division_is_readonly_after_any_membership_history(self):
        self.assertNotIn("division", self.team_admin.get_readonly_fields(self.request, self.team))
        TeamMembership.objects.create(
            player=self.profile,
            team=self.team,
            status=TeamMembership.STATUS_INACTIVE,
            effective_from=timezone.now() - timedelta(days=2),
            effective_to=timezone.now() - timedelta(days=1),
        )

        readonly = self.team_admin.get_readonly_fields(self.request, self.team)

        self.assertIn("division", readonly)
        self.assertNotIn("status", readonly)

    def test_team_status_is_readonly_with_active_membership(self):
        TeamMembership.objects.create(
            player=self.profile,
            team=self.team,
            status=TeamMembership.STATUS_ACTIVE,
            effective_from=timezone.now(),
        )

        self.assertIn("status", self.team_admin.get_readonly_fields(self.request, self.team))

    def test_team_status_is_readonly_with_scheduled_match(self):
        opponent = Team.objects.create(name="Admin Opponent", division=Team.DIVISION_MENS)
        starts_at = timezone.now() + timedelta(days=2)
        Match.objects.create(
            team_a=self.team,
            team_b=opponent,
            scheduled_week_start_date=starts_at.date(),
            scheduled_day_of_week="monday",
            scheduled_start_time=starts_at.time(),
            scheduled_end_time=(starts_at + timedelta(hours=1)).time(),
            scheduled_starts_at=starts_at,
            scheduled_ends_at=starts_at + timedelta(hours=1),
        )

        self.assertIn("status", self.team_admin.get_readonly_fields(self.request, self.team))

    def test_profile_gender_is_readonly_after_membership_or_participation(self):
        self.assertNotIn("gender", self.profile_admin.get_readonly_fields(self.request, self.profile))
        TeamMembership.objects.create(
            player=self.profile,
            team=self.team,
            status=TeamMembership.STATUS_INACTIVE,
            effective_from=timezone.now() - timedelta(days=2),
            effective_to=timezone.now() - timedelta(days=1),
        )
        self.assertIn("gender", self.profile_admin.get_readonly_fields(self.request, self.profile))

        participant = PlayerProfile.objects.create(
            user=get_user_model().objects.create_user(username="admin-participant"),
            gender=PlayerProfile.GENDER_MALE,
        )
        opponent = Team.objects.create(name="Participant Opponent", division=Team.DIVISION_MENS)
        starts_at = timezone.now() + timedelta(days=2)
        suggestion = MatchSuggestion.objects.create(
            team_a=self.team,
            team_b=opponent,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            expires_at=starts_at + timedelta(days=1),
        )
        SuggestionParticipant.objects.create(
            suggestion=suggestion,
            team=self.team,
            player=participant,
            side=SuggestionParticipant.SIDE_A,
            lineup_order=1,
        )
        self.assertIn("gender", self.profile_admin.get_readonly_fields(self.request, participant))

    def test_team_and_profile_cannot_be_deleted_in_admin(self):
        self.assertFalse(self.team_admin.has_delete_permission(self.request, self.team))
        self.assertFalse(self.profile_admin.has_delete_permission(self.request, self.profile))


class PasswordResetRequestTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="recovering-player",
            email="player@example.com",
            password="OldStrongPass123!",
        )
        PlayerProfile.objects.create(user=self.user, gender=PlayerProfile.GENDER_MALE)

    def test_login_page_links_to_password_reset(self):
        response = self.client.get(reverse("ladder:login"))

        self.assertContains(response, reverse("ladder:password_reset"))
        self.assertContains(response, "Forgot your password?")

    def test_known_email_sends_one_reset_message_without_exposing_username(self):
        response = self.client.post(reverse("ladder:password_reset"), {"email": "PLAYER@example.com"})

        self.assertRedirects(response, reverse("ladder:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["player@example.com"])
        self.assertEqual(message.subject, "Reset your BC Tennis Ladder password")
        self.assertNotIn(self.user.username, message.body)
        self.assertIn("http://testserver/accounts/reset/", message.body)

    def test_password_reset_above_email_limit_keeps_done_response_without_more_mail(self):
        response = None
        for _ in range(4):
            response = self.client.post(
                reverse("ladder:password_reset"),
                {"email": self.user.email},
                follow=True,
                REMOTE_ADDR="198.51.100.20",
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "If an active account matches that email address")
        self.assertEqual(len(mail.outbox), 3)

    def test_unknown_and_inactive_accounts_receive_same_response_without_email(self):
        unknown_response = self.client.post(reverse("ladder:password_reset"), {"email": "unknown@example.com"})
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        inactive_response = self.client.post(reverse("ladder:password_reset"), {"email": self.user.email})

        self.assertRedirects(unknown_response, reverse("ladder:password_reset_done"))
        self.assertRedirects(inactive_response, reverse("ladder:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_password_reset_request_requires_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(reverse("ladder:password_reset"), {"email": self.user.email})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_valid_token_replaces_password_and_cannot_be_reused(self):
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        token_url = reverse(
            "ladder:password_reset_confirm",
            kwargs={"uidb64": uidb64, "token": token},
        )

        response = self.client.get(token_url)
        self.assertRedirects(response, token_url.replace(token, "set-password"))

        response = self.client.post(
            token_url.replace(token, "set-password"),
            {
                "new_password1": "NewStrongPass456!",
                "new_password2": "NewStrongPass456!",
            },
        )

        self.assertRedirects(response, reverse("ladder:password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewStrongPass456!"))
        self.assertFalse(default_token_generator.check_token(self.user, token))

        login_response = self.client.post(
            reverse("ladder:login"),
            {"username": self.user.username, "password": "NewStrongPass456!"},
        )
        self.assertRedirects(login_response, reverse("ladder:dashboard"))

    def test_invalid_token_does_not_show_password_form(self):
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))

        response = self.client.get(
            reverse(
                "ladder:password_reset_confirm",
                kwargs={"uidb64": uidb64, "token": "invalid-token"},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This password reset link is invalid or has expired.")
        self.assertNotContains(response, 'name="new_password1"')


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="BC Ladder <ladder@example.com>",
)
class EmailNotificationTests(TransactionTestCase):
    captureOnCommitCallbacks = TestCase.captureOnCommitCallbacks
    club_tz = ZoneInfo("America/New_York")

    def setUp(self):
        mail.outbox.clear()

    def create_profile(self, username, *, email=None, is_staff=False, is_active=True):
        user = get_user_model().objects.create_user(
            username=username,
            email=email if email is not None else f"{username}@example.com",
        )
        user.is_staff = is_staff
        user.is_active = is_active
        user.save(update_fields=["is_staff", "is_active"])
        return PlayerProfile.objects.create(user=user, gender=PlayerProfile.GENDER_MALE)

    def create_team_with_members(self, name, count):
        team = Team.objects.create(name=name, division=Team.DIVISION_MENS)
        players = []
        for index in range(1, count + 1):
            profile = self.create_profile(f"{name.lower().replace(' ', '-')}-{index}")
            request_membership_change(profile.user, profile, team, "join")
            players.append(profile)
        return team, players

    def make_dt(self, day, hour):
        return datetime(2027, 9, day, hour, tzinfo=self.club_tz)

    def create_suggestion(self, *, third_member=False):
        team_a, players_a = self.create_team_with_members("Request A", 3 if third_member else 2)
        team_b, players_b = self.create_team_with_members("Request B", 2)
        starts_at = self.make_dt(13, 18)
        ends_at = self.make_dt(13, 20)
        for player in players_a + players_b:
            save_availability(player.user, starts_at, ends_at)
        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option, expires_at=self.make_dt(20, 18), actor=players_a[0].user)
        return suggestion, players_a, players_b

    def test_new_match_request_emails_exactly_the_four_selected_players(self):
        with self.captureOnCommitCallbacks(execute=True):
            suggestion, players_a, players_b = self.create_suggestion(third_member=True)

        selected_user_ids = set(suggestion.participants.values_list("player__user_id", flat=True))
        event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.MATCH_REQUEST_CREATED)
        self.assertEqual(event.suggestion, suggestion)
        self.assertEqual(set(event.recipients.values_list("user_id", flat=True)), selected_user_ids)
        self.assertEqual(len(mail.outbox), 4)
        self.assertEqual(
            {message.to[0] for message in mail.outbox}, {user.email for user in get_user_model().objects.filter(id__in=selected_user_ids)}
        )
        self.assertNotIn(players_a[2].user.email, {message.to[0] for message in mail.outbox})
        for message in mail.outbox:
            self.assertEqual(len(message.to), 1)
            self.assertIn("Request A vs Request B", message.subject)
            self.assertIn("Monday, September 13, 2027", message.body)
            self.assertIn("06:00 PM–08:00 PM America/New_York", message.body)
            for player in players_a[:2] + players_b:
                self.assertIn(player.user.username, message.body)
        self.assertEqual(
            EmailNotificationDelivery.objects.filter(status=EmailNotificationDelivery.STATUS_SENT).count(),
            4,
        )

    @override_settings(NOTIFICATION_DELIVERY_MODE="scheduled")
    def test_scheduled_mode_leaves_delivery_pending_without_sending(self):
        with self.captureOnCommitCallbacks(execute=True):
            suggestion, _players_a, _players_b = self.create_suggestion()

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(
            EmailNotificationDelivery.objects.filter(event__suggestion=suggestion, status=EmailNotificationDelivery.STATUS_PENDING).count(),
            4,
        )

    @override_settings(NOTIFICATION_DELIVERY_MODE="thread")
    def test_thread_mode_delivers_using_joined_worker(self):
        threads = []
        from .email_notifications import _start_delivery_thread as original_start

        def capture_thread(event_id):
            thread = original_start(event_id)
            threads.append(thread)
            return thread

        with patch("ladder.email_notifications._start_delivery_thread", side_effect=capture_thread):
            with self.captureOnCommitCallbacks(execute=True):
                suggestion, _players_a, _players_b = self.create_suggestion()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(len(threads), 1)
        self.assertFalse(threads[0].is_alive())
        self.assertEqual(len(mail.outbox), 4)
        self.assertEqual(
            EmailNotificationDelivery.objects.filter(event__suggestion=suggestion, status=EmailNotificationDelivery.STATUS_SENT).count(),
            4,
        )

    def test_duplicate_match_request_does_not_queue_or_send_again(self):
        with self.captureOnCommitCallbacks(execute=True):
            suggestion, _players_a, _players_b = self.create_suggestion()
        option = {
            "team_a": suggestion.team_a,
            "team_b": suggestion.team_b,
            "team_a_players": tuple(
                participant.player for participant in suggestion.participants.filter(side="a").order_by("lineup_order")
            ),
            "team_b_players": tuple(
                participant.player for participant in suggestion.participants.filter(side="b").order_by("lineup_order")
            ),
            "starts_at": suggestion.starts_at,
            "ends_at": suggestion.ends_at,
        }
        option["availability"] = {
            slot.player_id: slot
            for slot in AvailabilitySlot.objects.filter(
                player_id__in=[player.pk for player in option["team_a_players"] + option["team_b_players"]],
                status=AvailabilitySlot.STATUS_ACTIVE,
                starts_at__lte=suggestion.starts_at,
                ends_at__gte=suggestion.ends_at,
            )
        }

        with self.captureOnCommitCallbacks(execute=True):
            duplicate = create_match_suggestion(option, expires_at=self.make_dt(20, 18))

        self.assertEqual(duplicate, suggestion)
        self.assertEqual(len(mail.outbox), 4)
        self.assertEqual(EmailNotificationDelivery.objects.count(), 4)

    def test_missing_player_email_is_recorded_as_skipped_without_blocking_request(self):
        team_a, players_a = self.create_team_with_members("Missing A", 2)
        team_b, players_b = self.create_team_with_members("Missing B", 2)
        players_b[1].user.email = ""
        players_b[1].user.save(update_fields=["email"])
        starts_at = self.make_dt(14, 18)
        ends_at = self.make_dt(14, 20)
        for player in players_a + players_b:
            save_availability(player.user, starts_at, ends_at)
        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]

        with self.assertLogs("ladder.email_notifications", level="WARNING"), self.captureOnCommitCallbacks(execute=True):
            suggestion = create_match_suggestion(option, expires_at=self.make_dt(21, 18))

        self.assertIsNotNone(suggestion.pk)
        self.assertEqual(len(mail.outbox), 3)
        skipped = EmailNotificationDelivery.objects.get(user=players_b[1].user)
        self.assertEqual(skipped.status, EmailNotificationDelivery.STATUS_SKIPPED)

        players_b[1].user.email = "corrected@example.com"
        players_b[1].user.save(update_fields=["email"])
        call_command("send_notification_emails", "--retry-skipped", stdout=StringIO())
        skipped.refresh_from_db()
        self.assertEqual(skipped.status, EmailNotificationDelivery.STATUS_SENT)
        self.assertEqual(mail.outbox[-1].to, ["corrected@example.com"])

    def test_score_conflict_emails_only_active_staff_with_both_scores(self):
        admin = self.create_profile("active-admin", email="admin@example.com", is_staff=True)
        self.create_profile("inactive-admin", email="inactive@example.com", is_staff=True, is_active=False)
        team_a, players_a = self.create_team_with_members("Score A", 2)
        team_b, players_b = self.create_team_with_members("Score B", 2)
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2027, 9, 13),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.WEDNESDAY,
            scheduled_start_time=time(18),
            scheduled_end_time=time(20),
        )
        for side, team, players in (("a", team_a, players_a), ("b", team_b, players_b)):
            for order, player in enumerate(players, start=1):
                MatchParticipant.objects.create(match=match, team=team, player=player, side=side, lineup_order=order)

        submit_match_result(players_a[0].user, match, [(6, 4), (6, 4)])
        with self.captureOnCommitCallbacks(execute=True):
            submit_match_result(players_b[0].user, match, [(4, 6), (4, 6)])

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [admin.user.email])
        self.assertIn("Action required: score conflict", message.subject)
        self.assertIn("Score A submitted: 6-4, 6-4", message.body)
        self.assertIn("Score B submitted: 4-6, 4-6", message.body)
        self.assertIn("Wednesday, September 15, 2027", message.body)
        self.assertNotIn("inactive@example.com", {item.to[0] for item in mail.outbox})
        self.assertEqual(EmailNotificationDelivery.objects.count(), 1)

        with self.captureOnCommitCallbacks(execute=True):
            duplicate = create_admin_notification_for_conflict(match)

        self.assertEqual(duplicate, AdminNotification.objects.get(match=match, is_resolved=False))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(EmailNotificationDelivery.objects.count(), 1)

    def test_backend_failure_preserves_request_and_can_be_retried(self):
        with patch("ladder.email_notifications.EmailMessage.send", side_effect=OSError("SMTP unavailable")):
            with self.assertLogs("ladder.email_notifications", level="ERROR"), self.captureOnCommitCallbacks(execute=True):
                suggestion, _players_a, _players_b = self.create_suggestion()

        self.assertTrue(MatchSuggestion.objects.filter(pk=suggestion.pk).exists())
        self.assertEqual(
            EmailNotificationDelivery.objects.filter(status=EmailNotificationDelivery.STATUS_FAILED).count(),
            4,
        )
        call_command("send_notification_emails", "--retry-failed", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 4)
        self.assertEqual(
            EmailNotificationDelivery.objects.filter(status=EmailNotificationDelivery.STATUS_SENT).count(),
            4,
        )

    def test_delivery_locks_only_outbox_rows(self):
        suggestion, _players_a, _players_b = self.create_suggestion()
        event = WorkflowEvent.objects.get(suggestion=suggestion)
        EmailNotificationDelivery.objects.filter(event=event).update(status=EmailNotificationDelivery.STATUS_PENDING)

        with patch.object(
            EmailNotificationDelivery.objects,
            "select_for_update",
            wraps=EmailNotificationDelivery.objects.select_for_update,
        ) as select_for_update:
            deliver_event_email_notifications(event.id)

        self.assertEqual(select_for_update.call_count, 4)
        select_for_update.assert_called_with(of=("self",))

    def test_smtp_runs_without_transaction_and_live_claim_is_not_reclaimed(self):
        from django.db import connection

        from .email_notifications import _claim_delivery

        def send(message):
            self.assertFalse(connection.in_atomic_block)
            delivery = EmailNotificationDelivery.objects.get(user__email=message.to[0])
            self.assertEqual(delivery.status, "sending")
            self.assertIsNone(_claim_delivery(delivery.pk, ["pending"]))
            return 1

        with patch("ladder.email_notifications.EmailMessage.send", autospec=True, side_effect=send):
            self.create_suggestion()

    def test_crash_claim_is_recovered_and_old_token_cannot_finalize(self):
        from .email_notifications import _claim_delivery, _finalize_delivery

        suggestion, _, _ = self.create_suggestion()
        delivery = EmailNotificationDelivery.objects.filter(event__suggestion=suggestion).first()
        EmailNotificationDelivery.objects.filter(pk=delivery.pk).update(status="pending", sent_at=None)
        old = _claim_delivery(delivery.pk, ["pending"])
        self.assertIsNone(_claim_delivery(delivery.pk, ["pending"]))
        EmailNotificationDelivery.objects.filter(pk=delivery.pk).update(claim_expires_at=timezone.now() - timedelta(seconds=1))
        new = _claim_delivery(delivery.pk, ["pending"])
        self.assertNotEqual(old.claim_token, new.claim_token)
        self.assertFalse(_finalize_delivery(old, "sent"))
        self.assertTrue(_finalize_delivery(new, "sent"))
        new.refresh_from_db()
        self.assertEqual(new.attempts, 3)

    def test_delivery_rejects_caller_transaction(self):
        with transaction.atomic(), self.assertRaises(RuntimeError):
            deliver_event_email_notifications(0)

    def test_process_crash_before_smtp_is_recovered_by_command(self):
        with patch("ladder.email_notifications.EmailMessage.send", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            # Simulate a terminated worker; domain data remains committed.
            self.create_suggestion()
        delivery = EmailNotificationDelivery.objects.get(status="sending")
        self.assertEqual(delivery.attempts, 1)
        EmailNotificationDelivery.objects.filter(pk=delivery.pk).update(claim_expires_at=timezone.now() - timedelta(seconds=1))
        call_command("send_notification_emails", stdout=StringIO())
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, "sent")
        self.assertEqual(delivery.attempts, 2)
        self.assertEqual(len(mail.outbox), 4)

    def test_crash_after_provider_acceptance_can_duplicate_on_recovery(self):
        with patch("ladder.email_notifications._finalize_delivery", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.create_suggestion()
        self.assertEqual(len(mail.outbox), 1)
        delivery = EmailNotificationDelivery.objects.get(status="sending")
        EmailNotificationDelivery.objects.filter(pk=delivery.pk).update(claim_expires_at=timezone.now() - timedelta(seconds=1))
        call_command("send_notification_emails", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 5)
        self.assertEqual(EmailNotificationDelivery.objects.filter(status="sent").count(), 4)

    def test_rollback_neither_queues_nor_sends(self):
        with self.assertRaises(ValueError), transaction.atomic():
            self.create_suggestion()
            raise ValueError("Rollback domain transaction")
        self.assertFalse(EmailNotificationDelivery.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_provider_error_details_are_not_logged(self):
        with patch("ladder.email_notifications.EmailMessage.send", side_effect=OSError("secret-token private@example.com")):
            with self.assertLogs("ladder.email_notifications", level="ERROR") as logs:
                self.create_suggestion()
        self.assertNotIn("secret-token", " ".join(logs.output))
        self.assertNotIn("private@example.com", " ".join(logs.output))

    @skipUnlessDBFeature("has_select_for_update")
    def test_postgresql_competing_workers_claim_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from django.db import close_old_connections

        from .email_notifications import _claim_delivery

        suggestion, _, _ = self.create_suggestion()
        delivery = EmailNotificationDelivery.objects.filter(event__suggestion=suggestion).first()
        EmailNotificationDelivery.objects.filter(pk=delivery.pk).update(status="pending")
        barrier = Barrier(2)

        def claim():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                result = _claim_delivery(delivery.pk, ["pending"])
                return result.claim_token if result else None
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: claim(), range(2)))
        self.assertEqual(sum(token is not None for token in results), 1)
        delivery.refresh_from_db()
        self.assertEqual(delivery.attempts, 2)


class ProductionSettingsValidationTests(TestCase):
    def valid_kwargs(self):
        return {
            "debug": False,
            "secret_key": "valid-secret-A7mQ9vR2xT6pL4sN8wY3zB5cD1eF0gH",
            "secret_key_was_set": True,
            "allowed_hosts": ["bc-ladder.example.com"],
            "csrf_trusted_origins": ["https://bc-ladder.example.com"],
            "session_cookie_secure": True,
            "csrf_cookie_secure": True,
            "secure_ssl_redirect": True,
            "proxy_ssl_header_name": "HTTP_X_FORWARDED_PROTO",
            "proxy_ssl_header_value": "https",
            "hsts_seconds": 0,
            "hsts_include_subdomains": False,
            "hsts_preload": False,
            "database": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "bc_ladder",
                "USER": "bc_ladder",
                "PASSWORD": "secret",
                "HOST": "db.example.com",
                "PORT": "5432",
            },
            "whitenoise_manifest_strict": True,
        }

    def test_render_external_hostname_is_safe_default_allowed_host(self):
        self.assertEqual(
            build_allowed_hosts(render_external_hostname="bc-ladder.onrender.com"),
            ["bc-ladder.onrender.com"],
        )

    def test_explicit_allowed_hosts_can_include_render_hostname(self):
        self.assertEqual(
            build_allowed_hosts(
                explicit_hosts="custom.example.com",
                render_external_hostname="bc-ladder.onrender.com",
            ),
            ["custom.example.com", "bc-ladder.onrender.com"],
        )

    def test_database_url_builds_postgresql_config_with_connection_reuse(self):
        database = build_database_config("postgresql://bc_ladder:secret@db.internal:5432/bc_ladder")["default"]

        self.assertEqual(database["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(database["NAME"], "bc_ladder")
        self.assertEqual(database["USER"], "bc_ladder")
        self.assertEqual(database["PASSWORD"], "secret")
        self.assertEqual(database["HOST"], "db.internal")
        self.assertEqual(database["PORT"], 5432)
        self.assertEqual(database["CONN_MAX_AGE"], 600)
        self.assertTrue(database["CONN_HEALTH_CHECKS"])

    def test_database_url_without_port_is_valid_for_render_postgresql(self):
        database = build_database_config("postgresql://bc_ladder:secret@db.internal/bc_ladder")["default"]
        kwargs = self.valid_kwargs()
        kwargs["database"] = database

        self.assertEqual(database["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(database["PORT"], "")
        self.assertEqual(production_settings_errors(**kwargs), [])

    def test_manual_database_env_path_remains_development_fallback(self):
        database = build_database_config(manual_env={})["default"]

        self.assertEqual(database["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(database["NAME"], settings.BASE_DIR / "db.sqlite3")

    def test_debug_mode_allows_development_defaults(self):
        kwargs = self.valid_kwargs()
        kwargs.update(
            {
                "debug": True,
                "secret_key": DEVELOPMENT_SECRET_KEY,
                "secret_key_was_set": False,
                "allowed_hosts": ["localhost", "127.0.0.1"],
                "csrf_trusted_origins": ["http://localhost:8000"],
                "session_cookie_secure": False,
                "csrf_cookie_secure": False,
                "secure_ssl_redirect": False,
                "database": {"ENGINE": "django.db.backends.sqlite3", "NAME": "db.sqlite3"},
                "whitenoise_manifest_strict": False,
            }
        )

        self.assertEqual(production_settings_errors(**kwargs), [])

    def test_production_rejects_development_secret_and_hosts(self):
        kwargs = self.valid_kwargs()
        kwargs.update(
            {
                "secret_key": DEVELOPMENT_SECRET_KEY,
                "secret_key_was_set": False,
                "allowed_hosts": ["localhost", "*"],
            }
        )

        errors = production_settings_errors(**kwargs)

        self.assertTrue(any("DJANGO_SECRET_KEY" in error for error in errors))
        self.assertTrue(any("must not contain '*'" in error for error in errors))
        self.assertTrue(any("local development hosts" in error for error in errors))

    def test_production_rejects_insecure_or_local_csrf_trusted_origins(self):
        kwargs = self.valid_kwargs()
        kwargs["csrf_trusted_origins"] = [
            "http://bc-ladder.example.com",
            "https://*.example.com",
            "https://localhost:8000",
        ]

        errors = production_settings_errors(**kwargs)

        self.assertTrue(any("entries must use https://" in error for error in errors))
        self.assertTrue(any("must not contain wildcards" in error for error in errors))
        self.assertTrue(any("local development origins" in error for error in errors))

    def test_production_rejects_low_diversity_or_django_insecure_secret(self):
        kwargs = self.valid_kwargs()
        kwargs["secret_key"] = "x" * 32

        low_diversity_errors = production_settings_errors(**kwargs)

        self.assertTrue(any("DJANGO_SECRET_KEY" in error for error in low_diversity_errors))

        kwargs["secret_key"] = "django-insecure-this-secret-is-long-enough-but-invalid-A7mQ9v"

        django_insecure_errors = production_settings_errors(**kwargs)

        self.assertTrue(any("DJANGO_SECRET_KEY" in error for error in django_insecure_errors))

    def test_production_rejects_sqlite_or_incomplete_database(self):
        kwargs = self.valid_kwargs()
        kwargs["database"] = {"ENGINE": "django.db.backends.sqlite3", "NAME": "db.sqlite3"}

        sqlite_errors = production_settings_errors(**kwargs)

        self.assertTrue(any("DJANGO_DB_ENGINE" in error for error in sqlite_errors))
        self.assertTrue(any("DJANGO_DB_USER" in error for error in sqlite_errors))

        kwargs = self.valid_kwargs()
        kwargs["database"] = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "bc_ladder",
            "USER": "",
            "PASSWORD": "secret",
            "HOST": "db.example.com",
            "PORT": "5432",
        }

        incomplete_errors = production_settings_errors(**kwargs)

        self.assertTrue(any("DJANGO_DB_USER" in error for error in incomplete_errors))

    def test_production_rejects_non_strict_static_manifest(self):
        kwargs = self.valid_kwargs()
        kwargs["whitenoise_manifest_strict"] = False

        errors = production_settings_errors(**kwargs)

        self.assertTrue(any("DJANGO_WHITENOISE_MANIFEST_STRICT" in error for error in errors))

    def test_production_rejects_insecure_cookie_and_redirect_settings(self):
        kwargs = self.valid_kwargs()
        kwargs.update(
            {
                "session_cookie_secure": False,
                "csrf_cookie_secure": False,
                "secure_ssl_redirect": False,
            }
        )

        errors = production_settings_errors(**kwargs)

        self.assertTrue(any("DJANGO_SESSION_COOKIE_SECURE" in error for error in errors))
        self.assertTrue(any("DJANGO_CSRF_COOKIE_SECURE" in error for error in errors))
        self.assertTrue(any("DJANGO_SECURE_SSL_REDIRECT" in error for error in errors))

    def test_production_rejects_partial_proxy_and_invalid_hsts_preload(self):
        kwargs = self.valid_kwargs()
        kwargs.update(
            {
                "proxy_ssl_header_name": "HTTP_X_FORWARDED_PROTO",
                "proxy_ssl_header_value": "",
                "hsts_preload": True,
                "hsts_include_subdomains": False,
                "hsts_seconds": 300,
            }
        )

        errors = production_settings_errors(**kwargs)

        self.assertTrue(any("Set both DJANGO_SECURE_PROXY_SSL_HEADER_NAME" in error for error in errors))
        self.assertTrue(any("DJANGO_SECURE_HSTS_PRELOAD requires DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS" in error for error in errors))
        self.assertTrue(any("DJANGO_SECURE_HSTS_PRELOAD requires DJANGO_SECURE_HSTS_SECONDS" in error for error in errors))


class HealthCheckTests(TestCase):
    def test_health_check_is_public_and_not_cached(self):
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_staticfiles_storage_is_non_manifest_in_debug_tests(self):
        self.assertEqual(
            settings.STORAGES["staticfiles"]["BACKEND"],
            "django.contrib.staticfiles.storage.StaticFilesStorage",
        )

    def test_static_manifest_is_non_strict_in_debug_tests(self):
        self.assertFalse(settings.WHITENOISE_MANIFEST_STRICT)
