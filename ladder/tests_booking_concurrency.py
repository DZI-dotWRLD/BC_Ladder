import threading
import unittest
from datetime import timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    AvailabilitySlot,
    Match,
    MatchReservation,
    MatchSuggestion,
    PlayerProfile,
    SuggestionParticipant,
    Team,
    TeamMembership,
    WorkflowEvent,
)
from .services import (
    AuthorizationFailure,
    BookingCollision,
    InvalidInput,
    StaleState,
    accept_suggestion,
    cancel_availability,
    cancel_match,
    create_match_suggestion,
    create_match_suggestion_from_candidate,
    find_opponent_suggestions,
    request_membership_change,
    resolve_membership_request,
    save_availability,
    sign_candidate,
)


class BookingFixtures:
    def setUp(self):
        super().setUp()
        self.start = (
            (timezone.now() + timedelta(days=5))
            .astimezone(ZoneInfo("America/New_York"))
            .replace(hour=18, minute=0, second=0, microsecond=0)
        )
        self.end = self.start + timedelta(hours=2)
        self.team_a, self.players_a = self.make_team("booking-a", 3)
        self.team_b, self.players_b = self.make_team("booking-b", 2)
        self.option = find_opponent_suggestions(self.team_a, (self.start, self.end))[0]
        self.admin = get_user_model().objects.create_user(username="booking-admin", is_staff=True)

    def make_team(self, name, count):
        team = Team.objects.create(name=name, division=Team.DIVISION_MENS)
        players = []
        for index in range(count):
            user = get_user_model().objects.create_user(username=f"{name}-{index}")
            player = PlayerProfile.objects.create(user=user, gender=PlayerProfile.GENDER_MALE)
            request_membership_change(user, player, team, "join")
            save_availability(user, self.start, self.end)
            players.append(player)
        return team, players

    def proposed(self):
        return create_match_suggestion(self.option, actor=self.players_a[0].user)

    def partially_accepted(self):
        suggestion = self.proposed()
        accept_suggestion(self.players_a[0].user, suggestion, suggestion.version)
        return suggestion

    def request_removal(self):
        return request_membership_change(self.players_a[0].user, self.players_a[0], action="request_removal")


