from datetime import date, datetime, time, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from config.settings import DEVELOPMENT_SECRET_KEY, build_allowed_hosts, build_database_config, production_settings_errors

from .models import (
    AdminNotification,
    AvailabilitySlot,
    Challenge,
    ConfirmedMatchResult,
    LadderStanding,
    Match,
    MatchParticipant,
    MatchReservation,
    MatchResultSubmission,
    MatchSuggestion,
    PlayerProfile,
    PointLedger,
    ScoreCorrectionAudit,
    Team,
    TeamMembership,
    WorkflowEvent,
)
from .services import (
    AuthorizationFailure,
    InvalidInput,
    StaleState,
    accept_suggestion,
    cancel_availability,
    cancel_join_request,
    cancel_match,
    create_admin_notification_for_conflict,
    create_match_suggestion,
    create_team_for_player,
    find_opponent_suggestions,
    find_team_match_options,
    finalize_match_result,
    generate_team_lineups,
    get_pair_matching_slots,
    get_player_pairs,
    get_team_pair_availability,
    get_match_status,
    complete_match_if_result_confirmed,
    get_match_winner_and_loser,
    recalculate_ladder_positions,
    reconcile_ladder_standings,
    request_membership_change,
    resolve_membership_request,
    resolve_score_conflict,
    save_availability,
    submit_match_result,
    validate_match_score,
)


class TeamMatchOptionTests(TestCase):
    def setUp(self):
        self.week_start_date = date(2026, 6, 1)
        self.team_a = Team.objects.create(name="Team A", division=Team.DIVISION_MENS)
        self.team_b = Team.objects.create(name="Team B", division=Team.DIVISION_MENS)

        self.team_a_players = [self.create_player(f"a-player-{index}", self.team_a) for index in range(1, 3)]
        self.team_b_players = [self.create_player(f"b-player-{index}", self.team_b) for index in range(1, 3)]

    def create_player(self, username, team):
        user = get_user_model().objects.create_user(username=username)
        return PlayerProfile.objects.create(
            user=user,
            gender=PlayerProfile.GENDER_MALE,
            team=team,
        )

    def create_slot(self, player, day, start, end, week_start_date=None):
        return AvailabilitySlot.objects.create(
            player=player,
            week_start_date=week_start_date or self.week_start_date,
            day_of_week=day,
            start_time=start,
            end_time=end,
        )

    def test_get_player_pairs_returns_all_two_player_combinations(self):
        third_player = self.create_player("a-player-3", self.team_a)

        pairs = get_player_pairs(self.team_a)
        pair_ids = [{pair[0].id, pair[1].id} for pair in pairs]

        self.assertEqual(len(pairs), 3)
        self.assertIn(
            {self.team_a_players[0].id, self.team_a_players[1].id},
            pair_ids,
        )
        self.assertIn(
            {self.team_a_players[0].id, third_player.id},
            pair_ids,
        )
        self.assertIn(
            {self.team_a_players[1].id, third_player.id},
            pair_ids,
        )

    def test_get_pair_matching_slots_returns_exact_matches_only(self):
        matching_slot = self.create_slot(
            self.team_a_players[0],
            AvailabilitySlot.DayOfWeek.MONDAY,
            time(18, 0),
            time(19, 0),
        )
        self.create_slot(
            self.team_a_players[0],
            AvailabilitySlot.DayOfWeek.TUESDAY,
            time(18, 0),
            time(19, 0),
        )
        self.create_slot(
            self.team_a_players[1],
            AvailabilitySlot.DayOfWeek.MONDAY,
            time(18, 0),
            time(19, 0),
        )

        matching_slots = get_pair_matching_slots(
            self.team_a_players[0],
            self.team_a_players[1],
            self.week_start_date,
        )

        self.assertEqual(matching_slots, [matching_slot])

    def test_get_team_pair_availability_returns_only_pairs_with_shared_slots(self):
        third_player = self.create_player("a-player-3", self.team_a)

        monday_slot = self.create_slot(
            self.team_a_players[0],
            AvailabilitySlot.DayOfWeek.MONDAY,
            time(18, 0),
            time(19, 0),
        )
        self.create_slot(
            self.team_a_players[1],
            AvailabilitySlot.DayOfWeek.MONDAY,
            time(18, 0),
            time(19, 0),
        )
        wednesday_slot = self.create_slot(
            self.team_a_players[0],
            AvailabilitySlot.DayOfWeek.WEDNESDAY,
            time(19, 0),
            time(20, 0),
        )
        self.create_slot(
            third_player,
            AvailabilitySlot.DayOfWeek.WEDNESDAY,
            time(19, 0),
            time(20, 0),
        )

        pair_availability = get_team_pair_availability(
            self.team_a,
            self.week_start_date,
        )
        result_pair_ids = [{result["players"][0].id, result["players"][1].id} for result in pair_availability]

        self.assertEqual(len(pair_availability), 2)
        self.assertIn(
            {self.team_a_players[0].id, self.team_a_players[1].id},
            result_pair_ids,
        )
        self.assertIn(
            {self.team_a_players[0].id, third_player.id},
            result_pair_ids,
        )
        self.assertEqual(pair_availability[0]["slots"], [monday_slot])
        self.assertEqual(pair_availability[1]["slots"], [wednesday_slot])

    def test_find_team_match_options_returns_shared_team_pair_slots(self):
        team_a_slot = self.create_slot(
            self.team_a_players[0],
            AvailabilitySlot.DayOfWeek.MONDAY,
            time(18, 0),
            time(19, 0),
        )
        self.create_slot(
            self.team_a_players[1],
            AvailabilitySlot.DayOfWeek.MONDAY,
            time(18, 0),
            time(19, 0),
        )
        self.create_slot(
            self.team_b_players[0],
            AvailabilitySlot.DayOfWeek.MONDAY,
            time(18, 0),
            time(19, 0),
        )
        self.create_slot(
            self.team_b_players[1],
            AvailabilitySlot.DayOfWeek.MONDAY,
            time(18, 0),
            time(19, 0),
        )

        match_options = find_team_match_options(
            self.team_a,
            self.team_b,
            self.week_start_date,
        )

        self.assertEqual(
            match_options,
            [
                {
                    "team_a_players": tuple(self.team_a_players),
                    "team_b_players": tuple(self.team_b_players),
                    "slot": team_a_slot,
                }
            ],
        )

    def test_find_team_match_options_returns_empty_list_when_no_slots_match(self):
        self.create_slot(
            self.team_a_players[0],
            AvailabilitySlot.DayOfWeek.MONDAY,
            time(18, 0),
            time(19, 0),
        )
        self.create_slot(
            self.team_a_players[1],
            AvailabilitySlot.DayOfWeek.MONDAY,
            time(18, 0),
            time(19, 0),
        )
        self.create_slot(
            self.team_b_players[0],
            AvailabilitySlot.DayOfWeek.TUESDAY,
            time(18, 0),
            time(19, 0),
        )
        self.create_slot(
            self.team_b_players[1],
            AvailabilitySlot.DayOfWeek.TUESDAY,
            time(18, 0),
            time(19, 0),
        )

        match_options = find_team_match_options(
            self.team_a,
            self.team_b,
            self.week_start_date,
        )

        self.assertEqual(match_options, [])


class ModelValidationTests(TestCase):
    def setUp(self):
        self.week_start_date = date(2026, 6, 1)
        self.mens_team = Team.objects.create(
            name="Men's Team",
            division=Team.DIVISION_MENS,
        )
        self.womens_team = Team.objects.create(
            name="Women's Team",
            division=Team.DIVISION_WOMENS,
        )

    def create_player(self, username, gender, team=None):
        user = get_user_model().objects.create_user(username=username)
        return PlayerProfile.objects.create(
            user=user,
            gender=gender,
            team=team,
        )

    def test_male_player_cannot_join_womens_team(self):
        user = get_user_model().objects.create_user(username="male-player")
        profile = PlayerProfile(
            user=user,
            gender=PlayerProfile.GENDER_MALE,
            team=self.womens_team,
        )

        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_female_player_cannot_join_mens_team(self):
        user = get_user_model().objects.create_user(username="female-player")
        profile = PlayerProfile(
            user=user,
            gender=PlayerProfile.GENDER_FEMALE,
            team=self.mens_team,
        )

        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_fourth_player_cannot_join_team(self):
        for index in range(1, 4):
            self.create_player(
                f"team-player-{index}",
                PlayerProfile.GENDER_MALE,
                self.mens_team,
            )

        user = get_user_model().objects.create_user(username="team-player-4")
        profile = PlayerProfile(
            user=user,
            gender=PlayerProfile.GENDER_MALE,
            team=self.mens_team,
        )

        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_availability_slot_start_time_must_be_before_end_time(self):
        player = self.create_player(
            "availability-player",
            PlayerProfile.GENDER_MALE,
            self.mens_team,
        )
        slot = AvailabilitySlot(
            player=player,
            week_start_date=self.week_start_date,
            day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            start_time=time(18, 0),
            end_time=time(18, 0),
        )

        with self.assertRaises(ValidationError):
            slot.full_clean()

    def test_duplicate_availability_slot_is_blocked(self):
        player = self.create_player(
            "duplicate-player",
            PlayerProfile.GENDER_MALE,
            self.mens_team,
        )
        slot_data = {
            "player": player,
            "week_start_date": self.week_start_date,
            "day_of_week": AvailabilitySlot.DayOfWeek.MONDAY,
            "start_time": time(18, 0),
            "end_time": time(19, 0),
        }
        AvailabilitySlot.objects.create(**slot_data)

        with self.assertRaises(IntegrityError), transaction.atomic():
            AvailabilitySlot.objects.create(**slot_data)

    def test_availability_interval_requires_both_timestamp_bounds(self):
        player = self.create_player(
            "half-null-availability-player",
            PlayerProfile.GENDER_MALE,
            self.mens_team,
        )
        slot_data = {
            "player": player,
            "week_start_date": self.week_start_date,
            "day_of_week": AvailabilitySlot.DayOfWeek.MONDAY,
            "start_time": time(18, 0),
            "end_time": time(19, 0),
            "starts_at": datetime(2026, 6, 1, 18, tzinfo=ZoneInfo("America/New_York")),
            "ends_at": None,
        }

        with self.assertRaises(ValidationError):
            AvailabilitySlot(**slot_data).full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AvailabilitySlot.objects.create(**slot_data)


