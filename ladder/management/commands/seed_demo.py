from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from ladder.models import (
    AvailabilitySlot,
    ConfirmedMatchResult,
    LadderStanding,
    Match,
    MatchParticipant,
    MatchSuggestion,
    PlayerProfile,
    Team,
)
from ladder.services import (
    CLUB_TIMEZONE,
    InvalidInput,
    accept_suggestion,
    create_match_suggestion,
    find_opponent_suggestions,
    request_membership_change,
    save_availability,
    submit_match_result,
)


DEMO_PASSWORD = "DemoPass123!"


class Command(BaseCommand):
    help = "Create deterministic local demo data for BC_ladder."

    def handle(self, *args, **options):
        with transaction.atomic():
            self._user("demo-admin", is_staff=True, is_superuser=True)
            mens_players = self._players("mens", PlayerProfile.GENDER_MALE, 8)
            womens_players = self._players("womens", PlayerProfile.GENDER_FEMALE, 8)
            mens_teams = self._teams("Men", Team.DIVISION_MENS, mens_players)
            womens_teams = self._teams("Women", Team.DIVISION_WOMENS, womens_players)
            self._ensure_standings(mens_teams)
            self._ensure_standings(womens_teams)
            self._cancel_open_demo_suggestions(mens_teams + womens_teams)
            self._reset_open_demo_availability(mens_players + womens_players)

        self._availability(mens_players[:4], days_from_now=7, hour=18)
        self._availability(mens_players[4:8], days_from_now=7, hour=18)
        self._availability(mens_players[:4], days_from_now=8, hour=19)
        self._availability(mens_players[4:8], days_from_now=8, hour=19)
        self._availability(mens_players, days_from_now=14, hour=18)
        self._availability(mens_players, days_from_now=15, hour=19)
        self._availability(womens_players[:4], days_from_now=9, hour=18)
        self._availability(womens_players[4:8], days_from_now=9, hour=18)
        self._availability(womens_players, days_from_now=16, hour=18)
        self._availability(womens_players, days_from_now=17, hour=19)

        pending = self._pending_suggestion(mens_teams[0])
        scheduled = self._confirmed_match(mens_teams[0])
        completed = self._completed_match(mens_teams[2], mens_teams[3], mens_players[4:6], mens_players[6:8])
        self._refresh_standings(mens_teams)
        self._refresh_standings(womens_teams)

        self.stdout.write(self.style.SUCCESS("Demo data is ready."))
        self.stdout.write(f"Admin login: demo-admin / {DEMO_PASSWORD}")
        self.stdout.write(f"Player login example: demo-mens-1 / {DEMO_PASSWORD}")
        self.stdout.write(f"Pending suggestion: {pending.id if pending else 'not available'}")
        self.stdout.write(f"Scheduled match: {scheduled.id if scheduled else 'not available'}")
        self.stdout.write(f"Completed match: {completed.id if completed else 'not available'}")

    def _user(self, username, is_staff=False, is_superuser=False):
        User = get_user_model()
        user, _ = User.objects.get_or_create(username=username, defaults={"email": f"{username}@example.com"})
        user.is_staff = is_staff
        user.is_superuser = is_superuser
        user.set_password(DEMO_PASSWORD)
        user.save()
        return user

    def _players(self, prefix, gender, count):
        players = []
        for index in range(1, count + 1):
            username = f"demo-{prefix}-{index}"
            user = self._user(username)
            profile, _ = PlayerProfile.objects.get_or_create(
                user=user,
                defaults={"gender": gender},
            )
            if profile.gender != gender:
                profile.gender = gender
                profile.save(update_fields=["gender"])
            players.append(profile)
        return players

    def _teams(self, label, division, players):
        teams = []
        for index in range(1, 5):
            team, _ = Team.objects.get_or_create(
                name=f"{label} Demo Team {index}",
                defaults={"division": division},
            )
            if team.division != division or team.status != Team.STATUS_ACTIVE:
                team.division = division
                team.status = Team.STATUS_ACTIVE
                team.save(update_fields=["division", "status"])
            teams.append(team)

        for index, player in enumerate(players):
            team = teams[index // 2]
            request_membership_change(player.user, player, team, "join")
        return teams

    def _ensure_standings(self, teams):
        for index, team in enumerate(teams, start=1):
            LadderStanding.objects.update_or_create(
                team=team,
                defaults={"position": index, "matches_played": 0, "wins": 0, "losses": 0, "points": 0},
            )

    def _refresh_standings(self, teams):
        stats = {team.id: {"matches_played": 0, "wins": 0, "losses": 0, "points": 0} for team in teams}
        completed_results = ConfirmedMatchResult.objects.filter(
            winning_team__in=teams,
            losing_team__in=teams,
        ).select_related("winning_team", "losing_team")

        for result in completed_results:
            stats[result.winning_team_id]["matches_played"] += 1
            stats[result.winning_team_id]["wins"] += 1
            stats[result.winning_team_id]["points"] += 3
            stats[result.losing_team_id]["matches_played"] += 1
            stats[result.losing_team_id]["losses"] += 1

        ordered_team_ids = sorted(
            stats,
            key=lambda team_id: (
                -stats[team_id]["points"],
                -stats[team_id]["wins"],
                stats[team_id]["losses"],
                team_id,
            ),
        )
        positions = {team_id: index for index, team_id in enumerate(ordered_team_ids, start=1)}
        for team in teams:
            LadderStanding.objects.update_or_create(
                team=team,
                defaults={"position": positions[team.id], **stats[team.id]},
            )

    def _window(self, days_from_now, hour):
        target_date = timezone.localdate() + timedelta(days=days_from_now)
        starts_at = datetime.combine(target_date, time(hour, 0), tzinfo=CLUB_TIMEZONE)
        ends_at = starts_at + timedelta(hours=2)
        return starts_at, ends_at

    def _availability(self, players, days_from_now, hour):
        starts_at, ends_at = self._window(days_from_now, hour)
        for player in players:
            existing = AvailabilitySlot.objects.filter(player=player, starts_at=starts_at, ends_at=ends_at).first()
            if existing:
                if existing.status == AvailabilitySlot.STATUS_CANCELLED and not existing.match_reservations.exists():
                    existing.status = AvailabilitySlot.STATUS_ACTIVE
                    existing.save(update_fields=["status"])
                continue
            try:
                save_availability(player.user, starts_at, ends_at)
            except InvalidInput as error:
                if "overlaps an existing active window" not in str(error):
                    raise
                self._cancel_open_demo_availability(player)
                save_availability(player.user, starts_at, ends_at)

    def _reset_open_demo_availability(self, players):
        for player in players:
            self._cancel_open_demo_availability(player)

    def _cancel_open_demo_availability(self, player):
        AvailabilitySlot.objects.filter(
            player=player,
            status=AvailabilitySlot.STATUS_ACTIVE,
            match_reservations__isnull=True,
        ).update(status=AvailabilitySlot.STATUS_CANCELLED)

    def _cancel_open_demo_suggestions(self, teams):
        MatchSuggestion.objects.filter(
            team_a__in=teams,
            status__in=[MatchSuggestion.STATUS_PROPOSED, MatchSuggestion.STATUS_PARTIALLY_ACCEPTED],
        ).update(status=MatchSuggestion.STATUS_CANCELLED)

    def _pending_suggestion(self, team):
        starts_at, ends_at = self._window(days_from_now=7, hour=18)
        existing = MatchSuggestion.objects.filter(
            team_a=team,
            starts_at=starts_at,
            ends_at=ends_at,
            status__in=[MatchSuggestion.STATUS_PROPOSED, MatchSuggestion.STATUS_PARTIALLY_ACCEPTED],
        ).first()
        if existing:
            return existing
        options = find_opponent_suggestions(team, (starts_at, ends_at))
        return create_match_suggestion(options[0]) if options else None

    def _confirmed_match(self, team):
        starts_at, ends_at = self._window(days_from_now=8, hour=19)
        existing = Match.objects.filter(scheduled_starts_at=starts_at, scheduled_ends_at=ends_at).first()
        if existing:
            return existing
        options = find_opponent_suggestions(team, (starts_at, ends_at))
        if not options:
            return None
        suggestion = create_match_suggestion(options[0])
        team_a_user = options[0]["team_a_players"][0].user
        team_b_user = options[0]["team_b_players"][0].user
        accept_suggestion(team_a_user, suggestion, suggestion.version)
        return accept_suggestion(team_b_user, suggestion, suggestion.version)

    def _completed_match(self, team_a, team_b, team_a_players, team_b_players):
        starts_at, ends_at = self._window(days_from_now=3, hour=17)
        match, _ = Match.objects.get_or_create(
            team_a=team_a,
            team_b=team_b,
            scheduled_starts_at=starts_at,
            scheduled_ends_at=ends_at,
            defaults={
                "scheduled_week_start_date": starts_at.date() - timedelta(days=starts_at.weekday()),
                "scheduled_day_of_week": list(AvailabilitySlot.DayOfWeek.values)[starts_at.weekday()],
                "scheduled_start_time": starts_at.time(),
                "scheduled_end_time": ends_at.time(),
            },
        )
        self._ensure_match_participants(match, team_a_players, team_b_players)
        if match.status != Match.STATUS_COMPLETED:
            submit_match_result(team_a_players[0].user, match, [(6, 4), (6, 4)])
            submit_match_result(team_b_players[0].user, match, [(6, 4), (6, 4)])
        return match

    def _ensure_match_participants(self, match, team_a_players, team_b_players):
        for order, player in enumerate(team_a_players, start=1):
            MatchParticipant.objects.get_or_create(
                match=match,
                player=player,
                defaults={
                    "team": match.team_a,
                    "side": MatchParticipant.SIDE_A,
                    "lineup_order": order,
                },
            )
        for order, player in enumerate(team_b_players, start=1):
            MatchParticipant.objects.get_or_create(
                match=match,
                player=player,
                defaults={
                    "team": match.team_b,
                    "side": MatchParticipant.SIDE_B,
                    "lineup_order": order,
                },
            )
