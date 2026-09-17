from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from .models import (
    AvailabilitySlot,
    PlayerProfile,
    Team,
    TeamMembership,
)
from .services import (
    get_player_pairs,
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
        player = PlayerProfile.objects.create(user=user, gender=PlayerProfile.GENDER_MALE)
        TeamMembership.objects.create(
            player=player,
            team=team,
            status=TeamMembership.STATUS_ACTIVE,
            effective_from=timezone.now(),
        )
        return player

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
        player = PlayerProfile.objects.create(user=user, gender=gender)
        if team is not None:
            TeamMembership.objects.create(
                player=player,
                team=team,
                status=TeamMembership.STATUS_ACTIVE,
                effective_from=timezone.now(),
            )
        return player

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