class ChallengeModelTests(TestCase):
    def setUp(self):
        self.week_start_date = date(2026, 6, 1)
        self.team_a = Team.objects.create(name="Challenge Team A", division=Team.DIVISION_MENS)
        self.team_b = Team.objects.create(name="Challenge Team B", division=Team.DIVISION_MENS)
        self.womens_team = Team.objects.create(
            name="Challenge Women's Team",
            division=Team.DIVISION_WOMENS,
        )

    def test_challenge_can_be_created_for_two_teams_in_same_division(self):
        challenge = Challenge(
            challenger_team=self.team_a,
            opponent_team=self.team_b,
            proposed_week_start_date=self.week_start_date,
            proposed_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            proposed_start_time=time(18, 0),
            proposed_end_time=time(19, 0),
        )

        challenge.full_clean()
        challenge.save()

        self.assertEqual(challenge.status, Challenge.STATUS_PENDING)

    def test_team_cannot_challenge_itself(self):
        challenge = Challenge(
            challenger_team=self.team_a,
            opponent_team=self.team_a,
            proposed_week_start_date=self.week_start_date,
            proposed_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            proposed_start_time=time(18, 0),
            proposed_end_time=time(19, 0),
        )

        with self.assertRaises(ValidationError):
            challenge.full_clean()

    def test_challenge_teams_must_be_in_same_division(self):
        challenge = Challenge(
            challenger_team=self.team_a,
            opponent_team=self.womens_team,
            proposed_week_start_date=self.week_start_date,
            proposed_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            proposed_start_time=time(18, 0),
            proposed_end_time=time(19, 0),
        )

        with self.assertRaises(ValidationError):
            challenge.full_clean()

    def test_challenge_start_time_must_be_before_end_time(self):
        challenge = Challenge(
            challenger_team=self.team_a,
            opponent_team=self.team_b,
            proposed_week_start_date=self.week_start_date,
            proposed_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            proposed_start_time=time(19, 0),
            proposed_end_time=time(18, 0),
        )

        with self.assertRaises(ValidationError):
            challenge.full_clean()


class MatchModelTests(TestCase):
    def setUp(self):
        self.week_start_date = date(2026, 6, 1)
        self.team_a = Team.objects.create(name="Challenge Team A", division=Team.DIVISION_MENS)
        self.team_b = Team.objects.create(name="Challenge Team B", division=Team.DIVISION_MENS)
        self.womens_team = Team.objects.create(
            name="Challenge Women's Team",
            division=Team.DIVISION_WOMENS,
        )

    def test_match_can_be_created_for_two_teams_in_same_division(self):
        match = Match(
            team_a=self.team_a,
            team_b=self.team_b,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(19, 0),
        )

        match.full_clean()

        self.assertEqual(match.status, Match.STATUS_SCHEDULED)

    def test_match_team_cannot_play_itself(self):
        match = Match(
            team_a=self.team_a,
            team_b=self.team_a,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(19, 0),
        )

        with self.assertRaises(ValidationError):
            match.full_clean()

    def test_match_teams_must_be_in_same_division(self):
        match = Match(
            team_a=self.team_a,
            team_b=self.womens_team,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(19, 0),
        )

        with self.assertRaises(ValidationError):
            match.full_clean()

    def test_match_start_time_must_be_before_end_time(self):
        match = Match(
            team_a=self.team_a,
            team_b=self.team_b,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(19, 0),
            scheduled_end_time=time(18, 0),
        )

        with self.assertRaises(ValidationError):
            match.full_clean()

    def test_match_start_time_must_be_not_equal_end_time(self):
        match = Match(
            team_a=self.team_a,
            team_b=self.team_b,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(18, 0),
        )

        with self.assertRaises(ValidationError):
            match.full_clean()


