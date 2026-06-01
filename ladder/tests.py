from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import AvailabilitySlot, PlayerProfile, Team
from .services import find_team_match_options, get_pair_matching_slots


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