class BookingIntegrityTests(BookingFixtures, TestCase):
    def test_retired_team_rejected_before_first_acceptance(self):
        suggestion = self.proposed()
        Team.objects.filter(pk=self.team_b.pk).update(status=Team.STATUS_RETIRED)
        with self.assertRaises(StaleState):
            accept_suggestion(self.players_a[0].user, suggestion, suggestion.version)
        self.assertEqual(suggestion.acceptances.count(), 0)

    def test_inactive_membership_cannot_fall_back_to_legacy_profile_team(self):
        suggestion = self.partially_accepted()
        TeamMembership.objects.filter(player=self.players_a[0]).update(status=TeamMembership.STATUS_INACTIVE)
        with self.assertRaises(StaleState):
            accept_suggestion(self.players_b[0].user, suggestion, suggestion.version)
        self.assertFalse(Match.objects.exists())
        self.assertFalse(MatchReservation.objects.exists())
        self.assertEqual(suggestion.acceptances.count(), 1)

    def test_participant_side_must_name_its_exact_suggestion_team(self):
        suggestion = self.partially_accepted()
        suggestion.participants.filter(side=SuggestionParticipant.SIDE_A).update(team=self.team_b)
        with self.assertRaises(InvalidInput):
            accept_suggestion(self.players_b[0].user, suggestion, suggestion.version)
        self.assertFalse(Match.objects.exists())

    def test_trusted_option_creation_revalidates_team_and_source_window(self):
        Team.objects.filter(pk=self.team_b.pk).update(status=Team.STATUS_RETIRED)
        with self.assertRaises(StaleState):
            create_match_suggestion(self.option)
        Team.objects.filter(pk=self.team_b.pk).update(status=Team.STATUS_ACTIVE)
        AvailabilitySlot.objects.filter(pk=self.option["availability"][self.players_b[0].pk].pk).update(
            status=AvailabilitySlot.STATUS_CANCELLED
        )
        with self.assertRaises(BookingCollision):
            create_match_suggestion(self.option)
        self.assertFalse(MatchSuggestion.objects.exists())

    def test_creation_rejects_unrelated_actor(self):
        with self.assertRaises(AuthorizationFailure):
            create_match_suggestion(self.option, actor=self.players_b[0].user)
        self.assertFalse(MatchSuggestion.objects.exists())

    def test_missing_source_mapping_rejects_with_controlled_error(self):
        option = {key: value for key, value in self.option.items() if key != "availability"}
        with self.assertRaises(InvalidInput):
            create_match_suggestion(option)
        self.assertFalse(MatchSuggestion.objects.exists())
        self.assertFalse(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.MATCH_REQUEST_CREATED).exists())

    def test_equivalent_reversed_lineup_creation_is_idempotent(self):
        first = self.proposed()
        reversed_option = {
            **self.option,
            "team_a_players": tuple(reversed(self.option["team_a_players"])),
            "team_b_players": tuple(reversed(self.option["team_b_players"])),
        }
        second = create_match_suggestion(reversed_option, actor=self.players_a[0].user)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(MatchSuggestion.objects.count(), 1)
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.MATCH_REQUEST_CREATED).count(), 1)

    def test_reversed_team_perspective_dedupes_without_rewriting_first_lineage(self):
        first = self.proposed()
        opposite = {
            **self.option,
            "team_a": self.team_b,
            "team_b": self.team_a,
            "team_a_players": self.option["team_b_players"],
            "team_b_players": self.option["team_a_players"],
        }
        second = create_match_suggestion(opposite, actor=self.players_b[0].user)
        self.assertEqual(first.pk, second.pk)
        first.refresh_from_db()
        self.assertEqual((first.team_a_id, first.team_b_id), (self.team_a.pk, self.team_b.pk))
        self.assertEqual(first.participants.filter(side="a").first().team_id, self.team_a.pk)

    def test_same_unknown_division_and_changed_player_division_reject(self):
        Team.objects.filter(pk__in=[self.team_a.pk, self.team_b.pk]).update(division="unknown")
        with self.assertRaises(InvalidInput):
            create_match_suggestion(self.option)
        Team.objects.filter(pk__in=[self.team_a.pk, self.team_b.pk]).update(division=Team.DIVISION_MENS)
        PlayerProfile.objects.filter(pk=self.players_a[0].pk).update(gender=PlayerProfile.GENDER_FEMALE)
        with self.assertRaises(InvalidInput):
            create_match_suggestion(self.option)
        self.assertFalse(MatchSuggestion.objects.exists())

    def test_invalid_team_and_player_shapes_reject_before_writes(self):
        for invalid in (
            {**self.option, "team_b": self.team_a},
            {**self.option, "team_b_players": self.option["team_a_players"]},
            {**self.option, "team_a_players": self.option["team_a_players"][:1]},
        ):
            with self.subTest(option=invalid), self.assertRaises(InvalidInput):
                create_match_suggestion(invalid)
        Team.objects.filter(pk=self.team_b.pk).update(division=Team.DIVISION_WOMENS)
        with self.assertRaises(InvalidInput):
            create_match_suggestion(self.option)
        self.assertFalse(MatchSuggestion.objects.exists())

    def test_confirmation_and_post_removal_retries_preserve_four_historical_players(self):
        suggestion = self.partially_accepted()
        match = accept_suggestion(self.players_b[0].user, suggestion, suggestion.version)
        removal = self.request_removal()
        resolve_membership_request(self.admin, removal, "approve")
        self.assertEqual(accept_suggestion(self.players_a[0].user, suggestion, suggestion.version).pk, match.pk)
        self.assertEqual(set(match.reservations.values_list("player_id", flat=True)), {p.pk for p in self.players_a[:2] + self.players_b})
        self.assertEqual(match.participants.count(), 4)
        self.assertTrue(self.players_a[2].availability_slots.filter(status=AvailabilitySlot.STATUS_ACTIVE).exists())
        self.client.force_login(self.players_a[0].user)
        self.assertEqual(self.client.get(reverse("ladder:match_detail", args=[match.pk])).status_code, 200)
        cancel_match(self.players_a[0].user, match)
        self.assertEqual(MatchReservation.objects.filter(status=MatchReservation.STATUS_ACTIVE).count(), 0)

    def test_request_rejects_stale_membership_without_creating_second_acceptance(self):
        suggestion = self.partially_accepted()
        resolve_membership_request(self.admin, self.request_removal(), "approve")
        self.client.force_login(self.players_b[0].user)
        response = self.client.post(reverse("ladder:accept_suggestion", args=[suggestion.pk]), {"version": suggestion.version})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Match.objects.exists())
        self.assertEqual(suggestion.acceptances.count(), 1)

    def test_confirmation_rolls_back_match_and_first_reservation_when_later_reservation_write_fails(self):
        suggestion = self.partially_accepted()
        source_availability_ids = [slot.pk for slot in self.option["availability"].values()]
        original_create = MatchReservation.objects.create
        writes = 0

        def fail_after_first_reservation(*args, **kwargs):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise RuntimeError("simulated reservation write failure")
            return original_create(*args, **kwargs)

        with patch("ladder.services.MatchReservation.objects.create", side_effect=fail_after_first_reservation):
            with self.assertRaisesRegex(RuntimeError, "simulated reservation write failure"):
                accept_suggestion(self.players_b[0].user, suggestion, suggestion.version)

        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, MatchSuggestion.STATUS_PARTIALLY_ACCEPTED)
        self.assertFalse(Match.objects.exists())
        self.assertFalse(MatchReservation.objects.exists())
        self.assertEqual(suggestion.acceptances.count(), 1)
        self.assertEqual(
            AvailabilitySlot.objects.filter(pk__in=source_availability_ids, status=AvailabilitySlot.STATUS_ACTIVE).count(),
            4,
        )
        self.assertFalse(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.MATCH_CONFIRMED).exists())