class MatchResultSubmissionTests(TestCase):
    def setUp(self):
        self.week_start_date = date(2026, 6, 1)
        self.team_a = Team.objects.create(
            name="Team A",
            division=Team.DIVISION_MENS,
        )
        self.team_b = Team.objects.create(
            name="Team B",
            division=Team.DIVISION_MENS,
        )
        self.outside_team = Team.objects.create(
            name="Outside Team",
            division=Team.DIVISION_MENS,
        )

        self.match = Match.objects.create(
            team_a=self.team_a,
            team_b=self.team_b,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(19, 0),
        )

    def test_result_submission_can_be_created_by_team_a(self):
        submission = MatchResultSubmission(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        submission.full_clean()

    def test_result_submission_can_be_created_by_team_b(self):
        submission = MatchResultSubmission(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        submission.full_clean()

    def test_result_submission_rejects_team_not_in_match(self):
        submission = MatchResultSubmission(
            match=self.match,
            submitting_team=self.outside_team,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        with self.assertRaises(ValidationError):
            submission.full_clean()

    def test_same_team_cannot_submit_twice_for_same_match(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        duplicate_submission = MatchResultSubmission(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        with self.assertRaises(ValidationError):
            duplicate_submission.full_clean()


class MatchResultStatusServiceTests(TestCase):
    def setUp(self):
        self.week_start_date = date(2026, 6, 1)
        self.team_a = Team.objects.create(
            name="Team A",
            division=Team.DIVISION_MENS,
        )
        self.team_b = Team.objects.create(
            name="Team B",
            division=Team.DIVISION_MENS,
        )

        self.match = Match.objects.create(
            team_a=self.team_a,
            team_b=self.team_b,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(19, 0),
        )

    def test_match_result_status_waiting_when_less_than_two_submissions(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        status = get_match_status(self.match)

        self.assertEqual(status, "waiting_for_submissions")

    def test_match_result_status_confirmed_when_submissions_match(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        status = get_match_status(self.match)

        self.assertEqual(status, "confirmed")

    def test_match_result_status_conflict_when_submissions_disagree(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=1,
            team_b_sets_won=2,
        )

        status = get_match_status(self.match)

        self.assertEqual(status, "conflict")

    def test_complete_match_when_result_confirmed(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        completed = complete_match_if_result_confirmed(self.match)

        self.match.refresh_from_db()
        self.assertTrue(completed)
        self.assertEqual(self.match.status, Match.STATUS_COMPLETED)

    def test_does_not_complete_match_when_result_conflicts(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=1,
            team_b_sets_won=2,
        )

        completed = complete_match_if_result_confirmed(self.match)

        self.match.refresh_from_db()
        self.assertFalse(completed)
        self.assertEqual(self.match.status, Match.STATUS_SCHEDULED)

    def test_does_not_complete_match_when_waiting_for_submissions(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        completed = complete_match_if_result_confirmed(self.match)

        self.match.refresh_from_db()
        self.assertFalse(completed)
        self.assertEqual(self.match.status, Match.STATUS_SCHEDULED)

    def test_get_match_winner_and_loser_returns_team_a_when_team_a_wins(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        result = get_match_winner_and_loser(self.match)

        self.assertEqual(result, [self.team_a, self.team_b])

    def test_get_match_winner_and_loser_returns_team_b_when_team_b_wins(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=1,
            team_b_sets_won=2,
        )
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=1,
            team_b_sets_won=2,
        )

        result = get_match_winner_and_loser(self.match)

        self.assertEqual(result, [self.team_b, self.team_a])

    def test_get_match_winner_and_loser_returns_none_when_result_conflicts(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=1,
            team_b_sets_won=2,
        )

        winner = get_match_winner_and_loser(self.match)

        self.assertEqual(winner, [])

    def test_get_match_winner__and_loser_returns_none_when_waiting_for_submissions(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        winner = get_match_winner_and_loser(self.match)

        self.assertEqual(winner, [])


class AdminNotificationTests(TestCase):
    def setUp(self):
        self.week_start_date = date(2026, 6, 1)

        self.team_a = Team.objects.create(
            name="Team A",
            division=Team.DIVISION_MENS,
        )
        self.team_b = Team.objects.create(
            name="Team B",
            division=Team.DIVISION_MENS,
        )

        self.match = Match.objects.create(
            team_a=self.team_a,
            team_b=self.team_b,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(19, 0),
        )

    def test_admin_notification_can_be_created_for_match(self):
        notification = AdminNotification(
            match=self.match,
            message="Scores do not match.",
        )

        notification.full_clean()
        notification.save()

        self.assertEqual(notification.match, self.match)

    def test_admin_notification_defaults_to_unresolved(self):
        notification = AdminNotification.objects.create(
            match=self.match,
            message="Scores do not match.",
        )

        self.assertFalse(notification.is_resolved)

    def test_match_can_access_admin_notifications(self):
        notification = AdminNotification.objects.create(
            match=self.match,
            message="Scores do not match.",
        )

        self.assertIn(notification, self.match.admin_notifications.all())

    def test_conflict_notification_is_created_when_result_conflict(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=1,
            team_b_sets_won=2,
        )

        notification = create_admin_notification_for_conflict(self.match)

        self.assertIsNotNone(notification)
        self.assertEqual(notification.match, self.match)
        self.assertEqual(AdminNotification.objects.count(), 1)

    def test_conflict_notification_is_not_created_when_result_confirmed(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        notification = create_admin_notification_for_conflict(self.match)

        self.assertIsNone(notification)
        self.assertEqual(AdminNotification.objects.count(), 0)

    def test_conflict_notification_is_not_created_when_waiting_for_submissions(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        notification = create_admin_notification_for_conflict(self.match)

        self.assertIsNone(notification)
        self.assertEqual(AdminNotification.objects.count(), 0)


class RemediationServiceTests(TestCase):
    club_tz = ZoneInfo("America/New_York")

    def create_profile(self, username, gender=PlayerProfile.GENDER_MALE, team=None, is_staff=False):
        user = get_user_model().objects.create_user(username=username)
        user.is_staff = is_staff
        user.save(update_fields=["is_staff"])
        return PlayerProfile.objects.create(user=user, gender=gender, team=team)

    def make_dt(self, year, month, day, hour, minute=0):
        return datetime(year, month, day, hour, minute, tzinfo=self.club_tz)

    def create_team_with_members(self, name, count, division=Team.DIVISION_MENS):
        team = Team.objects.create(name=name, division=division)
        players = []
        for index in range(1, count + 1):
            gender = PlayerProfile.GENDER_MALE if division == Team.DIVISION_MENS else PlayerProfile.GENDER_FEMALE
            profile = self.create_profile(f"{name}-{index}", gender=gender)
            request_membership_change(profile.user, profile, team, "join")
            players.append(profile)
        return team, players

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

    def test_membership_join_enforces_one_active_team_and_three_member_limit(self):
        team, players = self.create_team_with_members("members", 3)
        other_team = Team.objects.create(name="other", division=Team.DIVISION_MENS)

        with self.assertRaises(InvalidInput):
            request_membership_change(players[0].user, players[0], other_team, "join")

        fourth = self.create_profile("fourth")
        with self.assertRaises(InvalidInput):
            request_membership_change(fourth.user, fourth, team, "join")

        self.assertEqual(TeamMembership.objects.filter(team=team, status=TeamMembership.STATUS_ACTIVE).count(), 3)

    def test_create_team_for_player_derives_division_membership_and_standing(self):
        male = self.create_profile("create-mens", gender=PlayerProfile.GENDER_MALE)
        female = self.create_profile("create-womens", gender=PlayerProfile.GENDER_FEMALE)

        mens_team, mens_membership = create_team_for_player(male.user, male, "  Black Label  ")
        womens_team, womens_membership = create_team_for_player(female.user, female, "Red Room")

        male.refresh_from_db()
        female.refresh_from_db()
        self.assertEqual(mens_team.name, "Black Label")
        self.assertEqual(mens_team.division, Team.DIVISION_MENS)
        self.assertEqual(womens_team.division, Team.DIVISION_WOMENS)
        self.assertEqual(mens_membership.status, TeamMembership.STATUS_ACTIVE)
        self.assertEqual(womens_membership.status, TeamMembership.STATUS_ACTIVE)
        self.assertEqual(male.team, mens_team)
        self.assertEqual(female.team, womens_team)
        self.assertTrue(LadderStanding.objects.filter(team=mens_team).exists())
        self.assertTrue(LadderStanding.objects.filter(team=womens_team).exists())

    def test_create_team_for_player_rejects_existing_membership_and_duplicate_name(self):
        team, players = self.create_team_with_members("existing-membership", 1)

        with self.assertRaises(InvalidInput):
            create_team_for_player(players[0].user, players[0], "New Team")

        free_player = self.create_profile("duplicate-team-player")
        with self.assertRaises(InvalidInput):
            create_team_for_player(free_player.user, free_player, team.name.lower())

        self.assertFalse(Team.objects.filter(name="New Team").exists())

    def test_admin_resolves_removal_request_without_deleting_history(self):
        team, players = self.create_team_with_members("remove", 1)
        membership = request_membership_change(players[0].user, players[0], action="request_removal")
        admin_profile = self.create_profile("admin", is_staff=True)

        request = TeamMembership.objects.get(pk=membership.pk)
        resolved = resolve_membership_request(admin_profile.user, request, "approve")

        players[0].refresh_from_db()
        self.assertEqual(resolved.status, TeamMembership.STATUS_INACTIVE)
        self.assertIsNone(players[0].team)
        self.assertTrue(TeamMembership.objects.filter(team=team, player=players[0]).exists())

    def test_player_join_request_is_pending_idempotent_and_admin_approved(self):
        team = Team.objects.create(name="join-request-team", division=Team.DIVISION_MENS)
        player = self.create_profile("join-request-player")
        admin_profile = self.create_profile("join-request-admin", is_staff=True)

        first = request_membership_change(player.user, player, team, "request_join")
        second = request_membership_change(player.user, player, team, "request_join")

        player.refresh_from_db()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.status, TeamMembership.STATUS_JOIN_REQUESTED)
        self.assertIsNone(player.team)

        approved = resolve_membership_request(admin_profile.user, first, "approve")

        player.refresh_from_db()
        self.assertEqual(approved.status, TeamMembership.STATUS_ACTIVE)
        self.assertEqual(player.team, team)

    def test_admin_join_approval_rechecks_capacity_and_rejects_cleanly(self):
        team, _players = self.create_team_with_members("join-full", 2)
        requester = self.create_profile("join-full-requester")
        request = request_membership_change(requester.user, requester, team, "request_join")
        late_player = self.create_profile("join-full-late")
        request_membership_change(late_player.user, late_player, team, "join")
        admin_profile = self.create_profile("join-full-admin", is_staff=True)

        with self.assertRaises(InvalidInput):
            resolve_membership_request(admin_profile.user, request, "approve")

        requester.refresh_from_db()
        request.refresh_from_db()
        self.assertIsNone(requester.team)
        self.assertEqual(request.status, TeamMembership.STATUS_JOIN_REQUESTED)
        self.assertFalse(
            WorkflowEvent.objects.filter(
                membership=request,
                event_type=WorkflowEvent.EventType.JOIN_REQUEST_APPROVED,
            ).exists()
        )

    def test_admin_rejects_join_request_without_active_membership(self):
        team = Team.objects.create(name="join-reject-team", division=Team.DIVISION_MENS)
        player = self.create_profile("join-reject-player")
        admin_profile = self.create_profile("join-reject-admin", is_staff=True)
        request = request_membership_change(player.user, player, team, "request_join")

        rejected = resolve_membership_request(admin_profile.user, request, "reject")

        player.refresh_from_db()
        self.assertEqual(rejected.status, TeamMembership.STATUS_INACTIVE)
        self.assertIsNone(player.team)

    def test_player_can_cancel_own_join_request_only(self):
        team = Team.objects.create(name="join-cancel-team", division=Team.DIVISION_MENS)
        player = self.create_profile("join-cancel-player")
        other = self.create_profile("join-cancel-other")
        request = request_membership_change(player.user, player, team, "request_join")

        with self.assertRaises(AuthorizationFailure):
            cancel_join_request(other.user, player)

        cancelled = cancel_join_request(player.user, player)

        request.refresh_from_db()
        player.refresh_from_db()
        self.assertEqual(cancelled.pk, request.pk)
        self.assertEqual(request.status, TeamMembership.STATUS_INACTIVE)
        self.assertEqual(request.resolution_note, "Cancelled by player.")
        self.assertIsNone(player.team)

    def test_save_availability_is_idempotent_and_rejects_overlap(self):
        team, players = self.create_team_with_members("availability", 1)
        starts_at = self.make_dt(2026, 7, 6, 18)
        ends_at = self.make_dt(2026, 7, 6, 20)

        first = save_availability(players[0].user, starts_at, ends_at)
        second = save_availability(players[0].user, starts_at, ends_at)

        self.assertEqual(first, second)
        with self.assertRaises(InvalidInput):
            save_availability(players[0].user, self.make_dt(2026, 7, 6, 19), self.make_dt(2026, 7, 6, 21))
        self.assertEqual(AvailabilitySlot.objects.filter(player=players[0]).count(), 1)

    def test_generate_team_lineups_returns_three_stable_interval_pairings(self):
        team, players = self.create_team_with_members("lineups", 3)
        starts_at = self.make_dt(2026, 7, 7, 18)
        ends_at = self.make_dt(2026, 7, 7, 20)
        for player in players:
            save_availability(player.user, starts_at, ends_at)

        lineups = generate_team_lineups(team, (starts_at, ends_at))
        lineup_ids = [tuple(player.id for player in lineup["players"]) for lineup in lineups]

        self.assertEqual(
            lineup_ids,
            [
                (players[0].id, players[1].id),
                (players[0].id, players[2].id),
                (players[1].id, players[2].id),
            ],
        )

    def test_dual_acceptance_confirms_match_and_consumes_only_four_players(self):
        team_a, team_a_players = self.create_team_with_members("accept-a", 3)
        team_b, team_b_players = self.create_team_with_members("accept-b", 2)
        starts_at = self.make_dt(2026, 7, 8, 18)
        ends_at = self.make_dt(2026, 7, 8, 20)
        for player in team_a_players[:2] + team_b_players:
            save_availability(player.user, starts_at, ends_at)
        third_slot = save_availability(team_a_players[2].user, starts_at, ends_at)

        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option, expires_at=datetime(2026, 9, 1, tzinfo=self.club_tz))

        partial = accept_suggestion(team_a_players[0].user, suggestion, suggestion.version)
        match = accept_suggestion(team_b_players[0].user, suggestion, suggestion.version)
        retry = accept_suggestion(team_b_players[0].user, suggestion, suggestion.version)

        third_slot.refresh_from_db()
        self.assertEqual(partial.status, MatchSuggestion.STATUS_PARTIALLY_ACCEPTED)
        self.assertEqual(match, retry)
        self.assertEqual(MatchReservation.objects.filter(match=match).count(), 4)
        self.assertEqual(AvailabilitySlot.objects.filter(status=AvailabilitySlot.STATUS_CONSUMED).count(), 4)
        self.assertEqual(third_slot.status, AvailabilitySlot.STATUS_ACTIVE)

    def test_non_lineup_teammate_cannot_accept_suggestion(self):
        team_a, team_a_players = self.create_team_with_members("accept-auth-a", 3)
        team_b, team_b_players = self.create_team_with_members("accept-auth-b", 2)
        starts_at = self.make_dt(2026, 7, 8, 18)
        ends_at = self.make_dt(2026, 7, 8, 20)
        for player in team_a_players + team_b_players:
            save_availability(player.user, starts_at, ends_at)

        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option, expires_at=datetime(2026, 9, 1, tzinfo=self.club_tz))

        with self.assertRaises(AuthorizationFailure):
            accept_suggestion(team_a_players[2].user, suggestion, suggestion.version)

    def test_duplicate_suggestion_returns_existing_active_suggestion(self):
        team_a, team_a_players = self.create_team_with_members("suggest-dup-a", 2)
        team_b, team_b_players = self.create_team_with_members("suggest-dup-b", 2)
        starts_at = self.make_dt(2026, 7, 9, 18)
        ends_at = self.make_dt(2026, 7, 9, 20)
        for player in team_a_players + team_b_players:
            save_availability(player.user, starts_at, ends_at)

        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        first = create_match_suggestion(option)
        second = create_match_suggestion(option)

        self.assertEqual(first, second)
        self.assertEqual(MatchSuggestion.objects.count(), 1)
        self.assertEqual(first.expires_at, starts_at)

    def test_score_validation_accepts_match_tiebreak_and_rejects_ten_nine(self):
        for breaker in [(10, 8), (11, 9), (12, 10)]:
            score = validate_match_score([(6, 4), (4, 6), breaker])
            self.assertEqual(score["winner_team_side"], "team_a")

        with self.assertRaises(InvalidInput):
            validate_match_score([(6, 4), (4, 6), (10, 9)])
        with self.assertRaises(InvalidInput):
            validate_match_score([(6, 4), (7, 5), (10, 8)])

    def test_score_submission_authorization_conflict_and_exactly_once_points(self):
        team_a, team_a_players = self.create_team_with_members("score-a", 3)
        team_b, team_b_players = self.create_team_with_members("score-b", 2)
        _, outside_players = self.create_team_with_members("score-outside", 1)
        LadderStanding.objects.create(team=team_a, position=1)
        LadderStanding.objects.create(team=team_b, position=2)
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 7, 6),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2026, 7, 6, 18),
            scheduled_ends_at=self.make_dt(2026, 7, 6, 20),
        )
        self.add_match_participants(match, team_a_players[:2], team_b_players)

        with self.assertRaises(AuthorizationFailure):
            submit_match_result(outside_players[0].user, match, [(6, 4), (6, 4)])
        with self.assertRaises(AuthorizationFailure):
            submit_match_result(team_a_players[2].user, match, [(6, 4), (6, 4)])

        first = submit_match_result(team_a_players[0].user, match, [(6, 4), (6, 4)])
        duplicate = submit_match_result(team_a_players[0].user, match, [(6, 4), (6, 4)])
        self.assertEqual(first, duplicate)

        submit_match_result(team_b_players[0].user, match, [(4, 6), (4, 6)])
        create_admin_notification_for_conflict(match)
        create_admin_notification_for_conflict(match)
        self.assertEqual(AdminNotification.objects.filter(match=match, is_resolved=False).count(), 1)

        confirmed_match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 7, 13),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2026, 7, 13, 18),
            scheduled_ends_at=self.make_dt(2026, 7, 13, 20),
        )
        self.add_match_participants(confirmed_match, team_a_players[:2], team_b_players)
        submit_match_result(team_a_players[0].user, confirmed_match, [(6, 4), (6, 4)])
        submit_match_result(team_b_players[0].user, confirmed_match, [(6, 4), (6, 4)])
        finalize_match_result(confirmed_match)
        finalize_match_result(confirmed_match)
        team_a.standing.refresh_from_db()
        team_b.standing.refresh_from_db()

        self.assertEqual(team_a.standing.points, 3)
        self.assertEqual(team_a.standing.wins, 1)
        self.assertEqual(team_b.standing.losses, 1)
        self.assertEqual(ConfirmedMatchResult.objects.count(), 1)
        self.assertEqual(PointLedger.objects.filter(match=confirmed_match).count(), 2)

    def test_ladder_positions_use_approved_tiebreak_order(self):
        alpha = Team.objects.create(name="Alpha Club", division=Team.DIVISION_MENS)
        beta = Team.objects.create(name="Beta Club", division=Team.DIVISION_MENS)
        gamma = Team.objects.create(name="Gamma Club", division=Team.DIVISION_MENS)
        LadderStanding.objects.create(team=gamma, position=1, points=6, wins=2, losses=2, matches_played=4)
        LadderStanding.objects.create(team=beta, position=2, points=6, wins=2, losses=1, matches_played=3)
        LadderStanding.objects.create(team=alpha, position=3, points=6, wins=2, losses=1, matches_played=3)

        recalculate_ladder_positions(Team.DIVISION_MENS)

        ordered = list(LadderStanding.objects.order_by("position").values_list("team__name", flat=True))
        self.assertEqual(ordered, ["Alpha Club", "Beta Club", "Gamma Club"])

    def test_reconcile_ladder_standings_rebuilds_stats_from_confirmed_results(self):
        team_a, team_a_players = self.create_team_with_members("reconcile-a", 2)
        team_b, team_b_players = self.create_team_with_members("reconcile-b", 2)
        LadderStanding.objects.create(team=team_a, position=2, points=99, wins=10, losses=0, matches_played=10)
        LadderStanding.objects.create(team=team_b, position=1, points=99, wins=10, losses=0, matches_played=10)
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 7, 6),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2026, 7, 6, 18),
            scheduled_ends_at=self.make_dt(2026, 7, 6, 20),
        )
        self.add_match_participants(match, team_a_players, team_b_players)
        submit_match_result(team_a_players[0].user, match, [(6, 4), (6, 4)])
        submit_match_result(team_b_players[0].user, match, [(6, 4), (6, 4)])

        reconcile_ladder_standings(Team.DIVISION_MENS)
        team_a.standing.refresh_from_db()
        team_b.standing.refresh_from_db()

        self.assertEqual(
            (team_a.standing.matches_played, team_a.standing.wins, team_a.standing.losses, team_a.standing.points), (1, 1, 0, 3)
        )
        self.assertEqual(
            (team_b.standing.matches_played, team_b.standing.wins, team_b.standing.losses, team_b.standing.points), (1, 0, 1, 0)
        )
        self.assertEqual(team_a.standing.position, 1)


