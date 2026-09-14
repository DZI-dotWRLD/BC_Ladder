from datetime import datetime, timedelta, timezone as datetime_timezone

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import AvailabilityForm
from .models import (
    Match,
    MatchReservation,
    MatchSuggestion,
    PlayerProfile,
    SuggestionAcceptance,
    SuggestionParticipant,
    Team,
    WorkflowEvent,
)
from .services import AuthorizationFailure, InvalidInput, StaleState, accept_suggestion, save_availability


class ExpiryPersistenceTests(TransactionTestCase):
    def setUp(self):
        self.player = PlayerProfile.objects.create(user=get_user_model().objects.create_user(username="selected"), gender="M")
        self.team_a = Team.objects.create(name="A", division=Team.DIVISION_MENS)
        self.team_b = Team.objects.create(name="B", division=Team.DIVISION_MENS)
        self.suggestion = MatchSuggestion.objects.create(
            team_a=self.team_a,
            team_b=self.team_b,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        SuggestionParticipant.objects.create(
            suggestion=self.suggestion, team=self.team_a, player=self.player, side=SuggestionParticipant.SIDE_A, lineup_order=1
        )

    def assert_expired_without_booking(self):
        self.suggestion.refresh_from_db()
        self.assertEqual(self.suggestion.status, MatchSuggestion.STATUS_EXPIRED)
        for model in (Match, MatchReservation, SuggestionAcceptance, WorkflowEvent):
            self.assertEqual(model.objects.count(), 0)

    def test_expiry_survives_controlled_service_error_and_retry(self):
        for _ in range(2):
            with self.assertRaises(StaleState):
                accept_suggestion(self.player.user, self.suggestion, self.suggestion.version)
            self.assert_expired_without_booking()

    def test_expiry_survives_request_error(self):
        self.client.force_login(self.player.user)
        response = self.client.post(reverse("ladder:accept_suggestion", args=[self.suggestion.pk]), {"version": self.suggestion.version})
        self.assertEqual(response.status_code, 302)
        self.assertIn("Suggestion has expired.", [str(message) for message in get_messages(response.wsgi_request)])
        self.assert_expired_without_booking()

    def test_unselected_actor_cannot_expire_suggestion(self):
        other = PlayerProfile.objects.create(user=get_user_model().objects.create_user(username="other"), gender="M")
        with self.assertRaises(AuthorizationFailure):
            accept_suggestion(other.user, self.suggestion, self.suggestion.version)
        self.suggestion.refresh_from_db()
        self.assertEqual(self.suggestion.status, MatchSuggestion.STATUS_PROPOSED)

    def test_expiry_preserves_existing_acceptance_and_historical_times(self):
        SuggestionAcceptance.objects.create(
            suggestion=self.suggestion, team=self.team_a, accepted_by=self.player.user, accepted_version=self.suggestion.version
        )
        self.suggestion.status = MatchSuggestion.STATUS_PARTIALLY_ACCEPTED
        self.suggestion.save(update_fields=["status"])
        original_times = (self.suggestion.starts_at, self.suggestion.ends_at, self.suggestion.expires_at)
        with self.assertRaises(StaleState):
            accept_suggestion(self.player.user, self.suggestion, self.suggestion.version)
        self.suggestion.refresh_from_db()
        self.assertEqual(self.suggestion.status, MatchSuggestion.STATUS_EXPIRED)
        self.assertEqual((self.suggestion.starts_at, self.suggestion.ends_at, self.suggestion.expires_at), original_times)
        self.assertEqual(SuggestionAcceptance.objects.count(), 1)
        self.assertEqual(WorkflowEvent.objects.count(), 0)


@override_settings(TIME_ZONE="Europe/London")
class ConfiguredTimezoneTests(TestCase):
    def setUp(self):
        self.player = PlayerProfile.objects.create(user=get_user_model().objects.create_user(username="available"), gender="M")

    def test_form_service_and_legacy_parts_use_configured_zone(self):
        form = AvailabilityForm({"starts_at": "2027-06-01T18:15", "ends_at": "2027-06-01T20:15"})
        self.assertTrue(form.is_valid(), form.errors)
        slot = save_availability(self.player.user, **form.cleaned_data)
        slot.refresh_from_db()
        self.assertEqual(slot.starts_at, datetime(2027, 6, 1, 17, 15, tzinfo=datetime_timezone.utc))
        self.assertEqual(slot.start_time.hour, 18)
        self.assertEqual(slot.start_time.minute, 15)
        self.assertTrue(timezone.is_aware(slot.starts_at))

    def test_naive_service_input_uses_configured_zone(self):
        slot = save_availability(self.player.user, datetime(2027, 6, 1, 18), datetime(2027, 6, 1, 20))
        self.assertEqual(slot.starts_at, datetime(2027, 6, 1, 17, tzinfo=datetime_timezone.utc))

    def test_dst_ambiguous_and_nonexistent_wall_times_rejected(self):
        for value in ("2027-03-28T01:30", "2027-10-31T01:30"):
            with self.subTest(value=value):
                form = AvailabilityForm({"starts_at": value, "ends_at": value[:11] + "03:30"})
                self.assertFalse(form.is_valid())
                self.assertIn("starts_at", form.errors)
                start = datetime.fromisoformat(value)
                with self.assertRaises(InvalidInput):
                    save_availability(self.player.user, start, start.replace(hour=3))

    def test_request_stores_configured_local_input(self):
        self.client.force_login(self.player.user)
        response = self.client.post(reverse("ladder:availability"), {"starts_at": "2027-06-01T18:15", "ends_at": "2027-06-01T20:15"})
        self.assertEqual(response.status_code, 302)
        slot = self.player.availability_slots.get()
        self.assertEqual(slot.starts_at.hour, 17)

    def test_explicit_aware_dst_offset_preserved(self):
        start = datetime.fromisoformat("2027-10-31T01:30:00+00:00")
        slot = save_availability(self.player.user, start, datetime.fromisoformat("2027-10-31T03:30:00+00:00"))
        slot.refresh_from_db()
        self.assertEqual(slot.starts_at, start)

    @override_settings(TIME_ZONE="America/New_York")
    def test_new_york_default_remains_unchanged(self):
        form = AvailabilityForm({"starts_at": "2027-06-01T18:15", "ends_at": "2027-06-01T20:15"})
        self.assertTrue(form.is_valid(), form.errors)
        slot = save_availability(self.player.user, **form.cleaned_data)
        self.assertEqual(slot.starts_at.hour, 22)