@unittest.skipUnless(connection.vendor == "postgresql", "PostgreSQL separate-connection booking contention")
class PostgreSQLBookingContentionTests(BookingFixtures, TransactionTestCase):
    def ordered_contention(self, winner, loser, player_ids):
        """Winner owns stable parents; loser attempts the same FOR UPDATE first."""
        ready, attempted = threading.Event(), threading.Event()
        results, guard, contender_lock_queries = [], threading.Lock(), []

        def run(label, operation):
            close_old_connections()
            try:
                if label == "winner":
                    with transaction.atomic():
                        list(PlayerProfile.objects.select_for_update().filter(pk__in=player_ids).order_by("pk"))
                        ready.set()
                        if not attempted.wait(10):
                            raise AssertionError("Contender never attempted a profile lock")
                        value = operation()
                else:
                    if not ready.wait(10):
                        raise AssertionError("Winner never acquired parent locks")

                    def observe(execute, sql, params, many, context):
                        if "ladder_playerprofile" in sql and "FOR UPDATE" in sql:
                            contender_lock_queries.append(sql)
                            attempted.set()
                        return execute(sql, params, many, context)

                    with connection.execute_wrapper(observe):
                        value = operation()
                result = (label, "ok", value)
            except Exception as error:
                result = (label, "error", error)
            finally:
                connection.close()
            with guard:
                results.append(result)

        threads = [threading.Thread(target=run, args=("winner", winner)), threading.Thread(target=run, args=("loser", loser))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertFalse(any(thread.is_alive() for thread in threads), "Deadlock or hung contention worker")
        self.assertTrue(attempted.is_set(), "No actual shared-parent lock contention")
        self.assertTrue(
            any("ORDER BY" in sql.upper() for sql in contender_lock_queries),
            "Contender did not acquire its shared profile locks in a stable database order",
        )
        return {label: (status, value) for label, status, value in results}

    def test_duplicate_signed_and_trusted_creation_serialize_without_existing_row(self):
        token = sign_candidate(self.option, self.players_a[0].user)
        results = self.ordered_contention(
            lambda: create_match_suggestion(self.option, actor=self.players_a[0].user),
            lambda: create_match_suggestion_from_candidate(token, self.players_a[0].user, self.team_a),
            [p.pk for p in self.players_a[:2] + self.players_b],
        )
        self.assertEqual(results["winner"][0], "ok")
        self.assertEqual(results["loser"][0], "ok")
        self.assertEqual(results["winner"][1].pk, results["loser"][1].pk)
        self.assertEqual(MatchSuggestion.objects.count(), 1)
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.MATCH_REQUEST_CREATED).count(), 1)

    def test_competing_confirmations_serialize_when_reservations_do_not_exist(self):
        team_c, players_c = self.make_team("booking-c", 2)
        option_c = next(
            option for option in find_opponent_suggestions(self.team_a, (self.start, self.end)) if option["team_b"].pk == team_c.pk
        )
        first = self.partially_accepted()
        second = create_match_suggestion(option_c)
        accept_suggestion(self.players_a[0].user, second, second.version)
        self.assertFalse(MatchReservation.objects.exists())
        results = self.ordered_contention(
            lambda: accept_suggestion(self.players_b[0].user, first, first.version),
            lambda: accept_suggestion(players_c[0].user, second, second.version),
            [p.pk for p in self.players_a[:2] + self.players_b],
        )
        self.assertEqual(results["winner"][0], "ok")
        self.assertEqual(results["loser"][0], "error")
        self.assertIsInstance(results["loser"][1], BookingCollision)
        self.assertEqual(Match.objects.count(), 1)
        self.assertEqual(MatchReservation.objects.filter(status=MatchReservation.STATUS_ACTIVE).count(), 4)
        self.assertEqual(second.acceptances.count(), 1)

    def test_removal_wins_before_confirmation_and_prevents_booking(self):
        suggestion = self.partially_accepted()
        removal = self.request_removal()
        results = self.ordered_contention(
            lambda: resolve_membership_request(self.admin, removal, "approve"),
            lambda: accept_suggestion(self.players_b[0].user, suggestion, suggestion.version),
            [self.players_a[0].pk],
        )
        self.assertEqual(results["winner"][0], "ok")
        self.assertEqual(results["loser"][0], "error")
        self.assertIsInstance(results["loser"][1], StaleState)
        self.assertFalse(Match.objects.exists())
        self.assertFalse(MatchReservation.objects.exists())

    def test_confirmation_wins_then_removal_preserves_history(self):
        suggestion = self.partially_accepted()
        removal = self.request_removal()
        results = self.ordered_contention(
            lambda: accept_suggestion(self.players_b[0].user, suggestion, suggestion.version),
            lambda: resolve_membership_request(self.admin, removal, "approve"),
            [p.pk for p in self.players_a[:2] + self.players_b],
        )
        self.assertEqual(results["winner"][0], "ok")
        self.assertEqual(results["loser"][0], "ok")
        self.assertEqual(Match.objects.count(), 1)
        self.assertEqual(MatchReservation.objects.filter(status=MatchReservation.STATUS_ACTIVE).count(), 4)
        self.assertEqual(accept_suggestion(self.players_a[0].user, suggestion, suggestion.version).pk, results["winner"][1].pk)

    def test_availability_cancellation_wins_before_confirmation(self):
        suggestion = self.partially_accepted()
        slot = self.option["availability"][self.players_a[0].pk]
        results = self.ordered_contention(
            lambda: cancel_availability(self.players_a[0].user, slot),
            lambda: accept_suggestion(self.players_b[0].user, suggestion, suggestion.version),
            [self.players_a[0].pk],
        )
        self.assertEqual(results["winner"][0], "ok")
        self.assertEqual(results["loser"][0], "error")
        self.assertIsInstance(results["loser"][1], BookingCollision)
        self.assertFalse(Match.objects.exists())