class PhaseARequestTests(TestCase):
    club_tz = ZoneInfo("America/New_York")

    def create_profile(self, username, gender=PlayerProfile.GENDER_MALE):
        user = get_user_model().objects.create_user(username=username, password="pass")
        return PlayerProfile.objects.create(user=user, gender=gender)

    def make_dt(self, year, month, day, hour, minute=0):
        return datetime(year, month, day, hour, minute, tzinfo=self.club_tz)

    def create_team_with_members(self, name, count):
        team = Team.objects.create(name=name, division=Team.DIVISION_MENS)
        players = []
        for index in range(1, count + 1):
            profile = self.create_profile(f"{name}-{index}")
            request_membership_change(profile.user, profile, team, "join")
            players.append(profile)
        return team, players

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

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("ladder:dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("ladder:login"), response["Location"])

    def test_login_redirects_to_dashboard(self):
        profile = self.create_profile("login-redirect")

        response = self.client.post(
            reverse("ladder:login"),
            {"username": profile.user.username, "password": "pass"},
        )

        self.assertRedirects(response, reverse("ladder:dashboard"))

    def test_self_registration_creates_user_profile_and_logs_in(self):
        response = self.client.post(
            reverse("ladder:register"),
            {
                "username": "new-register",
                "gender": PlayerProfile.GENDER_FEMALE,
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        user = get_user_model().objects.get(username="new-register")
        self.assertRedirects(response, reverse("ladder:dashboard"))
        self.assertTrue(PlayerProfile.objects.filter(user=user, gender=PlayerProfile.GENDER_FEMALE).exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.id)

    def test_dashboard_redirects_user_without_profile_to_setup(self):
        user = get_user_model().objects.create_user(username="needs-profile", password="pass")
        self.client.force_login(user)

        response = self.client.get(reverse("ladder:dashboard"))

        self.assertRedirects(response, reverse("ladder:profile_setup"))

    def test_profile_setup_creates_profile_for_logged_in_user(self):
        user = get_user_model().objects.create_user(username="new-player", password="pass")
        self.client.force_login(user)

        response = self.client.post(
            reverse("ladder:profile_setup"),
            {"gender": PlayerProfile.GENDER_MALE},
        )

        self.assertRedirects(response, reverse("ladder:dashboard"))
        self.assertTrue(PlayerProfile.objects.filter(user=user, gender=PlayerProfile.GENDER_MALE).exists())

    def test_authenticated_dashboard_renders_player_state(self):
        team, players = self.create_team_with_members("dashboard", 1)
        self.client.force_login(players[0].user)

        response = self.client.get(reverse("ladder:dashboard"))

        self.assertContains(response, "Dashboard")
        self.assertContains(response, "Manual setup")
        self.assertContains(response, "Start here")
        self.assertContains(response, team.name)

    def test_shared_shell_supports_skip_navigation_and_marks_current_page(self):
        _team, players = self.create_team_with_members("shell-navigation", 1)
        self.client.force_login(players[0].user)

        dashboard_response = self.client.get(reverse("ladder:dashboard"))
        team_response = self.client.get(reverse("ladder:team"))

        self.assertContains(dashboard_response, 'class="skip-link" href="#main-content"')
        self.assertContains(dashboard_response, 'id="main-content" tabindex="-1"')
        self.assertContains(dashboard_response, "<summary>Menu</summary>", html=True)
        self.assertContains(
            dashboard_response,
            f'href="{reverse("ladder:dashboard")}" aria-current="page"',
        )
        self.assertNotContains(
            dashboard_response,
            f'href="{reverse("ladder:team")}" aria-current="page"',
        )
        self.assertContains(
            team_response,
            f'href="{reverse("ladder:team")}" aria-current="page"',
        )

    def test_dashboard_setup_checklist_shows_pending_join_request(self):
        profile = self.create_profile("dashboard-pending")
        team = Team.objects.create(name="Dashboard Pending Team", division=Team.DIVISION_MENS)
        request_membership_change(profile.user, profile, team, "request_join")
        self.client.force_login(profile.user)

        response = self.client.get(reverse("ladder:dashboard"))

        self.assertContains(response, "Team request pending")
        self.assertContains(response, "Dashboard Pending Team")
        self.assertContains(response, "Add at least one active window.")

    def test_team_join_is_post_only_scoped_to_player_division_and_pending(self):
        profile = self.create_profile("joiner")
        mens_team = Team.objects.create(name="Join Team", division=Team.DIVISION_MENS)
        self.client.force_login(profile.user)

        get_response = self.client.get(reverse("ladder:join_team"))
        post_response = self.client.post(reverse("ladder:join_team"), {"team": mens_team.id})

        profile.refresh_from_db()
        self.assertEqual(get_response.status_code, 302)
        self.assertEqual(post_response.status_code, 302)
        self.assertIsNone(profile.team)
        self.assertTrue(
            TeamMembership.objects.filter(
                player=profile,
                team=mens_team,
                status=TeamMembership.STATUS_JOIN_REQUESTED,
            ).exists()
        )

    def test_team_page_shows_pending_join_request_and_blocks_second_request(self):
        profile = self.create_profile("pending-view")
        requested_team = Team.objects.create(name="Requested Team", division=Team.DIVISION_MENS)
        other_team = Team.objects.create(name="Other Requested Team", division=Team.DIVISION_MENS)
        request_membership_change(profile.user, profile, requested_team, "request_join")
        self.client.force_login(profile.user)

        page_response = self.client.get(reverse("ladder:team"))
        post_response = self.client.post(reverse("ladder:join_team"), {"team": other_team.id})

        self.assertContains(page_response, "Requested Team")
        self.assertContains(page_response, "pending admin review")
        self.assertEqual(post_response.status_code, 302)
        self.assertFalse(TeamMembership.objects.filter(player=profile, team=other_team).exists())

    def test_pending_join_request_can_be_cancelled_by_player_post_only(self):
        profile = self.create_profile("pending-cancel")
        requested_team = Team.objects.create(name="Cancel Requested Team", division=Team.DIVISION_MENS)
        request = request_membership_change(profile.user, profile, requested_team, "request_join")
        self.client.force_login(profile.user)

        get_response = self.client.get(reverse("ladder:cancel_join_request"))
        post_response = self.client.post(reverse("ladder:cancel_join_request"))

        request.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(get_response.status_code, 302)
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(request.status, TeamMembership.STATUS_INACTIVE)
        self.assertEqual(request.resolution_note, "Cancelled by player.")
        self.assertIsNone(profile.team)

    def test_team_page_shows_capacity_and_full_state(self):
        team, players = self.create_team_with_members("capacity-page", 3)
        self.client.force_login(players[0].user)

        response = self.client.get(reverse("ladder:team"))

        self.assertContains(response, "3/3 members")
        self.assertContains(response, "This team is full")

    def test_team_join_rejects_wrong_division_and_existing_active_team(self):
        womens_profile = self.create_profile("wrong-division", gender=PlayerProfile.GENDER_FEMALE)
        mens_team = Team.objects.create(name="Mens Only", division=Team.DIVISION_MENS)
        self.client.force_login(womens_profile.user)

        wrong_division_response = self.client.post(reverse("ladder:join_team"), {"team": mens_team.id})

        active_profile = self.create_profile("already-active")
        current_team = Team.objects.create(name="Current Team", division=Team.DIVISION_MENS)
        other_team = Team.objects.create(name="Another Team", division=Team.DIVISION_MENS)
        request_membership_change(active_profile.user, active_profile, current_team, "join")
        self.client.force_login(active_profile.user)
        active_response = self.client.post(reverse("ladder:join_team"), {"team": other_team.id})

        self.assertEqual(wrong_division_response.status_code, 302)
        self.assertFalse(TeamMembership.objects.filter(player=womens_profile, team=mens_team).exists())
        self.assertEqual(active_response.status_code, 302)
        self.assertFalse(TeamMembership.objects.filter(player=active_profile, team=other_team).exists())

    def test_team_create_is_post_only_and_adds_creator_as_first_member(self):
        profile = self.create_profile("team-creator")
        self.client.force_login(profile.user)

        get_response = self.client.get(reverse("ladder:create_team"))
        post_response = self.client.post(reverse("ladder:create_team"), {"name": "  Creator Club  "})

        profile.refresh_from_db()
        team = Team.objects.get(name="Creator Club")
        self.assertEqual(get_response.status_code, 302)
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(team.division, Team.DIVISION_MENS)
        self.assertEqual(profile.team, team)
        self.assertTrue(TeamMembership.objects.filter(player=profile, team=team, status=TeamMembership.STATUS_ACTIVE).exists())
        self.assertTrue(LadderStanding.objects.filter(team=team).exists())

    def test_womens_player_created_team_uses_womens_division(self):
        profile = self.create_profile("womens-team-creator", gender=PlayerProfile.GENDER_FEMALE)
        self.client.force_login(profile.user)

        response = self.client.post(reverse("ladder:create_team"), {"name": "Womens Creator Club"})

        team = Team.objects.get(name="Womens Creator Club")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(team.division, Team.DIVISION_WOMENS)

    def test_team_create_requires_profile_and_no_active_team(self):
        user = get_user_model().objects.create_user(username="create-needs-profile", password="pass")
        self.client.force_login(user)

        no_profile_response = self.client.post(reverse("ladder:create_team"), {"name": "No Profile Team"})

        profile = self.create_profile("already-on-team")
        team = Team.objects.create(name="Already Team", division=Team.DIVISION_MENS)
        request_membership_change(profile.user, profile, team, "join")
        self.client.force_login(profile.user)
        existing_team_response = self.client.post(reverse("ladder:create_team"), {"name": "Should Not Exist"})

        self.assertEqual(no_profile_response.status_code, 302)
        self.assertIn(reverse("ladder:profile_setup"), no_profile_response["Location"])
        self.assertEqual(existing_team_response.status_code, 302)
        self.assertFalse(Team.objects.filter(name="No Profile Team").exists())
        self.assertFalse(Team.objects.filter(name="Should Not Exist").exists())

    def test_team_create_rejects_duplicate_or_blank_name_cleanly(self):
        profile = self.create_profile("duplicate-request-player")
        Team.objects.create(name="Existing Team", division=Team.DIVISION_MENS)
        self.client.force_login(profile.user)

        duplicate_response = self.client.post(reverse("ladder:create_team"), {"name": "existing team"})
        blank_response = self.client.post(reverse("ladder:create_team"), {"name": "   "})

        self.assertEqual(duplicate_response.status_code, 302)
        self.assertEqual(blank_response.status_code, 302)
        self.assertIsNone(PlayerProfile.objects.get(pk=profile.pk).team)
        self.assertEqual(Team.objects.filter(name__iexact="existing team").count(), 1)

    def test_availability_create_and_cancel_are_owner_scoped(self):
        team, players = self.create_team_with_members("availability-page", 1)
        other = self.create_profile("other")
        self.client.force_login(players[0].user)

        create_response = self.client.post(
            reverse("ladder:availability"),
            {"starts_at": "2026-07-27T18:00", "ends_at": "2026-07-27T20:00"},
        )
        slot = AvailabilitySlot.objects.get(player=players[0])
        cancel_response = self.client.post(reverse("ladder:cancel_availability", args=[slot.id]))

        save_availability(other.user, self.make_dt(2026, 7, 28, 18), self.make_dt(2026, 7, 28, 20))
        other_slot = AvailabilitySlot.objects.get(player=other)
        blocked_response = self.client.post(reverse("ladder:cancel_availability", args=[other_slot.id]))
        other_slot.refresh_from_db()

        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(cancel_response.status_code, 302)
        slot.refresh_from_db()
        self.assertEqual(slot.status, AvailabilitySlot.STATUS_CANCELLED)
        self.assertEqual(blocked_response.status_code, 302)
        self.assertEqual(other_slot.status, AvailabilitySlot.STATUS_ACTIVE)

    def test_cancelled_availability_is_hidden_from_player_window_list(self):
        _team, players = self.create_team_with_members("availability-hidden", 1)
        self.client.force_login(players[0].user)
        cancelled_slot = save_availability(players[0].user, self.make_dt(2026, 7, 29, 18), self.make_dt(2026, 7, 29, 20))
        active_slot = save_availability(players[0].user, self.make_dt(2026, 7, 30, 18), self.make_dt(2026, 7, 30, 20))
        cancel_availability(players[0].user, cancelled_slot)

        response = self.client.get(reverse("ladder:availability"))

        self.assertContains(response, "Active Windows")
        self.assertContains(response, "1 cancelled window")
        self.assertContains(response, active_slot.starts_at.strftime("%Y"))
        self.assertNotContains(response, "Jul 29, 2026")

    def test_suggestion_create_and_dual_acceptance_flow(self):
        team_a, team_a_players = self.create_team_with_members("phase-a", 2)
        team_b, team_b_players = self.create_team_with_members("phase-b", 2)
        starts_at = timezone.now() + timedelta(days=7)
        starts_at = starts_at.replace(hour=18, minute=0, second=0, microsecond=0)
        ends_at = starts_at + timedelta(hours=2)
        for player in team_a_players + team_b_players:
            save_availability(player.user, starts_at, ends_at)

        self.client.force_login(team_a_players[0].user)
        create_response = self.client.post(reverse("ladder:create_suggestion", args=[0]))
        suggestion = MatchSuggestion.objects.get()
        first_accept = self.client.post(
            reverse("ladder:accept_suggestion", args=[suggestion.id]),
            {"version": suggestion.version},
        )
        self.client.force_login(team_b_players[0].user)
        second_accept = self.client.post(
            reverse("ladder:accept_suggestion", args=[suggestion.id]),
            {"version": suggestion.version},
        )

        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(first_accept.status_code, 302)
        self.assertEqual(second_accept.status_code, 302)
        self.assertEqual(Match.objects.count(), 1)
        self.assertEqual(MatchReservation.objects.count(), 4)

    def test_suggestions_explain_missing_team_and_missing_members(self):
        no_team_profile = self.create_profile("suggestions-no-team")
        self.client.force_login(no_team_profile.user)

        no_team_response = self.client.get(reverse("ladder:suggestions"))

        one_member_team, one_member_players = self.create_team_with_members("suggestions-one-member", 1)
        self.client.force_login(one_member_players[0].user)
        one_member_response = self.client.get(reverse("ladder:suggestions"))

        self.assertContains(no_team_response, "Join or create a team first")
        self.assertContains(no_team_response, "Suggestions require an active team")
        self.assertContains(one_member_response, "Your team needs two active members")
        self.assertContains(one_member_response, one_member_team.name)

    def test_suggestions_explain_missing_availability_and_missing_shared_lineup(self):
        _team_without_availability, no_availability_players = self.create_team_with_members("suggestions-no-availability", 2)
        self.client.force_login(no_availability_players[0].user)

        no_availability_response = self.client.get(reverse("ladder:suggestions"))

        no_shared_team, no_shared_players = self.create_team_with_members("suggestions-no-shared", 2)
        first_starts_at = timezone.now() + timedelta(days=7)
        first_starts_at = first_starts_at.replace(hour=18, minute=0, second=0, microsecond=0)
        second_starts_at = first_starts_at + timedelta(days=1)
        save_availability(no_shared_players[0].user, first_starts_at, first_starts_at + timedelta(hours=2))
        save_availability(no_shared_players[1].user, second_starts_at, second_starts_at + timedelta(hours=2))
        self.client.force_login(no_shared_players[0].user)
        no_shared_response = self.client.get(reverse("ladder:suggestions"))

        self.assertContains(no_availability_response, "Add active availability")
        self.assertContains(no_availability_response, "At least two teammates need overlapping")
        self.assertContains(no_shared_response, "No shared team availability")
        self.assertContains(no_shared_response, no_shared_team.name)

    def test_suggestions_explain_missing_opponents_and_render_valid_option_details(self):
        solo_team, solo_players = self.create_team_with_members("suggestions-solo", 2)
        starts_at = timezone.now() + timedelta(days=7)
        starts_at = starts_at.replace(hour=18, minute=0, second=0, microsecond=0)
        ends_at = starts_at + timedelta(hours=2)
        for player in solo_players:
            save_availability(player.user, starts_at, ends_at)
        self.client.force_login(solo_players[0].user)

        no_opponents_response = self.client.get(reverse("ladder:suggestions"))

        opponent_team, opponent_players = self.create_team_with_members("suggestions-opponent", 2)
        for player in opponent_players:
            save_availability(player.user, starts_at, ends_at)
        valid_response = self.client.get(reverse("ladder:suggestions"))

        self.assertContains(no_opponents_response, "No opponent teams in this ladder yet")
        self.assertContains(valid_response, solo_team.name)
        self.assertContains(valid_response, opponent_team.name)
        self.assertContains(valid_response, "Your lineup")
        self.assertContains(valid_response, "Opponent lineup")
        self.assertContains(valid_response, "Create suggestion")

    def test_current_suggestion_accept_button_only_shows_for_selected_lineup_players(self):
        team_a, team_a_players = self.create_team_with_members("suggestions-selected-a", 3)
        _team_b, team_b_players = self.create_team_with_members("suggestions-selected-b", 2)
        starts_at = self.make_dt(2026, 8, 20, 18)
        ends_at = self.make_dt(2026, 8, 20, 20)
        for player in team_a_players[:2] + team_b_players:
            save_availability(player.user, starts_at, ends_at)
        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option)

        self.client.force_login(team_a_players[2].user)
        third_member_response = self.client.get(reverse("ladder:suggestions"))
        self.client.force_login(team_a_players[0].user)
        selected_player_response = self.client.get(reverse("ladder:suggestions"))

        self.assertContains(third_member_response, "Only the selected lineup players can accept this suggestion.")
        self.assertNotContains(third_member_response, f'action="{reverse("ladder:accept_suggestion", args=[suggestion.id])}"')
        self.assertContains(selected_player_response, f'action="{reverse("ladder:accept_suggestion", args=[suggestion.id])}"')

    def test_match_detail_blocks_unrelated_player_and_score_submission_updates_result(self):
        team_a, team_a_players = self.create_team_with_members("score-page-a", 2)
        team_b, team_b_players = self.create_team_with_members("score-page-b", 2)
        _, outside_players = self.create_team_with_members("score-page-out", 1)
        LadderStanding.objects.create(team=team_a, position=1)
        LadderStanding.objects.create(team=team_b, position=2)
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 7, 27),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2026, 7, 27, 18),
            scheduled_ends_at=self.make_dt(2026, 7, 27, 20),
        )
        self.add_match_participants(match, team_a_players, team_b_players)

        self.client.force_login(outside_players[0].user)
        denied = self.client.get(reverse("ladder:match_detail", args=[match.id]))
        self.client.force_login(team_a_players[0].user)
        first = self.client.post(
            reverse("ladder:submit_score", args=[match.id]),
            {"set1_team_a": 6, "set1_team_b": 4, "set2_team_a": 6, "set2_team_b": 4},
        )
        self.client.force_login(team_b_players[0].user)
        second = self.client.post(
            reverse("ladder:submit_score", args=[match.id]),
            {"set1_team_a": 6, "set1_team_b": 4, "set2_team_a": 6, "set2_team_b": 4},
        )

        match.refresh_from_db()
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(match.status, Match.STATUS_COMPLETED)
        self.assertEqual(PointLedger.objects.filter(match=match).count(), 2)

    def test_selected_participant_confirms_and_performs_match_cancellation(self):
        team_a, team_a_players = self.create_team_with_members("cancel-request-a", 3)
        team_b, team_b_players = self.create_team_with_members("cancel-request-b", 2)
        outsider = self.create_profile("cancel-request-outsider")
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2027, 9, 6),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2027, 9, 6, 18),
            scheduled_ends_at=self.make_dt(2027, 9, 6, 20),
        )
        self.add_match_participants(match, team_a_players[:2], team_b_players)
        cancel_url = reverse("ladder:cancel_match", args=[match.id])

        self.client.force_login(team_a_players[2].user)
        teammate_detail = self.client.get(reverse("ladder:match_detail", args=[match.id]))
        teammate_cancel = self.client.get(cancel_url)
        self.assertEqual(teammate_detail.status_code, 200)
        self.assertNotContains(teammate_detail, cancel_url)
        self.assertNotContains(teammate_detail, "Submit Score")
        self.assertEqual(teammate_cancel.status_code, 404)

        self.client.force_login(outsider.user)
        self.assertEqual(self.client.get(reverse("ladder:match_detail", args=[match.id])).status_code, 404)

        selected_player = team_a_players[0]
        membership = TeamMembership.objects.get(player=selected_player, status=TeamMembership.STATUS_ACTIVE)
        membership.status = TeamMembership.STATUS_INACTIVE
        membership.effective_to = timezone.now()
        membership.save(update_fields=["status", "effective_to", "updated_at"])
        selected_player.team = None
        selected_player.save(update_fields=["team"])

        self.client.force_login(selected_player.user)
        historical_detail = self.client.get(reverse("ladder:match_detail", args=[match.id]))
        confirmation = self.client.get(cancel_url)
        match.refresh_from_db()
        self.assertEqual(historical_detail.status_code, 200)
        self.assertContains(historical_detail, cancel_url)
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "The cancellation cannot be undone")
        self.assertEqual(match.status, Match.STATUS_SCHEDULED)

        response = self.client.post(cancel_url)

        match.refresh_from_db()
        self.assertRedirects(response, reverse("ladder:match_detail", args=[match.id]))
        self.assertEqual(match.status, Match.STATUS_CANCELLED)
        self.assertEqual(WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.MATCH_CANCELLED).actor, selected_player.user)

    def test_ladder_page_is_authenticated_and_scoped_by_division(self):
        mens_team = Team.objects.create(name="Mens Ladder Team", division=Team.DIVISION_MENS)
        womens_team = Team.objects.create(name="Womens Ladder Team", division=Team.DIVISION_WOMENS)
        LadderStanding.objects.create(team=mens_team, position=1, points=3)
        LadderStanding.objects.create(team=womens_team, position=1, points=9)
        profile = self.create_profile("ladder-viewer")
        self.client.force_login(profile.user)

        response = self.client.get(reverse("ladder:ladder", args=[Team.DIVISION_MENS]))

        self.assertContains(response, mens_team.name)
        self.assertNotContains(response, womens_team.name)


