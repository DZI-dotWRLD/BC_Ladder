from collections import defaultdict
from datetime import timedelta, timezone as datetime_timezone
from itertools import combinations
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from .models import (
    AdminNotification,
    AvailabilitySlot,
    ConfirmedMatchResult,
    LadderStanding,
    Match,
    MatchParticipant,
    MatchReservation,
    MatchResultSet,
    MatchResultSubmission,
    MatchSuggestion,
    PlayerProfile,
    PointLedger,
    ScoreCorrectionAudit,
    SuggestionAcceptance,
    SuggestionParticipant,
    Team,
    TeamMembership,
)

CLUB_TIMEZONE = ZoneInfo("America/New_York")
WIN_POINTS = 3
DEFAULT_SUGGESTION_EXPIRY = timedelta(days=7)
POSTGRES_AVAILABILITY_OVERLAP_CONSTRAINT = "availability_no_overlap_active_player"
POSTGRES_RESERVATION_OVERLAP_CONSTRAINT = "reservation_no_overlap_active_player"


class DomainError(Exception):
    pass


class InvalidInput(DomainError):
    pass


class StaleState(DomainError):
    pass


class AuthorizationFailure(DomainError):
    pass


class BookingCollision(DomainError):
    pass


def _integrity_constraint_name(error):
    cause = getattr(error, "__cause__", None)
    diagnostic = getattr(cause, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _profile_for_user(user):
    if not user or not getattr(user, "is_authenticated", False):
        raise AuthorizationFailure("Authentication is required.")
    try:
        return user.player_profile
    except PlayerProfile.DoesNotExist as exc:
        raise AuthorizationFailure("User does not have a player profile.") from exc


def _active_memberships_for_team(team):
    memberships = list(
        TeamMembership.objects.filter(team=team, status=TeamMembership.STATUS_ACTIVE)
        .select_related("player__user", "team")
        .order_by("player_id")
    )
    if memberships:
        return memberships

    now = timezone.now()
    return [
        TeamMembership(player=player, team=team, status=TeamMembership.STATUS_ACTIVE, effective_from=now)
        for player in team.players.select_related("user").order_by("id")
    ]


def _active_team_for_player(player):
    membership = TeamMembership.objects.filter(player=player, status=TeamMembership.STATUS_ACTIVE).select_related("team").first()
    return membership.team if membership else player.team


def _player_belongs_to_team(player, team):
    return _active_team_for_player(player) == team


def _suggestion_side_for_player(suggestion, player):
    participant = suggestion.participants.select_related("team").filter(player=player).first()
    if participant is None:
        return None
    return participant.team


def _match_team_for_participant(match, player):
    participant = match.participants.select_related("team").filter(player=player).first()
    if participant:
        return participant.team
    return None


def _validate_player_division(player, team):
    if player.gender == PlayerProfile.GENDER_MALE and team.division != Team.DIVISION_MENS:
        raise InvalidInput("Male players can only join men's teams.")
    if player.gender == PlayerProfile.GENDER_FEMALE and team.division != Team.DIVISION_WOMENS:
        raise InvalidInput("Female players can only join women's teams.")


def _legacy_slot_parts(starts_at, ends_at):
    local_start = timezone.localtime(starts_at, CLUB_TIMEZONE)
    local_end = timezone.localtime(ends_at, CLUB_TIMEZONE)
    week_start = local_start.date() - timedelta(days=local_start.weekday())
    day_value = list(AvailabilitySlot.DayOfWeek.values)[local_start.weekday()]
    return week_start, day_value, local_start.time().replace(microsecond=0), local_end.time().replace(microsecond=0)


def _make_aware(value):
    if timezone.is_naive(value):
        return timezone.make_aware(value, CLUB_TIMEZONE)
    return value.astimezone(datetime_timezone.utc)


def _intervals_overlap(starts_a, ends_a, starts_b, ends_b):
    return starts_a < ends_b and ends_a > starts_b


def _submission_score_signature(submission):
    sets = list(submission.sets.order_by("set_order"))
    if sets:
        return tuple((item.set_order, item.set_type, item.team_a_score, item.team_b_score) for item in sets)
    return (("legacy", submission.team_a_sets_won, submission.team_b_sets_won),)


def _get_or_create_standing(team):
    standing, _ = LadderStanding.objects.select_for_update().get_or_create(
        team=team,
        defaults={
            "position": (LadderStanding.objects.aggregate(max_position=Max("position"))["max_position"] or 0) + 1,
        },
    )
    return standing


def _get_locked_standings_for_teams(*teams):
    ordered_teams = sorted(teams, key=lambda team: team.id)
    for team in ordered_teams:
        LadderStanding.objects.get_or_create(
            team=team,
            defaults={
                "position": (LadderStanding.objects.aggregate(max_position=Max("position"))["max_position"] or 0) + 1,
            },
        )
    standings = {
        standing.team_id: standing
        for standing in LadderStanding.objects.select_for_update().filter(team__in=ordered_teams).select_related("team").order_by("team_id")
    }
    return {team.id: standings[team.id] for team in teams}


def recalculate_ladder_positions(division):
    with transaction.atomic():
        standings = list(LadderStanding.objects.select_for_update().filter(team__division=division).select_related("team"))
        ordered = sorted(
            standings,
            key=lambda standing: (
                -standing.points,
                -standing.wins,
                standing.losses,
                standing.team.name.lower(),
                standing.team_id,
            ),
        )
        for position, standing in enumerate(ordered, start=1):
            if standing.position != position:
                standing.position = position
                standing.save(update_fields=["position", "updated_at"])
        return ordered


def reconcile_ladder_standings(division=None):
    teams = Team.objects.all()
    if division:
        teams = teams.filter(division=division)
    teams = list(teams.order_by("division", "name", "id"))
    team_ids = [team.id for team in teams]
    stats = {team.id: {"matches_played": 0, "wins": 0, "losses": 0, "points": 0} for team in teams}
    results = ConfirmedMatchResult.objects.filter(
        winning_team_id__in=team_ids,
        losing_team_id__in=team_ids,
    ).select_related("winning_team", "losing_team")
    for result in results:
        stats[result.winning_team_id]["matches_played"] += 1
        stats[result.winning_team_id]["wins"] += 1
        stats[result.winning_team_id]["points"] += WIN_POINTS
        stats[result.losing_team_id]["matches_played"] += 1
        stats[result.losing_team_id]["losses"] += 1

    with transaction.atomic():
        for team in teams:
            standing = _get_or_create_standing(team)
            standing.matches_played = stats[team.id]["matches_played"]
            standing.wins = stats[team.id]["wins"]
            standing.losses = stats[team.id]["losses"]
            standing.points = stats[team.id]["points"]
            standing.save(update_fields=["matches_played", "wins", "losses", "points", "updated_at"])
        divisions = {team.division for team in teams}
        for team_division in divisions:
            recalculate_ladder_positions(team_division)
    return stats


def request_membership_change(actor, player, target_team=None, action="join"):
    if not getattr(actor, "is_authenticated", False):
        raise AuthorizationFailure("Authentication is required.")
    if not actor.is_staff and getattr(player, "user_id", None) != actor.id:
        raise AuthorizationFailure("Players may only request changes for themselves.")

    with transaction.atomic():
        locked_player = PlayerProfile.objects.select_for_update().get(pk=player.pk)
        current_active = (
            TeamMembership.objects.select_for_update().filter(player=locked_player, status=TeamMembership.STATUS_ACTIVE).first()
        )

        if action == "request_removal":
            if not current_active:
                raise InvalidInput("Player does not have an active team membership.")
            if current_active.removal_requested_at is None:
                current_active.removal_requested_at = timezone.now()
                current_active.save(update_fields=["removal_requested_at", "updated_at"])
            return current_active

        if action != "join":
            raise InvalidInput("Unsupported membership action.")
        if target_team is None:
            raise InvalidInput("A target team is required.")
        locked_team = Team.objects.select_for_update().get(pk=target_team.pk)
        if locked_team.status != Team.STATUS_ACTIVE:
            raise InvalidInput("Cannot join a retired team.")
        if current_active:
            if current_active.team_id == locked_team.id:
                return current_active
            raise InvalidInput("Player already has an active team membership.")

        _validate_player_division(locked_player, locked_team)
        active_count = TeamMembership.objects.select_for_update().filter(team=locked_team, status=TeamMembership.STATUS_ACTIVE).count()
        legacy_count = locked_team.players.exclude(pk=locked_player.pk).count()
        if max(active_count, legacy_count) >= 3:
            raise InvalidInput("Team already has three active members.")

        membership = TeamMembership.objects.create(
            player=locked_player,
            team=locked_team,
            status=TeamMembership.STATUS_ACTIVE,
            effective_from=timezone.now(),
        )
        locked_player.team = locked_team
        locked_player.save(update_fields=["team"])
        return membership


def resolve_membership_request(admin_actor, membership, decision):
    if not getattr(admin_actor, "is_staff", False):
        raise AuthorizationFailure("Only administrators may resolve membership requests.")
    if decision not in {"approve", "reject"}:
        raise InvalidInput("Decision must be approve or reject.")

    with transaction.atomic():
        locked_membership = TeamMembership.objects.select_for_update().get(pk=membership.pk)
        now = timezone.now()
        locked_membership.reviewed_by = admin_actor
        locked_membership.resolved_at = now
        if decision == "approve":
            locked_membership.status = TeamMembership.STATUS_INACTIVE
            locked_membership.effective_to = now
            locked_membership.player.team = None
            locked_membership.player.save(update_fields=["team"])
        else:
            locked_membership.removal_requested_at = None
        locked_membership.save(
            update_fields=[
                "status",
                "effective_to",
                "removal_requested_at",
                "reviewed_by",
                "resolved_at",
                "updated_at",
            ]
        )
        return locked_membership


def cancel_match(admin_actor, match):
    if not getattr(admin_actor, "is_staff", False):
        raise AuthorizationFailure("Only administrators may cancel matches.")
    with transaction.atomic():
        locked_match = Match.objects.select_for_update().get(pk=match.pk)
        if locked_match.status == Match.STATUS_COMPLETED:
            raise StaleState("Completed matches cannot be cancelled.")
        if locked_match.status == Match.STATUS_CANCELLED:
            return locked_match

        reservations = list(
            locked_match.reservations.select_for_update().filter(status=MatchReservation.STATUS_ACTIVE).order_by("player_id", "id")
        )
        availability_by_id = {
            availability.id: availability
            for availability in AvailabilitySlot.objects.select_for_update().filter(
                id__in=[reservation.availability_id for reservation in reservations if reservation.availability_id]
            )
        }
        for reservation in reservations:
            reservation.status = MatchReservation.STATUS_RELEASED
            reservation.save(update_fields=["status"])
            availability = availability_by_id.get(reservation.availability_id)
            if availability and availability.status == AvailabilitySlot.STATUS_CONSUMED:
                has_other_active = (
                    availability.match_reservations.exclude(pk=reservation.pk).filter(status=MatchReservation.STATUS_ACTIVE).exists()
                )
                if not has_other_active:
                    availability.status = AvailabilitySlot.STATUS_ACTIVE
                    availability.save(update_fields=["status"])

        locked_match.status = Match.STATUS_CANCELLED
        locked_match.save(update_fields=["status", "updated_at"])
        return locked_match


def resolve_score_conflict(admin_actor, notification, official_submission=None, note=""):
    if not getattr(admin_actor, "is_staff", False):
        raise AuthorizationFailure("Only administrators may resolve score conflicts.")
    if official_submission is None:
        raise InvalidInput("An official submission is required to resolve a score conflict.")
    with transaction.atomic():
        locked_notification = AdminNotification.objects.select_for_update().select_related("match").get(pk=notification.pk)
        if locked_notification.notification_type != AdminNotification.TYPE_SCORE_CONFLICT:
            raise InvalidInput("Notification is not a score conflict.")
        locked_submission = (
            MatchResultSubmission.objects.select_for_update()
            .select_related("match", "submitting_team")
            .prefetch_related("sets")
            .get(pk=official_submission.pk)
        )
        if locked_submission.match_id != locked_notification.match_id:
            raise InvalidInput("Official submission must belong to the notification match.")

        existing_audit = ScoreCorrectionAudit.objects.filter(
            match=locked_notification.match,
            official_submission=locked_submission,
            reason=ScoreCorrectionAudit.REASON_CONFLICT_RESOLUTION,
        ).first()
        if locked_notification.is_resolved and existing_audit:
            return existing_audit

        previous_result = getattr(locked_notification.match, "confirmed_result", None)
        winner, loser = _winner_and_loser_from_submission(locked_submission)
        _apply_official_result(
            locked_notification.match,
            locked_submission,
            winner,
            loser,
            previous_result,
        )
        locked_notification.is_resolved = True
        locked_notification.save(update_fields=["is_resolved", "updated_at"])
        return ScoreCorrectionAudit.objects.create(
            match=locked_notification.match,
            corrected_by=admin_actor,
            official_submission=locked_submission,
            previous_winning_team=previous_result.winning_team if previous_result else None,
            previous_losing_team=previous_result.losing_team if previous_result else None,
            new_winning_team=winner,
            new_losing_team=loser,
            reason=ScoreCorrectionAudit.REASON_CONFLICT_RESOLUTION,
            note=note,
        )


def save_availability(actor, starts_at, ends_at):
    player = _profile_for_user(actor)
    starts_at = _make_aware(starts_at)
    ends_at = _make_aware(ends_at)
    if starts_at >= ends_at:
        raise InvalidInput("Availability start must be before end.")

    week_start, day_value, start_time, end_time = _legacy_slot_parts(starts_at, ends_at)
    if start_time >= end_time:
        raise InvalidInput("Availability windows must start and end on the same club-time day.")

    with transaction.atomic():
        active_slots = AvailabilitySlot.objects.select_for_update().filter(
            player=player,
            status=AvailabilitySlot.STATUS_ACTIVE,
            starts_at__isnull=False,
            ends_at__isnull=False,
        )
        if active_slots.filter(starts_at=starts_at, ends_at=ends_at).exists():
            return active_slots.get(starts_at=starts_at, ends_at=ends_at)
        if active_slots.filter(starts_at__lt=ends_at, ends_at__gt=starts_at).exists():
            raise InvalidInput("Availability overlaps an existing active window.")

        try:
            with transaction.atomic():
                return AvailabilitySlot.objects.create(
                    player=player,
                    week_start_date=week_start,
                    day_of_week=day_value,
                    start_time=start_time,
                    end_time=end_time,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    status=AvailabilitySlot.STATUS_ACTIVE,
                )
        except IntegrityError as exc:
            existing = active_slots.filter(starts_at=starts_at, ends_at=ends_at).first()
            if existing:
                return existing
            if _integrity_constraint_name(exc) == POSTGRES_AVAILABILITY_OVERLAP_CONSTRAINT:
                raise InvalidInput("Availability overlaps an existing active window.") from exc
            raise


def cancel_availability(actor, availability):
    player = _profile_for_user(actor)
    with transaction.atomic():
        slot = AvailabilitySlot.objects.select_for_update().get(pk=availability.pk)
        if slot.player_id != player.id:
            raise AuthorizationFailure("User cannot cancel another player's availability.")
        if slot.status != AvailabilitySlot.STATUS_ACTIVE:
            return slot
        if slot.match_reservations.filter(status=MatchReservation.STATUS_ACTIVE).exists():
            raise StaleState("Availability is reserved for a confirmed match.")
        slot.status = AvailabilitySlot.STATUS_CANCELLED
        slot.save(update_fields=["status"])
        return slot


def get_team_players(team: Team):
    """Return active player profiles assigned to the given team."""
    memberships = _active_memberships_for_team(team)
    if memberships:
        return PlayerProfile.objects.filter(id__in=[membership.player_id for membership in memberships]).order_by("id")
    return team.players.order_by("id")


def get_player_pairs(team: Team):
    """Return all possible stable two-player pairings for a team."""
    return list(combinations(list(get_team_players(team)), 2))


def get_slot_key(slot):
    """Return the values that define an exact legacy availability time."""
    if slot.starts_at and slot.ends_at:
        return (slot.starts_at, slot.ends_at)
    return (slot.day_of_week, slot.start_time, slot.end_time)


def get_pair_matching_slots(player_one: PlayerProfile, player_two: PlayerProfile, week_start_date):
    """Return exact matching legacy availability slots shared by two players in a week."""
    player_one_slots = list(
        player_one.availability_slots.filter(
            week_start_date=week_start_date,
            status=AvailabilitySlot.STATUS_ACTIVE,
        ).order_by("day_of_week", "start_time", "end_time", "id")
    )
    player_two_slot_keys = {
        get_slot_key(slot)
        for slot in player_two.availability_slots.filter(
            week_start_date=week_start_date,
            status=AvailabilitySlot.STATUS_ACTIVE,
        )
    }
    return [slot_one for slot_one in player_one_slots if get_slot_key(slot_one) in player_two_slot_keys]


def get_team_pair_availability(team: Team, week_start_date):
    result_list = []
    for player_one, player_two in get_player_pairs(team):
        matching_slots = get_pair_matching_slots(player_one, player_two, week_start_date)
        if matching_slots:
            result_list.append({"players": (player_one, player_two), "slots": matching_slots})
    return result_list


def _active_intervals_for_player(player, interval=None):
    qs = player.availability_slots.filter(
        status=AvailabilitySlot.STATUS_ACTIVE,
        starts_at__isnull=False,
        ends_at__isnull=False,
    )
    if interval:
        qs = qs.filter(starts_at__lt=interval[1], ends_at__gt=interval[0])
    return list(qs.order_by("starts_at", "ends_at", "id"))


def _intersect_sorted_slots(slots_a, slots_b):
    intersections = []
    left = right = 0
    while left < len(slots_a) and right < len(slots_b):
        slot_a = slots_a[left]
        slot_b = slots_b[right]
        starts_at = max(slot_a.starts_at, slot_b.starts_at)
        ends_at = min(slot_a.ends_at, slot_b.ends_at)
        if starts_at < ends_at:
            intersections.append((starts_at, ends_at, slot_a, slot_b))
        if slot_a.ends_at <= slot_b.ends_at:
            left += 1
        else:
            right += 1
    return intersections


def generate_team_lineups(team, interval=None):
    lineups = []
    for player_one, player_two in get_player_pairs(team):
        slots_one = _active_intervals_for_player(player_one, interval)
        slots_two = _active_intervals_for_player(player_two, interval)
        for starts_at, ends_at, slot_one, slot_two in _intersect_sorted_slots(slots_one, slots_two):
            lineups.append(
                {
                    "team": team,
                    "players": tuple(sorted((player_one, player_two), key=lambda player: player.id)),
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "availability": {player_one.id: slot_one, player_two.id: slot_two},
                }
            )
    return sorted(lineups, key=lambda item: (item["starts_at"], item["ends_at"], [p.id for p in item["players"]]))


def find_team_match_options(team_a: Team, team_b: Team, week_start_date):
    if team_a.id == team_b.id or team_a.division != team_b.division:
        return []
    match_options = []
    team_a_availability = get_team_pair_availability(team_a, week_start_date)
    team_b_availability = get_team_pair_availability(team_b, week_start_date)

    team_b_pairs_by_slot = defaultdict(list)
    for pair_availability in team_b_availability:
        for slot in pair_availability["slots"]:
            team_b_pairs_by_slot[get_slot_key(slot)].append(pair_availability["players"])

    for team_a_pair_availability in team_a_availability:
        for team_a_slot in team_a_pair_availability["slots"]:
            for team_b_players in team_b_pairs_by_slot.get(get_slot_key(team_a_slot), []):
                match_options.append(
                    {
                        "team_a_players": team_a_pair_availability["players"],
                        "team_b_players": team_b_players,
                        "slot": team_a_slot,
                    }
                )
    return match_options


def find_opponent_suggestions(team, interval):
    if interval[0] >= interval[1]:
        raise InvalidInput("Search interval start must be before end.")
    own_lineups = generate_team_lineups(team, interval)
    opponents = Team.active.filter(division=team.division).exclude(pk=team.pk).order_by("id")
    options = []
    for opponent in opponents:
        for own in own_lineups:
            for other in generate_team_lineups(opponent, (own["starts_at"], own["ends_at"])):
                starts_at = max(own["starts_at"], other["starts_at"])
                ends_at = min(own["ends_at"], other["ends_at"])
                if starts_at >= ends_at:
                    continue
                player_ids = [player.id for player in own["players"] + other["players"]]
                if len(set(player_ids)) != 4:
                    continue
                if _has_reservation_conflict(player_ids, starts_at, ends_at):
                    continue
                options.append(
                    {
                        "team_a": team,
                        "team_b": opponent,
                        "team_a_players": own["players"],
                        "team_b_players": other["players"],
                        "starts_at": starts_at,
                        "ends_at": ends_at,
                        "availability": {**own["availability"], **other["availability"]},
                    }
                )
    return sorted(
        options,
        key=lambda item: (
            abs(getattr(item["team_a"], "standing", None).points - getattr(item["team_b"], "standing", None).points)
            if hasattr(item["team_a"], "standing") and hasattr(item["team_b"], "standing")
            else 0,
            item["starts_at"],
            item["team_b"].id,
            [player.id for player in item["team_a_players"]],
            [player.id for player in item["team_b_players"]],
        ),
    )


def _participant_signature_for_option(option):
    return {
        SuggestionParticipant.SIDE_A: tuple(player.id for player in option["team_a_players"]),
        SuggestionParticipant.SIDE_B: tuple(player.id for player in option["team_b_players"]),
    }


def _suggestion_participant_signature(suggestion):
    signature = defaultdict(list)
    for participant in suggestion.participants.order_by("side", "lineup_order", "player_id"):
        signature[participant.side].append(participant.player_id)
    return {side: tuple(player_ids) for side, player_ids in signature.items()}


def create_match_suggestion(option, expires_at=None):
    expires_at = expires_at or option["starts_at"]
    with transaction.atomic():
        option_signature = _participant_signature_for_option(option)
        existing_suggestions = (
            MatchSuggestion.objects.select_for_update()
            .filter(
                team_a=option["team_a"],
                team_b=option["team_b"],
                starts_at=option["starts_at"],
                ends_at=option["ends_at"],
                status__in=[MatchSuggestion.STATUS_PROPOSED, MatchSuggestion.STATUS_PARTIALLY_ACCEPTED],
            )
            .prefetch_related("participants")
            .order_by("id")
        )
        for existing in existing_suggestions:
            if _suggestion_participant_signature(existing) == option_signature:
                return existing

        suggestion = MatchSuggestion.objects.create(
            team_a=option["team_a"],
            team_b=option["team_b"],
            starts_at=option["starts_at"],
            ends_at=option["ends_at"],
            expires_at=expires_at,
        )
        rows = []
        for side, team_key, players_key in (
            (SuggestionParticipant.SIDE_A, "team_a", "team_a_players"),
            (SuggestionParticipant.SIDE_B, "team_b", "team_b_players"),
        ):
            for order, player in enumerate(option[players_key], start=1):
                rows.append(
                    SuggestionParticipant(
                        suggestion=suggestion,
                        team=option[team_key],
                        player=player,
                        side=side,
                        lineup_order=order,
                    )
                )
        SuggestionParticipant.objects.bulk_create(rows)
        return suggestion


def _has_reservation_conflict(player_ids, starts_at, ends_at):
    return MatchReservation.objects.filter(
        player_id__in=player_ids,
        status=MatchReservation.STATUS_ACTIVE,
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    ).exists()


def _players_for_suggestion(suggestion):
    participants = list(suggestion.participants.select_related("player", "team").order_by("side", "lineup_order", "player_id"))
    by_side = defaultdict(list)
    for participant in participants:
        by_side[participant.side].append(participant)
    if len(by_side[SuggestionParticipant.SIDE_A]) != 2 or len(by_side[SuggestionParticipant.SIDE_B]) != 2:
        raise InvalidInput("Suggestion must have exactly two players per side.")
    player_ids = [participant.player_id for participant in participants]
    if len(set(player_ids)) != 4:
        raise InvalidInput("Suggestion must have four distinct players.")
    return by_side


def _availability_covering(player, starts_at, ends_at):
    return (
        AvailabilitySlot.objects.select_for_update()
        .filter(
            player=player,
            status=AvailabilitySlot.STATUS_ACTIVE,
            starts_at__lte=starts_at,
            ends_at__gte=ends_at,
        )
        .order_by("starts_at", "ends_at", "id")
        .first()
    )


def accept_suggestion(actor, suggestion, expected_version):
    actor_profile = _profile_for_user(actor)
    with transaction.atomic():
        locked = MatchSuggestion.objects.select_for_update().select_related("team_a", "team_b").get(pk=suggestion.pk)
        if locked.version != expected_version:
            raise StaleState("Suggestion version is stale.")
        if locked.status in {MatchSuggestion.STATUS_EXPIRED, MatchSuggestion.STATUS_CANCELLED, MatchSuggestion.STATUS_DECLINED}:
            raise StaleState("Suggestion is no longer acceptible.")
        if locked.expires_at <= timezone.now():
            locked.status = MatchSuggestion.STATUS_EXPIRED
            locked.save(update_fields=["status", "updated_at"])
            raise StaleState("Suggestion has expired.")

        actor_team = _suggestion_side_for_player(locked, actor_profile)
        if actor_team is None:
            raise AuthorizationFailure("Only selected lineup players may accept this suggestion.")
        if actor_team not in {locked.team_a, locked.team_b}:
            raise AuthorizationFailure("User cannot accept for this suggestion.")

        acceptance, _ = SuggestionAcceptance.objects.get_or_create(
            suggestion=locked,
            team=actor_team,
            defaults={"accepted_by": actor, "accepted_version": expected_version},
        )
        if acceptance.accepted_version != expected_version:
            raise StaleState("Existing acceptance is for a stale suggestion version.")

        accepted_team_ids = set(locked.acceptances.select_for_update().values_list("team_id", flat=True))
        required_team_ids = {locked.team_a_id, locked.team_b_id}
        if accepted_team_ids != required_team_ids:
            locked.status = MatchSuggestion.STATUS_PARTIALLY_ACCEPTED
            locked.save(update_fields=["status", "updated_at"])
            return locked
        return _confirm_suggestion_locked(locked)


def _confirm_suggestion_locked(suggestion):
    if hasattr(suggestion, "confirmed_match"):
        return suggestion.confirmed_match

    if suggestion.team_a.division != suggestion.team_b.division:
        raise InvalidInput("Teams must be in the same division.")
    by_side = _players_for_suggestion(suggestion)
    participants = by_side[SuggestionParticipant.SIDE_A] + by_side[SuggestionParticipant.SIDE_B]
    player_ids = [participant.player_id for participant in participants]

    for participant in participants:
        if not _player_belongs_to_team(participant.player, participant.team):
            raise StaleState("A suggested player is no longer active on the suggested team.")
    if _has_reservation_conflict(player_ids, suggestion.starts_at, suggestion.ends_at):
        raise BookingCollision("One or more players already has a reservation for this window.")

    availability_by_player = {}
    for participant in participants:
        availability = _availability_covering(participant.player, suggestion.starts_at, suggestion.ends_at)
        if availability is None:
            raise BookingCollision("Required availability is no longer active.")
        availability_by_player[participant.player_id] = availability

    week_start, day_value, start_time, end_time = _legacy_slot_parts(suggestion.starts_at, suggestion.ends_at)
    match = Match.objects.create(
        team_a=suggestion.team_a,
        team_b=suggestion.team_b,
        source_suggestion=suggestion,
        scheduled_week_start_date=week_start,
        scheduled_day_of_week=day_value,
        scheduled_start_time=start_time,
        scheduled_end_time=end_time,
        scheduled_starts_at=suggestion.starts_at,
        scheduled_ends_at=suggestion.ends_at,
    )
    for participant in participants:
        side = MatchParticipant.SIDE_A if participant.side == SuggestionParticipant.SIDE_A else MatchParticipant.SIDE_B
        MatchParticipant.objects.create(
            match=match,
            team=participant.team,
            player=participant.player,
            side=side,
            lineup_order=participant.lineup_order,
        )
        availability = availability_by_player[participant.player_id]
        try:
            with transaction.atomic():
                MatchReservation.objects.create(
                    match=match,
                    player=participant.player,
                    availability=availability,
                    starts_at=suggestion.starts_at,
                    ends_at=suggestion.ends_at,
                )
        except IntegrityError as exc:
            if _integrity_constraint_name(exc) == POSTGRES_RESERVATION_OVERLAP_CONSTRAINT:
                raise BookingCollision("One or more players already has a reservation for this window.") from exc
            raise
        availability.status = AvailabilitySlot.STATUS_CONSUMED
        availability.save(update_fields=["status"])

    suggestion.status = MatchSuggestion.STATUS_CONFIRMED
    suggestion.save(update_fields=["status", "updated_at"])
    return match


def validate_regular_set(team_a_games, team_b_games):
    if not isinstance(team_a_games, int) or not isinstance(team_b_games, int):
        raise InvalidInput("Set scores must be integers.")
    if team_a_games < 0 or team_b_games < 0 or team_a_games == team_b_games:
        raise InvalidInput("Regular set scores must be non-negative and cannot be tied.")
    high = max(team_a_games, team_b_games)
    low = min(team_a_games, team_b_games)
    if high == 6 and low <= 4:
        return 1 if team_a_games > team_b_games else 2
    if high == 7 and low in {5, 6}:
        return 1 if team_a_games > team_b_games else 2
    raise InvalidInput("Invalid regular set score.")


def validate_match_tiebreak(team_a_points, team_b_points):
    if not isinstance(team_a_points, int) or not isinstance(team_b_points, int):
        raise InvalidInput("Tie-break scores must be integers.")
    if team_a_points < 0 or team_b_points < 0 or team_a_points == team_b_points:
        raise InvalidInput("Tie-break scores must be non-negative and cannot be tied.")
    if max(team_a_points, team_b_points) < 10 or abs(team_a_points - team_b_points) < 2:
        raise InvalidInput("A deciding match tie-break must be at least 10 points and won by two.")
    return 1 if team_a_points > team_b_points else 2


def validate_match_score(sets):
    normalized = []
    team_a_sets = 0
    team_b_sets = 0
    if len(sets) not in {2, 3}:
        raise InvalidInput("A match score must contain two or three sets.")

    first_two = sets[:2]
    for index, item in enumerate(first_two, start=1):
        a_score, b_score = _extract_scores(item)
        winner = validate_regular_set(a_score, b_score)
        team_a_sets += 1 if winner == 1 else 0
        team_b_sets += 1 if winner == 2 else 0
        normalized.append(
            {"set_order": index, "set_type": MatchResultSet.SET_TYPE_REGULAR, "team_a_score": a_score, "team_b_score": b_score}
        )

    if team_a_sets == 2 or team_b_sets == 2:
        if len(sets) != 2:
            raise InvalidInput("A deciding match tie-break is not allowed after a straight-set win.")
        winning_side = 1 if team_a_sets == 2 else 2
    else:
        if len(sets) != 3:
            raise InvalidInput("Split regular sets require a deciding match tie-break.")
        a_score, b_score = _extract_scores(sets[2])
        winning_side = validate_match_tiebreak(a_score, b_score)
        normalized.append(
            {
                "set_order": 3,
                "set_type": MatchResultSet.SET_TYPE_MATCH_TIEBREAK,
                "team_a_score": a_score,
                "team_b_score": b_score,
            }
        )

    winner_team_side = "team_a" if winning_side == 1 else "team_b"
    return {
        "sets": normalized,
        "team_a_sets_won": 2 if winner_team_side == "team_a" else 1 if len(normalized) == 3 else 0,
        "team_b_sets_won": 2 if winner_team_side == "team_b" else 1 if len(normalized) == 3 else 0,
        "winner_team_side": winner_team_side,
    }


def _extract_scores(item):
    if isinstance(item, dict):
        return item.get("team_a_score"), item.get("team_b_score")
    if isinstance(item, (tuple, list)) and len(item) == 2:
        return item[0], item[1]
    raise InvalidInput("Malformed set score.")


def submit_match_result(actor, match, normalized_sets):
    actor_profile = _profile_for_user(actor)
    score = validate_match_score(normalized_sets)
    with transaction.atomic():
        locked_match = Match.objects.select_for_update().select_related("team_a", "team_b").get(pk=match.pk)
        if locked_match.status != Match.STATUS_SCHEDULED:
            raise StaleState("Only scheduled matches can receive scores.")
        submitting_team = _match_team_for_participant(locked_match, actor_profile)
        if submitting_team is None:
            raise AuthorizationFailure("Only selected match participants may submit a result.")
        if submitting_team not in {locked_match.team_a, locked_match.team_b}:
            raise AuthorizationFailure("User cannot submit a result for this match.")

        submission, created = MatchResultSubmission.objects.select_for_update().get_or_create(
            match=locked_match,
            submitting_team=submitting_team,
            defaults={
                "submitting_user": actor,
                "team_a_sets_won": score["team_a_sets_won"],
                "team_b_sets_won": score["team_b_sets_won"],
            },
        )
        new_signature = tuple((item["set_order"], item["set_type"], item["team_a_score"], item["team_b_score"]) for item in score["sets"])
        if not created:
            if _submission_score_signature(submission) == new_signature:
                return submission
            raise StaleState("A different score has already been submitted by this team.")

        MatchResultSet.objects.bulk_create(
            [
                MatchResultSet(
                    submission=submission,
                    set_order=item["set_order"],
                    set_type=item["set_type"],
                    team_a_score=item["team_a_score"],
                    team_b_score=item["team_b_score"],
                )
                for item in score["sets"]
            ]
        )
        if get_match_status(locked_match) == "confirmed":
            finalize_match_result(locked_match)
        elif get_match_status(locked_match) == "conflict":
            create_admin_notification_for_conflict(locked_match)
        return submission


def get_submissions(match: Match):
    return match.result_submissions.select_related("submitting_team").prefetch_related("sets")


def submissions_match(submission_one, submission_two):
    return _submission_score_signature(submission_one) == _submission_score_signature(submission_two)


def get_match_status(match):
    submissions = list(get_submissions(match).order_by("submitting_team_id"))
    if len(submissions) < 2:
        return "waiting_for_submissions"
    if submissions_match(submissions[0], submissions[1]):
        return "confirmed"
    return "conflict"


def create_admin_notification_for_conflict(match):
    if get_match_status(match) != "conflict":
        return None
    notification, _ = AdminNotification.objects.get_or_create(
        match=match,
        notification_type=AdminNotification.TYPE_SCORE_CONFLICT,
        is_resolved=False,
        defaults={"message": "Result submissions do not match. Admin review is required."},
    )
    return notification


def complete_match_if_result_confirmed(match):
    if get_match_status(match) != "confirmed":
        return False
    finalize_match_result(match)
    return True


def get_match_winner_and_loser(match):
    if get_match_status(match) != "confirmed":
        return []
    submission = list(get_submissions(match).order_by("submitting_team_id"))[0]
    return list(_winner_and_loser_from_submission(submission))


def _winner_and_loser_from_submission(submission):
    match = submission.match
    if submission.sets.exists():
        score = validate_match_score([(item.team_a_score, item.team_b_score) for item in submission.sets.order_by("set_order")])
        return (match.team_a, match.team_b) if score["winner_team_side"] == "team_a" else (match.team_b, match.team_a)
    if submission.team_a_sets_won > submission.team_b_sets_won:
        return (match.team_a, match.team_b)
    if submission.team_a_sets_won < submission.team_b_sets_won:
        return (match.team_b, match.team_a)
    raise InvalidInput("Match must have exactly one winner.")


def _decrement_standing_for_previous_result(result):
    standings = _get_locked_standings_for_teams(result.winning_team, result.losing_team)
    previous_winner = standings[result.winning_team_id]
    previous_loser = standings[result.losing_team_id]
    previous_winner.matches_played = max(0, previous_winner.matches_played - 1)
    previous_winner.wins = max(0, previous_winner.wins - 1)
    previous_winner.points = max(0, previous_winner.points - WIN_POINTS)
    previous_loser.matches_played = max(0, previous_loser.matches_played - 1)
    previous_loser.losses = max(0, previous_loser.losses - 1)
    previous_winner.save(update_fields=["matches_played", "wins", "points", "updated_at"])
    previous_loser.save(update_fields=["matches_played", "losses", "updated_at"])


def _increment_standings_for_result(winner, loser):
    standings = _get_locked_standings_for_teams(winner, loser)
    winner_standing = standings[winner.id]
    loser_standing = standings[loser.id]
    winner_standing.matches_played += 1
    winner_standing.wins += 1
    winner_standing.points += WIN_POINTS
    loser_standing.matches_played += 1
    loser_standing.losses += 1
    winner_standing.save(update_fields=["matches_played", "wins", "points", "updated_at"])
    loser_standing.save(update_fields=["matches_played", "losses", "updated_at"])


def _apply_official_result(match, submission, winner, loser, previous_result=None):
    if not winner or winner == loser:
        raise InvalidInput("Match must have exactly one winner.")
    if previous_result:
        if previous_result.winning_team_id != winner.id or previous_result.losing_team_id != loser.id:
            _decrement_standing_for_previous_result(previous_result)
            _increment_standings_for_result(winner, loser)
        previous_result.winning_team = winner
        previous_result.losing_team = loser
        previous_result.confirmed_from_submission = submission
        previous_result.save(update_fields=["winning_team", "losing_team", "confirmed_from_submission"])
    else:
        _increment_standings_for_result(winner, loser)
        ConfirmedMatchResult.objects.create(
            match=match,
            winning_team=winner,
            losing_team=loser,
            confirmed_from_submission=submission,
        )

    PointLedger.objects.update_or_create(
        match=match,
        team=winner,
        reason="match_result",
        defaults={"points_delta": WIN_POINTS},
    )
    PointLedger.objects.update_or_create(
        match=match,
        team=loser,
        reason="match_result",
        defaults={"points_delta": 0},
    )
    match.status = Match.STATUS_COMPLETED
    match.save(update_fields=["status", "updated_at"])
    recalculate_ladder_positions(match.team_a.division)


def finalize_match_result(match):
    with transaction.atomic():
        locked_match = Match.objects.select_for_update().select_related("team_a", "team_b").get(pk=match.pk)
        if hasattr(locked_match, "confirmed_result"):
            return locked_match.confirmed_result
        if get_match_status(locked_match) != "confirmed":
            raise StaleState("Match result is not confirmed.")

        winner, loser = get_match_winner_and_loser(locked_match)
        if not winner or winner == loser:
            raise InvalidInput("Match must have exactly one winner.")
        submission = list(get_submissions(locked_match).order_by("submitting_team_id"))[0]

        _apply_official_result(locked_match, submission, winner, loser)
        return locked_match.confirmed_result


def update_ladder_stats_for_match(match):
    if get_match_status(match) != "confirmed":
        return False
    finalize_match_result(match)
    return True
