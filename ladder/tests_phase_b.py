import threading
import unittest
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from .models import (
    AvailabilitySlot,
    ConfirmedMatchResult,
    LadderStanding,
    Match,
    MatchParticipant,
    MatchReservation,
    MatchResultSubmission,
    MatchSuggestion,
    PlayerProfile,
    PointLedger,
    Team,
)
from .services import (
    BookingCollision,
    accept_suggestion,
    create_match_suggestion,
    finalize_match_result,
    find_opponent_suggestions,
    request_membership_change,
    save_availability,
)


@unittest.skipUnless(connection.vendor == "postgresql", "PostgreSQL row-lock concurrency coverage")
class PostgreSQLConcurrencyTests(TransactionTestCase):
    reset_sequences = True
    club_tz = ZoneInfo("America/New_York")

    def create_profile(self, username):
        user = get_user_model().objects.create_user(username=username)
        return PlayerProfile.objects.create(user=user, gender=PlayerProfile.GENDER_MALE)

    def create_team_with_members(self, name, count):
        team = Team.objects.create(name=name, division=Team.DIVISION_MENS)
        players = []
        for index in range(1, count + 1):
            profile = self.create_profile(f"{name}-{index}")
            request_membership_change(profile.user, profile, team, "join")
            players.append(profile)
        return team, players

    def make_dt(self, year, month, day, hour):
        return datetime(year, month, day, hour, tzinfo=self.club_tz)

    def add_match_participants(self, match, team_a_players, team_b_players):
        for order, player in enumerate(team_a_players, start=1):
            MatchParticipant.objects.create(
                match=match,
                team=match.team_a,
                player=player,
                side=MatchParticipant.SIDE_A,
                lineup_order=order,
            )
        for order, player in enumerate(team_b_players, start=1):
            MatchParticipant.objects.create(
                match=match,
                team=match.team_b,
                player=player,
                side=MatchParticipant.SIDE_B,
                lineup_order=order,
            )

    def create_scheduled_match(self, team_a, team_b, starts_at, ends_at):
        return Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=starts_at.date() - date.resolution * starts_at.weekday(),
            scheduled_day_of_week=list(AvailabilitySlot.DayOfWeek.values)[starts_at.weekday()],
            scheduled_start_time=starts_at.time(),
            scheduled_end_time=ends_at.time(),
            scheduled_starts_at=starts_at,
            scheduled_ends_at=ends_at,
        )

    def run_concurrently(self, callables):
        barrier = threading.Barrier(len(callables))
        results = []
        lock = threading.Lock()

        def worker(func):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                value = func()
                with lock:
                    results.append(("ok", value))
            except Exception as error:  # intentionally captured for assertions
                with lock:
                    results.append(("error", error))
            finally:
                close_old_connections()

        threads = [threading.Thread(target=worker, args=(func,)) for func in callables]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        return results

    def test_competing_suggestion_confirmations_create_one_booking(self):
        team_a, team_a_players = self.create_team_with_members("pg-book-a", 2)
        team_b, team_b_players = self.create_team_with_members("pg-book-b", 2)
        team_c, team_c_players = self.create_team_with_members("pg-book-c", 2)
        starts_at = self.make_dt(2026, 9, 1, 18)
        ends_at = self.make_dt(2026, 9, 1, 20)
        for player in team_a_players + team_b_players + team_c_players:
            save_availability(player.user, starts_at, ends_at)

        options = find_opponent_suggestions(team_a, (starts_at, ends_at))
        option_b = next(option for option in options if option["team_b"].id == team_b.id)
        option_c = next(option for option in options if option["team_b"].id == team_c.id)
        suggestion_b = create_match_suggestion(option_b, expires_at=self.make_dt(2026, 9, 2, 18))
        suggestion_c = create_match_suggestion(option_c, expires_at=self.make_dt(2026, 9, 2, 18))
        accept_suggestion(team_a_players[0].user, suggestion_b, suggestion_b.version)
        accept_suggestion(team_a_players[0].user, suggestion_c, suggestion_c.version)

        results = self.run_concurrently(
            [
                lambda: accept_suggestion(get_user_model().objects.get(pk=team_b_players[0].user_id), MatchSuggestion.objects.get(pk=suggestion_b.pk), suggestion_b.version),
                lambda: accept_suggestion(get_user_model().objects.get(pk=team_c_players[0].user_id), MatchSuggestion.objects.get(pk=suggestion_c.pk), suggestion_c.version),
            ]
        )

        successes = [value for status, value in results if status == "ok"]
        errors = [error for status, error in results if status == "error"]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], BookingCollision)
        self.assertEqual(Match.objects.count(), 1)
        self.assertEqual(MatchReservation.objects.filter(status=MatchReservation.STATUS_ACTIVE).count(), 4)
        self.assertEqual(AvailabilitySlot.objects.filter(player__in=team_a_players, status=AvailabilitySlot.STATUS_CONSUMED).count(), 2)

    def test_concurrent_finalization_of_same_match_is_idempotent(self):
        team_a, team_a_players = self.create_team_with_members("pg-final-a", 2)
        team_b, team_b_players = self.create_team_with_members("pg-final-b", 2)
        LadderStanding.objects.create(team=team_a, position=1)
        LadderStanding.objects.create(team=team_b, position=2)
        starts_at = self.make_dt(2026, 9, 2, 18)
        ends_at = self.make_dt(2026, 9, 2, 20)
        match = self.create_scheduled_match(team_a, team_b, starts_at, ends_at)
        self.add_match_participants(match, team_a_players, team_b_players)
        MatchResultSubmission.objects.create(match=match, submitting_team=team_a, submitting_user=team_a_players[0].user, team_a_sets_won=2, team_b_sets_won=0)
        MatchResultSubmission.objects.create(match=match, submitting_team=team_b, submitting_user=team_b_players[0].user, team_a_sets_won=2, team_b_sets_won=0)

        results = self.run_concurrently(
            [
                lambda: finalize_match_result(Match.objects.get(pk=match.pk)),
                lambda: finalize_match_result(Match.objects.get(pk=match.pk)),
            ]
        )

        self.assertEqual([status for status, _ in results], ["ok", "ok"])
        team_a.standing.refresh_from_db()
        team_b.standing.refresh_from_db()
        self.assertEqual(ConfirmedMatchResult.objects.filter(match=match).count(), 1)
        self.assertEqual(PointLedger.objects.filter(match=match).count(), 2)
        self.assertEqual((team_a.standing.matches_played, team_a.standing.wins, team_a.standing.points), (1, 1, 3))
        self.assertEqual((team_b.standing.matches_played, team_b.standing.losses, team_b.standing.points), (1, 1, 0))

    def test_concurrent_finalization_of_two_matches_preserves_shared_team_standing(self):
        team_a, team_a_players = self.create_team_with_members("pg-shared-a", 2)
        team_b, team_b_players = self.create_team_with_members("pg-shared-b", 2)
        team_c, team_c_players = self.create_team_with_members("pg-shared-c", 2)
        LadderStanding.objects.create(team=team_a, position=1)
        LadderStanding.objects.create(team=team_b, position=2)
        LadderStanding.objects.create(team=team_c, position=3)
        first = self.create_scheduled_match(team_a, team_b, self.make_dt(2026, 9, 3, 18), self.make_dt(2026, 9, 3, 20))
        second = self.create_scheduled_match(team_a, team_c, self.make_dt(2026, 9, 4, 18), self.make_dt(2026, 9, 4, 20))
        self.add_match_participants(first, team_a_players, team_b_players)
        self.add_match_participants(second, team_a_players, team_c_players)
        MatchResultSubmission.objects.create(match=first, submitting_team=team_a, submitting_user=team_a_players[0].user, team_a_sets_won=2, team_b_sets_won=0)
        MatchResultSubmission.objects.create(match=first, submitting_team=team_b, submitting_user=team_b_players[0].user, team_a_sets_won=2, team_b_sets_won=0)
        MatchResultSubmission.objects.create(match=second, submitting_team=team_a, submitting_user=team_a_players[1].user, team_a_sets_won=2, team_b_sets_won=0)
        MatchResultSubmission.objects.create(match=second, submitting_team=team_c, submitting_user=team_c_players[0].user, team_a_sets_won=2, team_b_sets_won=0)

        results = self.run_concurrently(
            [
                lambda: finalize_match_result(Match.objects.get(pk=first.pk)),
                lambda: finalize_match_result(Match.objects.get(pk=second.pk)),
            ]
        )

        self.assertEqual([status for status, _ in results], ["ok", "ok"])
        team_a.standing.refresh_from_db()
        self.assertEqual((team_a.standing.matches_played, team_a.standing.wins, team_a.standing.points), (2, 2, 6))
        self.assertEqual(ConfirmedMatchResult.objects.count(), 2)
        self.assertEqual(PointLedger.objects.count(), 4)