class PhaseA5OperationsTests(TestCase):
    club_tz = ZoneInfo("America/New_York")

    def create_profile(self, username, gender=PlayerProfile.GENDER_MALE, is_staff=False):
        user = get_user_model().objects.create_user(username=username, password="pass")
        user.is_staff = is_staff
        user.save(update_fields=["is_staff"])
        return PlayerProfile.objects.create(user=user, gender=gender)

    def create_team_with_members(self, name, count):
        team = Team.objects.create(name=name, division=Team.DIVISION_MENS)
        players = []
        for index in range(1, count + 1):
            profile = self.create_profile(f"{name}-{index}")
            request_membership_change(profile.user, profile, team, "join")
            players.append(profile)
        return team, players

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

    def make_dt(self, year, month, day, hour, minute=0):
        return datetime(year, month, day, hour, minute, tzinfo=self.club_tz)

    def test_admin_can_reject_and_approve_removal_requests(self):
        admin_profile = self.create_profile("ops-admin", is_staff=True)
        team, players = self.create_team_with_members("ops-remove", 2)
        first_request = request_membership_change(players[0].user, players[0], action="request_removal")

        rejected = resolve_membership_request(admin_profile.user, first_request, "reject")
        rejected.refresh_from_db()

        self.assertEqual(rejected.status, TeamMembership.STATUS_ACTIVE)
        self.assertIsNone(rejected.removal_requested_at)

        second_request = request_membership_change(players[0].user, players[0], action="request_removal")
        approved = resolve_membership_request(admin_profile.user, second_request, "approve")
        players[0].refresh_from_db()

        self.assertEqual(approved.status, TeamMembership.STATUS_INACTIVE)
        self.assertIsNone(players[0].team)
        self.assertTrue(TeamMembership.objects.filter(team=team, player=players[0]).exists())

    def test_membership_workflow_events_are_deduplicated_and_snapshot_recipients(self):
        admin = self.create_profile("event-admin", is_staff=True)
        other_admin = self.create_profile("event-other-admin", is_staff=True)
        inactive_admin = self.create_profile("event-inactive-admin", is_staff=True)
        inactive_admin.user.is_active = False
        inactive_admin.user.save(update_fields=["is_active"])
        team = Team.objects.create(name="event-membership-team", division=Team.DIVISION_MENS)

        cancelled_player = self.create_profile("event-cancelled-player")
        cancelled_request = request_membership_change(cancelled_player.user, cancelled_player, team, "request_join")
        repeated_request = request_membership_change(cancelled_player.user, cancelled_player, team, "request_join")

        self.assertEqual(repeated_request.pk, cancelled_request.pk)
        created_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.JOIN_REQUEST_CREATED)
        self.assertEqual(created_event.actor, cancelled_player.user)
        self.assertEqual(created_event.membership, cancelled_request)
        self.assertEqual(
            set(created_event.recipients.values_list("user_id", flat=True)),
            {cancelled_player.user_id, admin.user_id, other_admin.user_id},
        )

        cancelled_membership = cancel_join_request(cancelled_player.user, cancelled_player)
        repeated_cancel = cancel_join_request(cancelled_player.user, cancelled_player)
        self.assertEqual(repeated_cancel.pk, cancelled_membership.pk)
        cancelled_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.JOIN_REQUEST_CANCELLED)
        self.assertEqual(cancelled_event.previous_state, TeamMembership.STATUS_JOIN_REQUESTED)
        self.assertEqual(cancelled_event.new_state, TeamMembership.STATUS_INACTIVE)
        self.assertEqual(
            set(cancelled_event.recipients.values_list("user_id", flat=True)),
            {cancelled_player.user_id, admin.user_id, other_admin.user_id},
        )

        approved_player = self.create_profile("event-approved-player")
        approved_request = request_membership_change(approved_player.user, approved_player, team, "request_join")
        resolve_membership_request(admin.user, approved_request, "approve")
        resolve_membership_request(admin.user, approved_request, "approve")
        approved_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.JOIN_REQUEST_APPROVED)
        self.assertEqual(
            set(approved_event.recipients.values_list("user_id", flat=True)),
            {approved_player.user_id, admin.user_id},
        )

        rejected_player = self.create_profile("event-rejected-player")
        rejected_request = request_membership_change(rejected_player.user, rejected_player, team, "request_join")
        resolve_membership_request(admin.user, rejected_request, "reject")
        resolve_membership_request(admin.user, rejected_request, "reject")
        rejected_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.JOIN_REQUEST_REJECTED)
        self.assertEqual(
            set(rejected_event.recipients.values_list("user_id", flat=True)),
            {rejected_player.user_id, admin.user_id},
        )
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.JOIN_REQUEST_CREATED).count(), 3)
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.JOIN_REQUEST_CANCELLED).count(), 1)
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.JOIN_REQUEST_APPROVED).count(), 1)
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.JOIN_REQUEST_REJECTED).count(), 1)

    def test_match_and_score_workflow_events_snapshot_exact_recipients(self):
        admin = self.create_profile("score-event-admin", is_staff=True)
        other_admin = self.create_profile("score-event-other-admin", is_staff=True)
        team_a, team_a_players = self.create_team_with_members("score-event-a", 2)
        team_b, team_b_players = self.create_team_with_members("score-event-b", 2)
        starts_at = self.make_dt(2027, 8, 3, 18)
        ends_at = self.make_dt(2027, 8, 3, 20)
        for player in team_a_players + team_b_players:
            save_availability(player.user, starts_at, ends_at)
        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option, expires_at=self.make_dt(2027, 8, 10, 18))

        accept_suggestion(team_a_players[0].user, suggestion, suggestion.version)
        match = accept_suggestion(team_b_players[0].user, suggestion, suggestion.version)
        repeated_match = accept_suggestion(team_b_players[0].user, suggestion, suggestion.version)
        participant_user_ids = {player.user_id for player in team_a_players + team_b_players}

        self.assertEqual(repeated_match.pk, match.pk)
        confirmed_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.MATCH_CONFIRMED)
        self.assertEqual(confirmed_event.actor, team_b_players[0].user)
        self.assertEqual(set(confirmed_event.recipients.values_list("user_id", flat=True)), participant_user_ids)
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.MATCH_CONFIRMED).count(), 1)

        first_submission = submit_match_result(team_a_players[0].user, match, [(6, 4), (6, 4)])
        repeated_submission = submit_match_result(team_a_players[0].user, match, [(6, 4), (6, 4)])

        self.assertEqual(repeated_submission.pk, first_submission.pk)
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.SCORE_SUBMITTED).count(), 1)

        second_submission = submit_match_result(team_b_players[0].user, match, [(4, 6), (4, 6)])
        score_events = WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.SCORE_SUBMITTED).order_by("submission_id")
        self.assertEqual(score_events.count(), 2)
        self.assertEqual({event.submission_id for event in score_events}, {first_submission.id, second_submission.id})
        for event in score_events:
            self.assertEqual(set(event.recipients.values_list("user_id", flat=True)), participant_user_ids)

        notification = AdminNotification.objects.get(match=match, is_resolved=False)
        conflict_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.SCORE_CONFLICT_CREATED)
        self.assertEqual(conflict_event.actor, team_b_players[0].user)
        self.assertEqual(
            set(conflict_event.recipients.values_list("user_id", flat=True)),
            participant_user_ids | {admin.user_id, other_admin.user_id},
        )

        audit = resolve_score_conflict(admin.user, notification, first_submission)
        repeated_audit = resolve_score_conflict(admin.user, notification, first_submission)
        self.assertEqual(repeated_audit.pk, audit.pk)
        resolved_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.SCORE_CONFLICT_RESOLVED)
        self.assertEqual(resolved_event.score_correction_audit, audit)
        self.assertEqual(
            set(resolved_event.recipients.values_list("user_id", flat=True)),
            participant_user_ids | {admin.user_id},
        )
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.SCORE_CONFLICT_RESOLVED).count(), 1)

    def test_cancel_match_releases_reservations_and_restores_availability(self):
        admin_profile = self.create_profile("cancel-admin", is_staff=True)
        team_a, team_a_players = self.create_team_with_members("cancel-a", 2)
        team_b, team_b_players = self.create_team_with_members("cancel-b", 2)
        starts_at = self.make_dt(2026, 8, 3, 18)
        ends_at = self.make_dt(2026, 8, 3, 20)
        for player in team_a_players + team_b_players:
            save_availability(player.user, starts_at, ends_at)
        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option, expires_at=self.make_dt(2027, 8, 10, 18))
        accept_suggestion(team_a_players[0].user, suggestion, suggestion.version)
        match = accept_suggestion(team_b_players[0].user, suggestion, suggestion.version)

        cancelled = cancel_match(admin_profile.user, match)

        self.assertEqual(cancelled.status, Match.STATUS_CANCELLED)
        self.assertEqual(MatchReservation.objects.filter(match=match, status=MatchReservation.STATUS_RELEASED).count(), 4)
        self.assertEqual(AvailabilitySlot.objects.filter(status=AvailabilitySlot.STATUS_ACTIVE).count(), 4)
        cancelled_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.MATCH_CANCELLED)
        self.assertEqual(cancelled_event.actor, admin_profile.user)
        self.assertEqual(
            set(cancelled_event.recipients.values_list("user_id", flat=True)),
            {player.user_id for player in team_a_players + team_b_players},
        )
        cancel_match(admin_profile.user, match)
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.MATCH_CANCELLED).count(), 1)

    def test_selected_participant_can_cancel_but_other_players_cannot(self):
        team_a, team_a_players = self.create_team_with_members("participant-cancel-a", 3)
        team_b, team_b_players = self.create_team_with_members("participant-cancel-b", 2)
        outsider = self.create_profile("participant-cancel-outsider")
        starts_at = self.make_dt(2027, 8, 17, 18)
        ends_at = self.make_dt(2027, 8, 17, 20)
        for player in team_a_players + team_b_players:
            save_availability(player.user, starts_at, ends_at)
        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option, expires_at=self.make_dt(2027, 8, 24, 18))
        selected_player_ids = {player.id for player in option["team_a_players"] + option["team_b_players"]}
        selected_player = next(player for player in team_a_players if player.id in selected_player_ids)
        non_lineup_teammate = next(player for player in team_a_players if player.id not in selected_player_ids)
        accept_suggestion(option["team_a_players"][0].user, suggestion, suggestion.version)
        match = accept_suggestion(option["team_b_players"][0].user, suggestion, suggestion.version)

        with self.assertRaises(AuthorizationFailure):
            cancel_match(non_lineup_teammate.user, match)
        with self.assertRaises(AuthorizationFailure):
            cancel_match(outsider.user, match)

        cancelled = cancel_match(selected_player.user, match)

        self.assertEqual(cancelled.status, Match.STATUS_CANCELLED)
        self.assertEqual(WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.MATCH_CANCELLED).actor, selected_player.user)

    def test_match_cancellation_is_blocked_after_first_score_submission_for_everyone(self):
        admin = self.create_profile("score-cutoff-admin", is_staff=True)
        team_a, team_a_players = self.create_team_with_members("score-cutoff-a", 2)
        team_b, team_b_players = self.create_team_with_members("score-cutoff-b", 2)
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2027, 8, 23),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2027, 8, 23, 18),
            scheduled_ends_at=self.make_dt(2027, 8, 23, 20),
        )
        self.add_match_participants(match, team_a_players, team_b_players)
        submit_match_result(team_a_players[0].user, match, [(6, 4), (6, 4)])

        with self.assertRaises(StaleState):
            cancel_match(team_a_players[0].user, match)
        with self.assertRaises(StaleState):
            cancel_match(admin.user, match)

        match.refresh_from_db()
        self.assertEqual(match.status, Match.STATUS_SCHEDULED)
        self.assertFalse(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.MATCH_CANCELLED).exists())

    def test_cancellation_preserves_overlapping_replacement_availability(self):
        team_a, team_a_players = self.create_team_with_members("overlap-cancel-a", 2)
        team_b, team_b_players = self.create_team_with_members("overlap-cancel-b", 2)
        starts_at = self.make_dt(2027, 8, 30, 18)
        ends_at = self.make_dt(2027, 8, 30, 20)
        original_slots = {player.id: save_availability(player.user, starts_at, ends_at) for player in team_a_players + team_b_players}
        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option, expires_at=self.make_dt(2027, 9, 6, 18))
        accept_suggestion(team_a_players[0].user, suggestion, suggestion.version)
        match = accept_suggestion(team_b_players[0].user, suggestion, suggestion.version)
        replacement_player = team_a_players[0]
        replacement = save_availability(
            replacement_player.user,
            self.make_dt(2027, 8, 30, 18, 30),
            self.make_dt(2027, 8, 30, 20, 30),
        )

        cancel_match(replacement_player.user, match)

        for slot in original_slots.values():
            slot.refresh_from_db()
        replacement.refresh_from_db()
        suggestion.refresh_from_db()
        event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.MATCH_CANCELLED)
        self.assertEqual(original_slots[replacement_player.id].status, AvailabilitySlot.STATUS_CANCELLED)
        self.assertEqual(replacement.status, AvailabilitySlot.STATUS_ACTIVE)
        self.assertEqual(
            event.metadata["superseded_availability_ids"],
            [original_slots[replacement_player.id].id],
        )
        self.assertEqual(suggestion.status, MatchSuggestion.STATUS_CONFIRMED)

    def test_completed_match_cannot_be_cancelled(self):
        admin_profile = self.create_profile("completed-admin", is_staff=True)
        team_a, players_a = self.create_team_with_members("completed-a", 2)
        team_b, players_b = self.create_team_with_members("completed-b", 2)
        LadderStanding.objects.create(team=team_a, position=1)
        LadderStanding.objects.create(team=team_b, position=2)
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 8, 3),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2026, 8, 3, 18),
            scheduled_ends_at=self.make_dt(2026, 8, 3, 20),
        )
        self.add_match_participants(match, players_a, players_b)
        submit_match_result(players_a[0].user, match, [(6, 4), (6, 4)])
        submit_match_result(players_b[0].user, match, [(6, 4), (6, 4)])

        with self.assertRaises(StaleState):
            cancel_match(admin_profile.user, match)

    def test_admin_can_resolve_score_conflict_notification(self):
        admin_profile = self.create_profile("conflict-admin", is_staff=True)
        team_a, players_a = self.create_team_with_members("conflict-a", 2)
        team_b, players_b = self.create_team_with_members("conflict-b", 2)
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 8, 3),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2026, 8, 3, 18),
            scheduled_ends_at=self.make_dt(2026, 8, 3, 20),
        )
        self.add_match_participants(match, players_a, players_b)
        submit_match_result(players_a[0].user, match, [(6, 4), (6, 4)])
        submit_match_result(players_b[0].user, match, [(4, 6), (4, 6)])
        notification = AdminNotification.objects.get(match=match)
        official_submission = MatchResultSubmission.objects.get(match=match, submitting_team=team_a)

        audit = resolve_score_conflict(admin_profile.user, notification, official_submission)
        notification.refresh_from_db()
        match.refresh_from_db()

        self.assertTrue(notification.is_resolved)
        self.assertEqual(audit.official_submission, official_submission)
        self.assertEqual(audit.new_winning_team, team_a)
        self.assertEqual(match.status, Match.STATUS_COMPLETED)
        self.assertEqual(ScoreCorrectionAudit.objects.count(), 1)
        self.assertEqual(PointLedger.objects.filter(match=match).count(), 2)

    def test_seed_demo_is_idempotent_and_populates_core_flows(self):
        first_output = StringIO()
        second_output = StringIO()

        call_command("seed_demo", stdout=first_output)
        counts_after_first = {
            "users": get_user_model().objects.count(),
            "profiles": PlayerProfile.objects.count(),
            "teams": Team.objects.count(),
            "memberships": TeamMembership.objects.count(),
            "standings": LadderStanding.objects.count(),
            "matches": Match.objects.count(),
        }
        call_command("seed_demo", stdout=second_output)

        self.assertEqual(counts_after_first["users"], get_user_model().objects.count())
        self.assertEqual(counts_after_first["profiles"], PlayerProfile.objects.count())
        self.assertEqual(counts_after_first["teams"], Team.objects.count())
        self.assertEqual(counts_after_first["memberships"], TeamMembership.objects.count())
        self.assertEqual(counts_after_first["standings"], LadderStanding.objects.count())
        self.assertEqual(counts_after_first["matches"], Match.objects.count())
        self.assertGreaterEqual(Match.objects.count(), 2)
        self.assertTrue(MatchSuggestion.objects.exists())
        for standing in LadderStanding.objects.select_related("team"):
            self.assertEqual(standing.matches_played, standing.wins + standing.losses)
            self.assertEqual(standing.points, standing.wins * 3)

        demo_mens_team = Team.objects.get(name="Men Demo Team 1")
        starts_at = timezone.now()
        ends_at = starts_at + timedelta(days=30)
        self.assertTrue(find_opponent_suggestions(demo_mens_team, (starts_at, ends_at)))
        self.assertIn("Demo data is ready", second_output.getvalue())


