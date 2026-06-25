from datetime import date, time

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import AvailabilitySlot, Challenge, PlayerProfile, Team, Match, MatchResultSubmission, AdminNotification
from .services import (
    find_team_match_options,
    get_pair_matching_slots,
    get_player_pairs,
    get_team_pair_availability,
    get_submissions,
    submissions_match,
    get_match_status,
    create_admin_notification_for_conflict,
    complete_match_if_result_confirmed,
    get_match_winner,
)


class TeamMatchOptionTests(TestCase):
    def setUp(self):
        self.week_start_date = date(2026, 6, 1)
        self.team_a = Team.objects.create(name="Team A", division=Team.DIVISION_MENS)
        self.team_b = Team.objects.create(name="Team B", division=Team.DIVISION_MENS)

        self.team_a_players = [
            self.create_player(f"a-player-{index}", self.team_a)
            for index in range(1, 3)
        ]
        self.team_b_players = [
            self.create_player(f"b-player-{index}", self.team_b)
            for index in range(1, 3)
        ]

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
        result_pair_ids = [
            {result["players"][0].id, result["players"][1].id}
            for result in pair_availability
        ]

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
            challenger_team = self.team_a,
            opponent_team=self.team_b,
            proposed_week_start_date = self.week_start_date,
            proposed_day_of_week = AvailabilitySlot.DayOfWeek.MONDAY,
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
            team_a = self.team_a,
            team_b=self.team_b,
            scheduled_week_start_date = self.week_start_date,
            scheduled_day_of_week = AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(19, 0),
            scheduled_end_time=time(18, 0),
        )

        with self.assertRaises(ValidationError):
            match.full_clean()

    def test_match_start_time_must_be_not_equal_end_time(self):
        match = Match(
            team_a = self.team_a,
            team_b=self.team_b,
            scheduled_week_start_date = self.week_start_date,
            scheduled_day_of_week = AvailabilitySlot.DayOfWeek.MONDAY,
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
            team_a = self.team_a,
            team_b = self.team_b,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(19, 0),
        )   

    def test_result_submission_can_be_created_by_team_a(self):
        submission = MatchResultSubmission(
            match = self.match,
            submitting_team = self.team_a,
            team_a_sets_won = 2,
            team_b_sets_won = 1,

        )

        submission.full_clean()

    def test_result_submission_can_be_created_by_team_b(self):
        submission = MatchResultSubmission(
            match = self.match,
            submitting_team = self.team_b,
            team_a_sets_won = 2,
            team_b_sets_won = 1,

        )

        submission.full_clean()

    def test_result_submission_rejects_team_not_in_match(self):
        submission = MatchResultSubmission(
            match = self.match,
            submitting_team = self.outside_team,
            team_a_sets_won = 2,
            team_b_sets_won = 1,

        )

        with self.assertRaises(ValidationError):
            submission.full_clean()


    def test_same_team_cannot_submit_twice_for_same_match(self):
        MatchResultSubmission.objects.create(
            match= self.match,
            submitting_team = self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        duplicate_submission = MatchResultSubmission(
            match = self.match,
            submitting_team = self.team_a,
            team_a_sets_won = 2,
            team_b_sets_won = 1,

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
            team_a = self.team_a,
            team_b = self.team_b,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(19, 0),
        )

    def test_match_result_status_waiting_when_less_than_two_submissions(self):
        MatchResultSubmission.objects.create(
            match = self.match,
            submitting_team = self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,

        )

        status = get_match_status(self.match)

        self.assertEqual(status, "waiting_for_submissions")


    def test_match_result_status_confirmed_when_submissions_match(self):
        MatchResultSubmission.objects.create(
            match = self.match,
            submitting_team = self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,

        )
        MatchResultSubmission.objects.create(
            match = self.match,
            submitting_team = self.team_b,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        status = get_match_status(self.match)

        self.assertEqual(status, "confirmed")
    
    def test_match_result_status_conflict_when_submissions_disagree(self):
        MatchResultSubmission.objects.create(
            match = self.match,
            submitting_team = self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        MatchResultSubmission.objects.create(
            match = self.match,
            submitting_team = self.team_b,
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

    def test_get_match_winner_returns_team_a_when_team_a_wins(self):
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

        winner = get_match_winner(self.match)

        self.assertEqual(winner, self.team_a)

    def test_get_match_winner_returns_team_b_when_team_b_wins(self):
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

        winner = get_match_winner(self.match)

        self.assertEqual(winner, self.team_b)

    def test_get_match_winner_returns_none_when_result_conflicts(self):
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

        winner = get_match_winner(self.match)

        self.assertIsNone(winner)

    def test_get_match_winner_returns_none_when_waiting_for_submissions(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        winner = get_match_winner(self.match)

        self.assertIsNone(winner)



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