class DataIntegrityAuditCommandTests(TestCase):
    club_tz = ZoneInfo("America/New_York")

    def create_player(self, username, team):
        user = get_user_model().objects.create_user(username=username)
        return PlayerProfile.objects.create(user=user, gender=PlayerProfile.GENDER_MALE, team=team)

    def create_match(self, team_a, team_b):
        return Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 10, 5),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=datetime(2026, 10, 5, 18, tzinfo=self.club_tz),
            scheduled_ends_at=datetime(2026, 10, 5, 20, tzinfo=self.club_tz),
        )

    def test_audit_data_integrity_passes_for_empty_database(self):
        output = StringIO()

        call_command("audit_data_integrity", stdout=output)

        self.assertIn("Data integrity audit passed", output.getvalue())

    def test_audit_data_integrity_fails_for_overlapping_active_availability(self):
        if connection.vendor == "postgresql":
            self.skipTest("PostgreSQL exclusion constraints reject overlapping active availability before audit.")
        team = Team.objects.create(name="Audit Team", division=Team.DIVISION_MENS)
        player = self.create_player("audit-player", team)
        first_start = datetime(2026, 10, 5, 18, tzinfo=self.club_tz)
        first_end = datetime(2026, 10, 5, 20, tzinfo=self.club_tz)
        second_start = datetime(2026, 10, 5, 19, tzinfo=self.club_tz)
        second_end = datetime(2026, 10, 5, 21, tzinfo=self.club_tz)
        AvailabilitySlot.objects.create(
            player=player,
            week_start_date=date(2026, 10, 5),
            day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            start_time=time(18, 0),
            end_time=time(20, 0),
            starts_at=first_start,
            ends_at=first_end,
            status=AvailabilitySlot.STATUS_ACTIVE,
        )
        AvailabilitySlot.objects.create(
            player=player,
            week_start_date=date(2026, 10, 5),
            day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            start_time=time(19, 0),
            end_time=time(21, 0),
            starts_at=second_start,
            ends_at=second_end,
            status=AvailabilitySlot.STATUS_ACTIVE,
        )
        output = StringIO()

        with self.assertRaises(CommandError):
            call_command("audit_data_integrity", stdout=output)

        self.assertIn("availability.overlap_active", output.getvalue())

    def test_audit_data_integrity_fails_for_standing_mismatch_and_missing_ledger(self):
        team_a = Team.objects.create(name="Audit A", division=Team.DIVISION_MENS)
        team_b = Team.objects.create(name="Audit B", division=Team.DIVISION_MENS)
        match = self.create_match(team_a, team_b)
        submission = MatchResultSubmission.objects.create(
            match=match,
            submitting_team=team_a,
            team_a_sets_won=2,
            team_b_sets_won=0,
        )
        ConfirmedMatchResult.objects.create(
            match=match,
            winning_team=team_a,
            losing_team=team_b,
            confirmed_from_submission=submission,
        )
        LadderStanding.objects.create(team=team_a, position=1, matches_played=0, wins=0, losses=0, points=0)
        LadderStanding.objects.create(team=team_b, position=2, matches_played=0, wins=0, losses=0, points=0)
        output = StringIO()

        with self.assertRaises(CommandError):
            call_command("audit_data_integrity", stdout=output)

        audit_output = output.getvalue()
        self.assertIn("standing.stats_mismatch", audit_output)
        self.assertIn("ledger.missing_match_result", audit_output)

    def test_audit_data_integrity_fails_for_cross_linked_confirmed_result_and_extra_ledger(self):
        team_a = Team.objects.create(name="Audit Cross A", division=Team.DIVISION_MENS)
        team_b = Team.objects.create(name="Audit Cross B", division=Team.DIVISION_MENS)
        team_c = Team.objects.create(name="Audit Cross C", division=Team.DIVISION_MENS)
        team_d = Team.objects.create(name="Audit Cross D", division=Team.DIVISION_MENS)
        match = self.create_match(team_a, team_b)
        submission = MatchResultSubmission.objects.create(
            match=match,
            submitting_team=team_a,
            team_a_sets_won=2,
            team_b_sets_won=0,
        )
        ConfirmedMatchResult.objects.create(
            match=match,
            winning_team=team_c,
            losing_team=team_d,
            confirmed_from_submission=submission,
        )
        LadderStanding.objects.create(team=team_a, position=1)
        LadderStanding.objects.create(team=team_b, position=2)
        LadderStanding.objects.create(team=team_c, position=3, matches_played=1, wins=1, points=3)
        LadderStanding.objects.create(team=team_d, position=4, matches_played=1, losses=1)
        PointLedger.objects.create(match=match, team=team_c, points_delta=3, reason="match_result")
        PointLedger.objects.create(match=match, team=team_d, points_delta=0, reason="match_result")
        PointLedger.objects.create(match=match, team=team_a, points_delta=0, reason="match_result")
        output = StringIO()

        with self.assertRaises(CommandError):
            call_command("audit_data_integrity", stdout=output)

        audit_output = output.getvalue()
        self.assertIn("result.teams_mismatch", audit_output)
        self.assertIn("result.winner_mismatch", audit_output)
        self.assertIn("ledger.unexpected_match_result", audit_output)

    def test_audit_data_integrity_fails_for_submission_from_different_match(self):
        team_a = Team.objects.create(name="Audit Submission A", division=Team.DIVISION_MENS)
        team_b = Team.objects.create(name="Audit Submission B", division=Team.DIVISION_MENS)
        first_match = self.create_match(team_a, team_b)
        second_match = self.create_match(team_a, team_b)
        submission = MatchResultSubmission.objects.create(
            match=second_match,
            submitting_team=team_a,
            team_a_sets_won=2,
            team_b_sets_won=0,
        )
        ConfirmedMatchResult.objects.create(
            match=first_match,
            winning_team=team_a,
            losing_team=team_b,
            confirmed_from_submission=submission,
        )
        PointLedger.objects.create(match=first_match, team=team_a, points_delta=3, reason="match_result")
        PointLedger.objects.create(match=first_match, team=team_b, points_delta=0, reason="match_result")
        output = StringIO()

        with self.assertRaises(CommandError):
            call_command("audit_data_integrity", stdout=output)

        self.assertIn("result.submission_match_mismatch", output.getvalue())


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
