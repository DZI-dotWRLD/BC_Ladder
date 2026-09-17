from collections import defaultdict
from bisect import bisect_left
from datetime import datetime, timedelta, timezone as datetime_timezone
from hashlib import sha256
from itertools import combinations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core import signing
from django.db import IntegrityError, connection, transaction
from django.db.models import Max, Q
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .models import (
    AdminNotification,
    AvailabilitySlot,
    ConfirmedMatchResult,
    EmailNotificationDelivery,
    LadderStanding,
    Match,
    MatchParticipant,
    MatchReservation,
    MatchResultSet,
    MatchResultSubmission,
    MatchSuggestion,
    PlayerProfile,
    PointLedger,
    RateLimitEvent,
    ScoreCorrectionAudit,
    SuggestionAcceptance,
    SuggestionParticipant,
    Team,
    TeamMembership,
    WorkflowEvent,
)
from .email_notifications import queue_email_notifications
from .workflow_events import (
    active_staff_user_ids,
    match_participant_user_ids,
    record_workflow_event,
    suggestion_participant_user_ids,
)

WIN_POINTS = 3
DEFAULT_SUGGESTION_EXPIRY = timedelta(days=7)
POSTGRES_AVAILABILITY_OVERLAP_CONSTRAINT = "availability_no_overlap_active_player"
POSTGRES_RESERVATION_OVERLAP_CONSTRAINT = "reservation_no_overlap_active_player"
STANDINGS_LOCK_NAMESPACE = 0x42434C44
STANDINGS_DIVISION_LOCK_IDS = {Team.DIVISION_MENS: 1, Team.DIVISION_WOMENS: 2}
STANDINGS_OTHER_DIVISION_LOCK_ID = 3


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


class ProvisioningError(DomainError):
    pass


def provision_first_administrator(username, email):
    """Create and notify the first superuser, or report an existing provision."""
    user_model = get_user_model()
    if user_model.objects.filter(is_superuser=True).exists():
        return None
    username = (username or "").strip()
    email = (email or "").strip()
    if not username or not email:
        raise ProvisioningError("Bootstrap administrator username and email are required.")

    try:
        with transaction.atomic():
            if user_model.objects.select_for_update().filter(is_superuser=True).exists():
                return None
            user = user_model(
                username=username,
                email=user_model.objects.normalize_email(email),
                is_active=True,
                is_staff=True,
                is_superuser=True,
            )
            user.set_unusable_password()
            user.full_clean()
            user.save()

            domain = settings.ALLOWED_HOSTS[0] if settings.ALLOWED_HOSTS else "localhost"
            context = {
                "domain": domain,
                "protocol": "http" if settings.DEBUG else "https",
                "uid": urlsafe_base64_encode(force_bytes(user.pk)),
                "token": default_token_generator.make_token(user),
            }
            try:
                sent = send_mail(
                    "Set your BC Tennis Ladder administrator password",
                    render_to_string("registration/bootstrap_admin_email.txt", context),
                    None,
                    [user.email],
                )
            except Exception as error:
                raise ProvisioningError("Administrator email could not be sent; no account was created.") from error
            if sent != 1:
                raise ProvisioningError("Administrator email could not be sent; no account was created.")
            return user
    except ValidationError as error:
        raise ProvisioningError("Bootstrap administrator settings are invalid.") from error
    except IntegrityError as error:
        if user_model.objects.filter(is_superuser=True).exists():
            return None
        raise ProvisioningError("Bootstrap administrator conflicts with an existing account.") from error


def rate_limit_key(scope, value):
    """Return a stable opaque key so rate-limit storage contains no raw identity."""
    digest = sha256(str(value).strip().lower().encode()).hexdigest()
    return f"{scope}:{digest}"


def enforce_rate_limit(key, limit, window):
    """Record one allowed attempt or raise when the rolling DB window is full."""
    if not key or limit < 1 or window <= timedelta(0):
        raise InvalidInput("Invalid rate-limit configuration.")
    cutoff = timezone.now() - window
    with transaction.atomic():
        if connection.vendor == "postgresql":
            lock_id = int.from_bytes(sha256(key.encode()).digest()[:8], signed=True)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])
        if RateLimitEvent.objects.filter(key=key, created_at__gte=cutoff).count() >= limit:
            raise InvalidInput("Too many requests. Try again later.")
        RateLimitEvent.objects.create(key=key)


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


def _lock_player_profiles(player_ids):
    """Stable parents serialize children that do not exist yet; always lock first."""
    return {
        player.pk: player for player in PlayerProfile.objects.select_for_update(of=("self",)).filter(pk__in=set(player_ids)).order_by("pk")
    }


def _lock_teams(team_ids):
    # Team IDs do not change: permit FK KEY SHARE references from scored history
    # while still serializing status/capacity changes and eligibility checks.
    return {team.pk: team for team in Team.objects.select_for_update(no_key=True, of=("self",)).filter(pk__in=set(team_ids)).order_by("pk")}


def _validate_booking_roster(team_a, team_b, players_a, players_b):
    if team_a.pk == team_b.pk or team_a.division != team_b.division or team_a.division not in {Team.DIVISION_MENS, Team.DIVISION_WOMENS}:
        raise InvalidInput("Suggestions require different teams in the same ladder.")
    if team_a.status != Team.STATUS_ACTIVE or team_b.status != Team.STATUS_ACTIVE:
        raise StaleState("A suggested team is no longer active.")
    if len(players_a) != 2 or len(players_b) != 2 or len(set(players_a + players_b)) != 4:
        raise InvalidInput("Suggestion must have exactly two players per team and four distinct players.")
    memberships = {
        membership.player_id: membership.team_id
        for membership in TeamMembership.objects.select_for_update(of=("self",))
        .filter(player_id__in=players_a + players_b, status=TeamMembership.STATUS_ACTIVE)
        .order_by("pk")
    }
    for team, player_ids in ((team_a, players_a), (team_b, players_b)):
        if any(memberships.get(player_id) != team.pk for player_id in player_ids):
            raise StaleState("A suggested player is no longer active on the suggested team.")
        for player in PlayerProfile.objects.filter(pk__in=player_ids):
            _validate_player_division(player, team)


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
    local_start = timezone.localtime(starts_at, timezone.get_default_timezone())
    local_end = timezone.localtime(ends_at, timezone.get_default_timezone())
    week_start = local_start.date() - timedelta(days=local_start.weekday())
    day_value = list(AvailabilitySlot.DayOfWeek.values)[local_start.weekday()]
    return week_start, day_value, local_start.time().replace(microsecond=0), local_end.time().replace(microsecond=0)


def _make_aware(value):
    if timezone.is_naive(value):
        club_timezone = timezone.get_default_timezone()
        if value.replace(tzinfo=club_timezone, fold=0).utcoffset() != value.replace(tzinfo=club_timezone, fold=1).utcoffset():
            raise InvalidInput("Availability time is ambiguous or does not exist in the configured club timezone.")
        value = timezone.make_aware(value, club_timezone)
    return value.astimezone(datetime_timezone.utc)


def _intervals_overlap(starts_a, ends_a, starts_b, ends_b):
    return starts_a < ends_b and ends_a > starts_b


def _submission_score_signature(submission):
    sets = list(submission.sets.order_by("set_order"))
    if sets:
        return tuple((item.set_order, item.set_type, item.team_a_score, item.team_b_score) for item in sets)
    return (("legacy", submission.team_a_sets_won, submission.team_b_sets_won),)


def _lock_standings_divisions(divisions=None):
    """Serialize before any standing row, including rows that do not exist yet."""
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError("Standings locks require an active transaction.")
    if connection.vendor != "postgresql":
        return
    if divisions is None:
        lock_ids = {*STANDINGS_DIVISION_LOCK_IDS.values(), STANDINGS_OTHER_DIVISION_LOCK_ID}
    else:
        lock_ids = {STANDINGS_DIVISION_LOCK_IDS.get(division, STANDINGS_OTHER_DIVISION_LOCK_ID) for division in divisions}
    with connection.cursor() as cursor:
        for lock_id in sorted(lock_ids):
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [STANDINGS_LOCK_NAMESPACE, lock_id])


def _get_or_create_standing(team):
    _lock_standings_divisions([team.division])
    standing, _ = LadderStanding.objects.select_for_update().get_or_create(
        team=team,
        defaults={
            "position": (LadderStanding.objects.aggregate(max_position=Max("position"))["max_position"] or 0) + 1,
        },
    )
    return standing


def _get_locked_standings_for_teams(*teams):
    _lock_standings_divisions([team.division for team in teams])
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
        for standing in LadderStanding.objects.select_for_update(of=("self",))
        .filter(team__in=ordered_teams)
        .select_related("team")
        .order_by("team_id")
    }
    return {team.id: standings[team.id] for team in teams}


def recalculate_ladder_positions(division):
    with transaction.atomic():
        _lock_standings_divisions([division])
        standings = list(
            LadderStanding.objects.select_for_update(of=("self",))
            .filter(team__division=division)
            .select_related("team")
            .order_by("team_id")
        )
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
    with transaction.atomic():
        # Take the source snapshot after the gate, so a waiting reconciliation
        # cannot overwrite a result that committed while it was waiting.
        _lock_standings_divisions([division] if division else None)
        teams = Team.objects.all()
        if division:
            teams = teams.filter(division=division)
        teams = list(teams.order_by("id"))
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

        for team in teams:
            standing = _get_or_create_standing(team)
            standing.matches_played = stats[team.id]["matches_played"]
            standing.wins = stats[team.id]["wins"]
            standing.losses = stats[team.id]["losses"]
            standing.points = stats[team.id]["points"]
            standing.save(update_fields=["matches_played", "wins", "losses", "points", "updated_at"])
        divisions = {team.division for team in teams}
        for team_division in sorted(divisions):
            recalculate_ladder_positions(team_division)
    return stats


def request_membership_change(actor, player, target_team=None, action="join"):
    if not getattr(actor, "is_authenticated", False):
        raise AuthorizationFailure("Authentication is required.")
    if not actor.is_staff and getattr(player, "user_id", None) != actor.id:
        raise AuthorizationFailure("Players may only request changes for themselves.")

    with transaction.atomic():
        locked_player = PlayerProfile.objects.select_for_update().get(pk=player.pk)
        current_team_ids = list(
            TeamMembership.objects.filter(player=locked_player, status=TeamMembership.STATUS_ACTIVE).values_list("team_id", flat=True)
        )
        teams = _lock_teams(current_team_ids + ([target_team.pk] if target_team is not None else []))
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

        if action not in {"join", "request_join"}:
            raise InvalidInput("Unsupported membership action.")
        if target_team is None:
            raise InvalidInput("A target team is required.")
        locked_team = teams[target_team.pk]
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

        if action == "request_join":
            existing_request = (
                TeamMembership.objects.select_for_update()
                .filter(player=locked_player, status=TeamMembership.STATUS_JOIN_REQUESTED)
                .order_by("created_at", "id")
                .first()
            )
            if existing_request:
                if existing_request.team_id == locked_team.id:
                    return existing_request
                raise InvalidInput("Player already has a pending team request.")

            membership = TeamMembership.objects.create(
                player=locked_player,
                team=locked_team,
                status=TeamMembership.STATUS_JOIN_REQUESTED,
                effective_from=timezone.now(),
            )
            record_workflow_event(
                event_type=WorkflowEvent.EventType.JOIN_REQUEST_CREATED,
                dedupe_key=f"join_request_created:{membership.id}",
                actor=actor,
                membership=membership,
                recipient_user_ids={locked_player.user_id} | active_staff_user_ids(),
                previous_state="",
                new_state=TeamMembership.STATUS_JOIN_REQUESTED,
                metadata={"player_id": locked_player.id, "team_id": locked_team.id, "team_name": locked_team.name},
            )
            return membership

        membership = TeamMembership.objects.create(
            player=locked_player,
            team=locked_team,
            status=TeamMembership.STATUS_ACTIVE,
            effective_from=timezone.now(),
        )
        locked_player.team = locked_team
        locked_player.save(update_fields=["team"])
        return membership


def create_team_for_player(actor, player, team_name):
    if not getattr(actor, "is_authenticated", False):
        raise AuthorizationFailure("Authentication is required.")
    if not actor.is_staff and getattr(player, "user_id", None) != actor.id:
        raise AuthorizationFailure("Players may only create a team for themselves.")

    normalized_name = " ".join((team_name or "").split())
    if not normalized_name:
        raise InvalidInput("Team name is required.")
    if len(normalized_name) > Team._meta.get_field("name").max_length:
        raise InvalidInput("Team name is too long.")

    if player.gender == PlayerProfile.GENDER_MALE:
        division = Team.DIVISION_MENS
    elif player.gender == PlayerProfile.GENDER_FEMALE:
        division = Team.DIVISION_WOMENS
    else:
        raise InvalidInput("Player profile must have a valid division.")

    with transaction.atomic():
        locked_player = PlayerProfile.objects.select_for_update().get(pk=player.pk)
        current_active = (
            TeamMembership.objects.select_for_update().filter(player=locked_player, status=TeamMembership.STATUS_ACTIVE).first()
        )
        if current_active:
            raise InvalidInput("Player already has an active team membership.")
        if Team.objects.select_for_update().filter(name__iexact=normalized_name).exists():
            raise InvalidInput("A team with this name already exists.")

        try:
            with transaction.atomic():
                team = Team.objects.create(name=normalized_name, division=division)
        except IntegrityError as exc:
            raise InvalidInput("A team with this name already exists.") from exc
        membership = request_membership_change(actor, locked_player, team, "join")
        _get_or_create_standing(team)
        recalculate_ladder_positions(division)
        return team, membership


def cancel_join_request(actor, player):
    if not getattr(actor, "is_authenticated", False):
        raise AuthorizationFailure("Authentication is required.")
    if not actor.is_staff and getattr(player, "user_id", None) != actor.id:
        raise AuthorizationFailure("Players may only cancel their own join requests.")

    with transaction.atomic():
        locked_player = PlayerProfile.objects.select_for_update().get(pk=player.pk)
        pending_request = (
            TeamMembership.objects.select_for_update()
            .filter(player=locked_player, status=TeamMembership.STATUS_JOIN_REQUESTED)
            .order_by("created_at", "id")
            .first()
        )
        if not pending_request:
            latest_membership = (
                TeamMembership.objects.select_for_update().filter(player=locked_player).order_by("-created_at", "-id").first()
            )
            if (
                latest_membership
                and latest_membership.status == TeamMembership.STATUS_INACTIVE
                and latest_membership.workflow_events.filter(event_type=WorkflowEvent.EventType.JOIN_REQUEST_CANCELLED).exists()
            ):
                return latest_membership
            raise InvalidInput("Player does not have a pending team join request.")

        now = timezone.now()
        pending_request.status = TeamMembership.STATUS_INACTIVE
        pending_request.effective_to = now
        pending_request.resolved_at = now
        pending_request.resolution_note = "Cancelled by player."
        pending_request.save(update_fields=["status", "effective_to", "resolved_at", "resolution_note", "updated_at"])
        record_workflow_event(
            event_type=WorkflowEvent.EventType.JOIN_REQUEST_CANCELLED,
            dedupe_key=f"join_request_cancelled:{pending_request.id}",
            actor=actor,
            membership=pending_request,
            recipient_user_ids={locked_player.user_id} | active_staff_user_ids(),
            previous_state=TeamMembership.STATUS_JOIN_REQUESTED,
            new_state=TeamMembership.STATUS_INACTIVE,
            metadata={
                "player_id": locked_player.id,
                "team_id": pending_request.team_id,
            },
        )
        return pending_request


def resolve_membership_request(admin_actor, membership, decision):
    if not getattr(admin_actor, "is_staff", False):
        raise AuthorizationFailure("Only administrators may resolve membership requests.")
    if decision not in {"approve", "reject"}:
        raise InvalidInput("Decision must be approve or reject.")

    with transaction.atomic():
        reference = TeamMembership.objects.get(pk=membership.pk)
        profiles = _lock_player_profiles([reference.player_id])
        teams = _lock_teams([reference.team_id])
        locked_membership = TeamMembership.objects.select_for_update(of=("self",)).get(pk=membership.pk)
        if (locked_membership.player_id, locked_membership.team_id) != (reference.player_id, reference.team_id):
            raise StaleState("Membership changed while resolving its request.")
        locked_membership.player = profiles[reference.player_id]
        locked_membership.team = teams[reference.team_id]
        resolved_event_type = (
            WorkflowEvent.EventType.JOIN_REQUEST_APPROVED if decision == "approve" else WorkflowEvent.EventType.JOIN_REQUEST_REJECTED
        )
        if locked_membership.workflow_events.filter(event_type=resolved_event_type).exists():
            return locked_membership
        is_join_request = locked_membership.status == TeamMembership.STATUS_JOIN_REQUESTED
        now = timezone.now()
        locked_membership.reviewed_by = admin_actor
        locked_membership.resolved_at = now

        if locked_membership.status == TeamMembership.STATUS_JOIN_REQUESTED:
            if decision == "approve":
                locked_player = profiles[locked_membership.player_id]
                locked_team = teams[locked_membership.team_id]
                current_active = (
                    TeamMembership.objects.select_for_update().filter(player=locked_player, status=TeamMembership.STATUS_ACTIVE).first()
                )
                if current_active:
                    raise InvalidInput("Player already has an active team membership.")
                _validate_player_division(locked_player, locked_team)
                active_count = (
                    TeamMembership.objects.select_for_update().filter(team=locked_team, status=TeamMembership.STATUS_ACTIVE).count()
                )
                legacy_count = locked_team.players.exclude(pk=locked_player.pk).count()
                if locked_team.status != Team.STATUS_ACTIVE:
                    raise InvalidInput("Cannot join a retired team.")
                if max(active_count, legacy_count) >= 3:
                    raise InvalidInput("Team already has three active members.")
                locked_membership.status = TeamMembership.STATUS_ACTIVE
                locked_membership.effective_from = now
                locked_player.team = locked_team
                locked_player.save(update_fields=["team"])
            else:
                locked_membership.status = TeamMembership.STATUS_INACTIVE
                locked_membership.effective_to = now
        elif locked_membership.removal_requested_at:
            if decision == "approve":
                locked_membership.status = TeamMembership.STATUS_INACTIVE
                locked_membership.effective_to = now
                locked_membership.player.team = None
                locked_membership.player.save(update_fields=["team"])
            else:
                locked_membership.removal_requested_at = None
        else:
            raise InvalidInput("Membership does not have a pending request.")
        locked_membership.save(
            update_fields=[
                "status",
                "effective_from",
                "effective_to",
                "removal_requested_at",
                "reviewed_by",
                "resolved_at",
                "updated_at",
            ]
        )
        if is_join_request:
            event_type = (
                WorkflowEvent.EventType.JOIN_REQUEST_APPROVED if decision == "approve" else WorkflowEvent.EventType.JOIN_REQUEST_REJECTED
            )
            record_workflow_event(
                event_type=event_type,
                dedupe_key=f"{event_type}:{locked_membership.id}",
                actor=admin_actor,
                membership=locked_membership,
                recipient_user_ids={locked_membership.player.user_id, admin_actor.id},
                previous_state=TeamMembership.STATUS_JOIN_REQUESTED,
                new_state=locked_membership.status,
                metadata={
                    "player_id": locked_membership.player_id,
                    "team_id": locked_membership.team_id,
                    "team_name": locked_membership.team.name,
                },
            )
        return locked_membership


def cancel_match(actor, match):
    if not getattr(actor, "is_authenticated", False):
        raise AuthorizationFailure("Authentication is required.")
    with transaction.atomic():
        participant_ids = list(MatchParticipant.objects.filter(match_id=match.pk).values_list("player_id", flat=True))
        _lock_player_profiles(participant_ids)
        locked_match = Match.objects.select_for_update(of=("self",)).get(pk=match.pk)
        participants = list(
            MatchParticipant.objects.select_for_update(of=("self",))
            .filter(match=locked_match)
            .select_related("player")
            .order_by("player_id", "id")
        )
        if set(participant_ids) != {participant.player_id for participant in participants}:
            raise StaleState("Match participants changed while cancelling.")
        participant_user_ids = {participant.player.user_id for participant in participants}
        if not actor.is_staff and actor.id not in participant_user_ids:
            raise AuthorizationFailure("Only selected match participants or administrators may cancel matches.")
        if locked_match.status == Match.STATUS_COMPLETED:
            raise StaleState("Completed matches cannot be cancelled.")
        if locked_match.status == Match.STATUS_CANCELLED:
            return locked_match
        if MatchResultSubmission.objects.select_for_update().filter(match=locked_match).exists():
            raise StaleState("Matches cannot be cancelled after a score has been submitted.")

        source_ids = list(locked_match.reservations.filter(status=MatchReservation.STATUS_ACTIVE).values_list("availability_id", flat=True))
        availability_by_id = {
            availability.pk: availability
            for availability in AvailabilitySlot.objects.select_for_update(of=("self",))
            .filter(
                Q(player_id__in=participant_ids, status=AvailabilitySlot.STATUS_ACTIVE)
                | Q(pk__in=[pk for pk in source_ids if pk is not None])
            )
            .order_by("pk")
        }

        reservations = list(
            locked_match.reservations.select_for_update(of=("self",)).filter(status=MatchReservation.STATUS_ACTIVE).order_by("pk")
        )
        restored_availability_ids = []
        superseded_availability_ids = []
        for reservation in reservations:
            reservation.status = MatchReservation.STATUS_RELEASED
            reservation.save(update_fields=["status"])
            availability = availability_by_id.get(reservation.availability_id)
            if availability and availability.status == AvailabilitySlot.STATUS_CONSUMED:
                has_other_active = (
                    availability.match_reservations.exclude(pk=reservation.pk).filter(status=MatchReservation.STATUS_ACTIVE).exists()
                )
                if not has_other_active:
                    has_overlapping_active = (
                        availability.starts_at
                        and availability.ends_at
                        and AvailabilitySlot.objects.filter(
                            player_id=availability.player_id,
                            status=AvailabilitySlot.STATUS_ACTIVE,
                            starts_at__lt=availability.ends_at,
                            ends_at__gt=availability.starts_at,
                        )
                        .exclude(pk=availability.pk)
                        .exists()
                    )
                    availability.status = AvailabilitySlot.STATUS_CANCELLED if has_overlapping_active else AvailabilitySlot.STATUS_ACTIVE
                    availability.save(update_fields=["status"])
                    if has_overlapping_active:
                        superseded_availability_ids.append(availability.id)
                    else:
                        restored_availability_ids.append(availability.id)

        locked_match.status = Match.STATUS_CANCELLED
        locked_match.save(update_fields=["status", "updated_at"])
        record_workflow_event(
            event_type=WorkflowEvent.EventType.MATCH_CANCELLED,
            dedupe_key=f"match_cancelled:{locked_match.id}",
            actor=actor,
            match=locked_match,
            recipient_user_ids=participant_user_ids,
            previous_state=Match.STATUS_SCHEDULED,
            new_state=Match.STATUS_CANCELLED,
            metadata={
                "team_a_id": locked_match.team_a_id,
                "team_b_id": locked_match.team_b_id,
                "restored_availability_ids": restored_availability_ids,
                "superseded_availability_ids": superseded_availability_ids,
            },
        )
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
            MatchResultSubmission.objects.select_for_update(of=("self",))
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
        audit = ScoreCorrectionAudit.objects.create(
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
        record_workflow_event(
            event_type=WorkflowEvent.EventType.SCORE_CONFLICT_RESOLVED,
            dedupe_key=f"score_conflict_resolved:{audit.id}",
            actor=admin_actor,
            match=locked_notification.match,
            submission=locked_submission,
            score_correction_audit=audit,
            recipient_user_ids=match_participant_user_ids(locked_notification.match) | {admin_actor.id},
            previous_state="conflict",
            new_state=Match.STATUS_COMPLETED,
            metadata={
                "official_submission_id": locked_submission.id,
                "winning_team_id": winner.id,
                "losing_team_id": loser.id,
            },
        )
        return audit


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
        player = PlayerProfile.objects.select_for_update().get(pk=player.pk)
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
        _lock_player_profiles([player.pk])
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


def _matchmaking_snapshot(team, interval):
    teams = {item.id: item for item in Team.active.filter(division=team.division).select_related("standing").order_by("id")}
    members = defaultdict(list)
    for membership in (
        TeamMembership.objects.filter(team_id__in=teams, status=TeamMembership.STATUS_ACTIVE)
        .select_related("player__user")
        .order_by("player_id")
    ):
        members[membership.team_id].append(membership.player)
    player_ids = {player.id for players in members.values() for player in players}
    slots = defaultdict(list)
    # Candidates preserve full source bounds, rather than clipping to search.
    # Load conflicts across their envelope, including portions outside search.
    reservation_start, reservation_end = interval
    for slot in AvailabilitySlot.objects.filter(
        player_id__in=player_ids, status=AvailabilitySlot.STATUS_ACTIVE, starts_at__lt=interval[1], ends_at__gt=interval[0]
    ).order_by("starts_at", "ends_at", "id"):
        slots[slot.player_id].append(slot)
        reservation_start = min(reservation_start, slot.starts_at)
        reservation_end = max(reservation_end, slot.ends_at)
    reservations = defaultdict(list)
    for reservation in MatchReservation.objects.filter(
        player_id__in=player_ids, status=MatchReservation.STATUS_ACTIVE, starts_at__lt=reservation_end, ends_at__gt=reservation_start
    ).order_by("starts_at", "ends_at"):
        reservations[reservation.player_id].append((reservation.starts_at, reservation.ends_at))
    reservation_index = {}
    for player_id, windows in reservations.items():
        starts, maximum_ends = [], []
        for start, end in windows:
            starts.append(start)
            maximum_ends.append(max(end, maximum_ends[-1]) if maximum_ends else end)
        reservation_index[player_id] = (starts, maximum_ends)
    lineups = []
    for team_id, players in members.items():
        for one, two in combinations(players, 2):
            for start, end, slot_one, slot_two in _intersect_sorted_slots(slots[one.id], slots[two.id]):
                lineups.append(
                    {
                        "team": teams[team_id],
                        "players": (one, two),
                        "starts_at": start,
                        "ends_at": end,
                        "availability": {one.id: slot_one, two.id: slot_two},
                    }
                )
    return teams, lineups, reservation_index


def _snapshot_conflict(reservations, player_id, start, end):
    starts, maximum_ends = reservations.get(player_id, ([], []))
    index = bisect_left(starts, end) - 1
    return index >= 0 and maximum_ends[index] > start


def candidate_identity(option):
    """Exact identity including source windows, not a position in a ranked list."""
    return {
        "teams": [option["team_a"].id, option["team_b"].id],
        "players": [[player.id for player in option[key]] for key in ("team_a_players", "team_b_players")],
        "start": option["starts_at"].isoformat(),
        "end": option["ends_at"].isoformat(),
        "slots": [
            [player_id, slot.id, slot.starts_at.isoformat(), slot.ends_at.isoformat()]
            for player_id, slot in sorted(option["availability"].items())
        ],
    }


def sign_candidate(option, actor):
    return signing.dumps(
        {"actor": actor.pk, "candidate": candidate_identity(option)}, salt="ladder.matchmaking.candidate.v1", compress=True
    )


def create_match_suggestion_from_candidate(token, actor, team):
    if not getattr(actor, "is_authenticated", False):
        raise AuthorizationFailure("Authentication is required.")
    try:
        payload = signing.loads(token, salt="ladder.matchmaking.candidate.v1", max_age=1800)
        identity = payload["candidate"]
        if payload["actor"] != actor.pk or identity["teams"][0] != team.pk:
            raise ValueError
        interval = (datetime.fromisoformat(identity["start"]), datetime.fromisoformat(identity["end"]))
        if interval[0] <= timezone.now():
            raise ValueError
    except signing.BadSignature, KeyError, TypeError, ValueError, IndexError:
        raise InvalidInput("Suggestion option is no longer available. Refresh and choose again.") from None
    with transaction.atomic():
        actor_profile = _profile_for_user(actor)
        _lock_player_profiles([player_id for side in identity["players"] for player_id in side] + [actor_profile.pk])
        _lock_teams(identity["teams"])
        if not TeamMembership.objects.filter(team=team, player_id=actor_profile.pk, status=TeamMembership.STATUS_ACTIVE).exists():
            raise AuthorizationFailure("Only active team members may request this match.")
        for option in find_opponent_suggestions(team, interval):
            if candidate_identity(option) == identity:
                return create_match_suggestion(option, actor=actor)
        raise InvalidInput("Suggestion option is no longer available. Refresh and choose again.")


def find_opponent_suggestions(team, interval):
    if interval[0] >= interval[1]:
        raise InvalidInput("Search interval start must be before end.")
    teams, lineups, reservations = _matchmaking_snapshot(team, interval)
    if team.pk not in teams:
        return []
    team = teams[team.pk]
    options = []
    # Temporal sweep buckets contain only overlapping lineups on the opposite side.
    events = []
    for index, lineup in enumerate(lineups):
        events.extend(((lineup["starts_at"], 1, index), (lineup["ends_at"], 0, index)))
    active = {True: {}, False: {}}
    seen = set()
    for _, beginning, index in sorted(events):
        lineup = lineups[index]
        own_side = lineup["team"].pk == team.pk
        if not beginning:
            active[own_side].pop(index, None)
            continue
        for opposite in active[not own_side].values():
            own, other = (lineup, opposite) if own_side else (opposite, lineup)
            opponent = other["team"]
            starts_at = max(own["starts_at"], other["starts_at"])
            ends_at = min(own["ends_at"], other["ends_at"])
            if starts_at >= ends_at:
                continue
            player_ids = [player.id for player in own["players"] + other["players"]]
            if len(set(player_ids)) != 4:
                continue
            if any(_snapshot_conflict(reservations, player_id, starts_at, ends_at) for player_id in player_ids):
                continue
            option = {
                "team_a": team,
                "team_b": opponent,
                "team_a_players": own["players"],
                "team_b_players": other["players"],
                "starts_at": starts_at,
                "ends_at": ends_at,
                "availability": {**own["availability"], **other["availability"]},
            }
            identity = str(candidate_identity(option))
            if identity not in seen:
                seen.add(identity)
                options.append(option)
        active[own_side][index] = lineup
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
            item["ends_at"],
            [slot.id for _, slot in sorted(item["availability"].items())],
        ),
    )


def _suggestion_participant_signature(suggestion):
    signature = defaultdict(list)
    for participant in sorted(suggestion.participants.all(), key=lambda item: (item.side, item.lineup_order, item.player_id)):
        signature[participant.side].append(participant.player_id)
    return {side: tuple(player_ids) for side, player_ids in signature.items()}


def _lock_option_parents(option, actor):
    players_a = tuple(sorted(player.pk for player in option["team_a_players"]))
    players_b = tuple(sorted(player.pk for player in option["team_b_players"]))
    actor_profile = _profile_for_user(actor) if actor is not None else None
    profiles = _lock_player_profiles(players_a + players_b + ((actor_profile.pk,) if actor_profile else ()))
    teams = _lock_teams([option["team_a"].pk, option["team_b"].pk])
    if len(profiles) != len(set(players_a + players_b + ((actor_profile.pk,) if actor_profile else ()))) or len(teams) != 2:
        raise InvalidInput("Suggestion requires two different existing teams and four existing players.")
    if (
        actor_profile
        and not TeamMembership.objects.filter(
            player_id=actor_profile.pk, team_id=option["team_a"].pk, status=TeamMembership.STATUS_ACTIVE
        ).exists()
    ):
        raise AuthorizationFailure("Only active members may request a match for their team.")
    return {
        **option,
        "team_a": teams[option["team_a"].pk],
        "team_b": teams[option["team_b"].pk],
        "team_a_players": tuple(profiles[pk] for pk in players_a),
        "team_b_players": tuple(profiles[pk] for pk in players_b),
    }


def _validate_option_sources_locked(option):
    player_ids = [player.pk for player in option["team_a_players"] + option["team_b_players"]]
    if not isinstance(option.get("availability"), dict) or any(
        not isinstance(slot, AvailabilitySlot) for slot in option["availability"].values()
    ):
        raise InvalidInput("Suggestion requires one valid source window for each selected player.")
    if option["starts_at"] >= option["ends_at"] or set(option["availability"]) != set(player_ids):
        raise InvalidInput("Suggestion requires one valid source window for each selected player.")
    sources = {
        slot.pk: slot
        for slot in AvailabilitySlot.objects.select_for_update(of=("self",))
        .filter(pk__in=[slot.pk for slot in option["availability"].values()])
        .order_by("pk")
    }
    for player_id, original in option["availability"].items():
        slot = sources.get(original.pk)
        if (
            slot is None
            or slot.player_id != player_id
            or slot.status != AvailabilitySlot.STATUS_ACTIVE
            or slot.starts_at is None
            or slot.ends_at is None
            or (slot.starts_at, slot.ends_at) != (original.starts_at, original.ends_at)
            or not (slot.starts_at <= option["starts_at"] < option["ends_at"] <= slot.ends_at)
        ):
            raise BookingCollision("Required availability is no longer active or has changed.")
    reservations = list(
        MatchReservation.objects.select_for_update(of=("self",))
        .filter(
            player_id__in=player_ids,
            status=MatchReservation.STATUS_ACTIVE,
            starts_at__lt=option["ends_at"],
            ends_at__gt=option["starts_at"],
        )
        .order_by("pk")
    )
    if reservations:
        raise BookingCollision("One or more players already has a reservation for this window.")


def create_match_suggestion(option, expires_at=None, actor=None):
    expires_at = expires_at or option["starts_at"]
    with transaction.atomic():
        option = _lock_option_parents(option, actor)
        option_signature = {
            option["team_a"].pk: tuple(player.pk for player in option["team_a_players"]),
            option["team_b"].pk: tuple(player.pk for player in option["team_b_players"]),
        }
        existing_suggestions = list(
            MatchSuggestion.objects.select_for_update(of=("self",))
            .filter(
                Q(team_a=option["team_a"], team_b=option["team_b"]) | Q(team_a=option["team_b"], team_b=option["team_a"]),
                starts_at=option["starts_at"],
                ends_at=option["ends_at"],
                status__in=[MatchSuggestion.STATUS_PROPOSED, MatchSuggestion.STATUS_PARTIALLY_ACCEPTED],
            )
            .prefetch_related("participants")
            .order_by("id")
        )
        _validate_booking_roster(
            option["team_a"], option["team_b"], list(option_signature[option["team_a"].pk]), list(option_signature[option["team_b"].pk])
        )
        _validate_option_sources_locked(option)
        for existing in existing_suggestions:
            signature = _suggestion_participant_signature(existing)
            if {
                existing.team_a_id: tuple(sorted(signature.get(SuggestionParticipant.SIDE_A, ()))),
                existing.team_b_id: tuple(sorted(signature.get(SuggestionParticipant.SIDE_B, ()))),
            } == option_signature:
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
        recipient_user_ids = suggestion_participant_user_ids(suggestion)
        event, created = record_workflow_event(
            event_type=WorkflowEvent.EventType.MATCH_REQUEST_CREATED,
            dedupe_key=f"match_request_created:{suggestion.id}",
            actor=actor,
            suggestion=suggestion,
            recipient_user_ids=recipient_user_ids,
            previous_state="",
            new_state=MatchSuggestion.STATUS_PROPOSED,
            metadata={
                "team_a_id": suggestion.team_a_id,
                "team_b_id": suggestion.team_b_id,
                "scheduled_starts_at": suggestion.starts_at.isoformat(),
                "scheduled_ends_at": suggestion.ends_at.isoformat(),
                "player_ids": sorted(participant.player_id for participant in rows),
            },
        )
        if created:
            queue_email_notifications(event, recipient_user_ids, EmailNotificationDelivery.TYPE_MATCH_REQUEST)
        return suggestion


def expire_open_suggestions(now=None):
    """Expire overdue open suggestions under row locks and return the count."""
    now = now or timezone.now()
    open_statuses = [MatchSuggestion.STATUS_PROPOSED, MatchSuggestion.STATUS_PARTIALLY_ACCEPTED]
    with transaction.atomic():
        locked_ids = list(
            MatchSuggestion.objects.select_for_update(of=("self",))
            .filter(status__in=open_statuses, expires_at__lte=now)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        if not locked_ids:
            return 0
        return MatchSuggestion.objects.filter(pk__in=locked_ids).update(
            status=MatchSuggestion.STATUS_EXPIRED,
            updated_at=now,
        )


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
    expected_teams = {SuggestionParticipant.SIDE_A: suggestion.team_a_id, SuggestionParticipant.SIDE_B: suggestion.team_b_id}
    if len(participants) != 4 or any(participant.team_id != expected_teams.get(participant.side) for participant in participants):
        raise InvalidInput("Each suggestion side must name its exact suggested team.")
    return by_side


def accept_suggestion(actor, suggestion):
    result = _accept_suggestion_transaction(actor, suggestion)
    # Expiry is a durable lifecycle transition, not a failed booking write.
    # Raise only after its transaction has committed; other errors still roll back.
    if isinstance(result, StaleState):
        raise result
    return result


def _accept_suggestion_transaction(actor, suggestion):
    actor_profile = _profile_for_user(actor)
    with transaction.atomic():
        reference = MatchSuggestion.objects.get(pk=suggestion.pk)
        player_ids = list(SuggestionParticipant.objects.filter(suggestion_id=reference.pk).values_list("player_id", flat=True))
        _lock_player_profiles(player_ids + [actor_profile.pk])
        teams = _lock_teams([reference.team_a_id, reference.team_b_id])
        locked = MatchSuggestion.objects.select_for_update(of=("self",)).get(pk=suggestion.pk)
        if (locked.team_a_id, locked.team_b_id) != (reference.team_a_id, reference.team_b_id):
            raise StaleState("Suggestion teams changed while accepting.")
        locked.team_a, locked.team_b = teams[locked.team_a_id], teams[locked.team_b_id]
        actor_team = _suggestion_side_for_player(locked, actor_profile)
        if actor_team is None or actor_team not in {locked.team_a, locked.team_b}:
            raise AuthorizationFailure("Only selected lineup players may accept this suggestion.")
        if locked.status == MatchSuggestion.STATUS_CONFIRMED:
            existing_match = Match.objects.filter(source_suggestion=locked).first()
            if existing_match is None:
                raise StaleState("Confirmed suggestion has no recorded match.")
            return existing_match
        if locked.status in {MatchSuggestion.STATUS_EXPIRED, MatchSuggestion.STATUS_CANCELLED, MatchSuggestion.STATUS_DECLINED}:
            raise StaleState("Suggestion is no longer acceptible.")
        if locked.expires_at <= timezone.now():
            locked.status = MatchSuggestion.STATUS_EXPIRED
            locked.save(update_fields=["status", "updated_at"])
            return StaleState("Suggestion has expired.")

        by_side = _players_for_suggestion(locked)
        if set(player_ids) != {participant.player_id for side in by_side.values() for participant in side}:
            raise StaleState("Suggestion participants changed while accepting.")
        _validate_booking_roster(
            locked.team_a,
            locked.team_b,
            [p.player_id for p in by_side[SuggestionParticipant.SIDE_A]],
            [p.player_id for p in by_side[SuggestionParticipant.SIDE_B]],
        )

        acceptance, _ = SuggestionAcceptance.objects.get_or_create(
            suggestion=locked,
            team=actor_team,
            defaults={"accepted_by": actor},
        )

        acceptances = list(locked.acceptances.select_for_update(of=("self",)).order_by("pk"))
        accepted_team_ids = {row.team_id for row in acceptances}
        required_team_ids = {locked.team_a_id, locked.team_b_id}
        if accepted_team_ids != required_team_ids:
            locked.status = MatchSuggestion.STATUS_PARTIALLY_ACCEPTED
            locked.save(update_fields=["status", "updated_at"])
            return locked
        return _confirm_suggestion_locked(locked, actor)


def _confirm_suggestion_locked(suggestion, actor):
    if hasattr(suggestion, "confirmed_match"):
        return suggestion.confirmed_match

    by_side = _players_for_suggestion(suggestion)
    participants = by_side[SuggestionParticipant.SIDE_A] + by_side[SuggestionParticipant.SIDE_B]
    player_ids = [participant.player_id for participant in participants]

    _validate_booking_roster(
        suggestion.team_a,
        suggestion.team_b,
        [p.player_id for p in by_side[SuggestionParticipant.SIDE_A]],
        [p.player_id for p in by_side[SuggestionParticipant.SIDE_B]],
    )
    sources = list(
        AvailabilitySlot.objects.select_for_update(of=("self",))
        .filter(
            player_id__in=player_ids,
            status=AvailabilitySlot.STATUS_ACTIVE,
            starts_at__lte=suggestion.starts_at,
            ends_at__gte=suggestion.ends_at,
        )
        .order_by("pk")
    )
    availability_by_player = {}
    for source in sorted(sources, key=lambda slot: (slot.starts_at, slot.ends_at, slot.pk)):
        availability_by_player.setdefault(source.player_id, source)
    conflicts = list(
        MatchReservation.objects.select_for_update(of=("self",))
        .filter(
            player_id__in=player_ids,
            status=MatchReservation.STATUS_ACTIVE,
            starts_at__lt=suggestion.ends_at,
            ends_at__gt=suggestion.starts_at,
        )
        .order_by("pk")
    )
    if conflicts:
        raise BookingCollision("One or more players already has a reservation for this window.")
    if set(availability_by_player) != set(player_ids):
        raise BookingCollision("Required availability is no longer active.")

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
    record_workflow_event(
        event_type=WorkflowEvent.EventType.MATCH_CONFIRMED,
        dedupe_key=f"match_confirmed:{match.id}",
        actor=actor,
        match=match,
        recipient_user_ids=match_participant_user_ids(match),
        previous_state="",
        new_state=Match.STATUS_SCHEDULED,
        metadata={
            "source_suggestion_id": suggestion.id,
            "team_a_id": match.team_a_id,
            "team_b_id": match.team_b_id,
            "scheduled_starts_at": suggestion.starts_at.isoformat(),
            "scheduled_ends_at": suggestion.ends_at.isoformat(),
            "player_ids": sorted(player_ids),
        },
    )
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
    if team_a_points > 99 or team_b_points > 99:
        raise InvalidInput("Tie-break scores cannot exceed 99 points.")
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
        locked_match = Match.objects.select_for_update(of=("self",)).select_related("team_a", "team_b").get(pk=match.pk)
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
        record_workflow_event(
            event_type=WorkflowEvent.EventType.SCORE_SUBMITTED,
            dedupe_key=f"score_submitted:{submission.id}",
            actor=actor,
            match=locked_match,
            submission=submission,
            recipient_user_ids=match_participant_user_ids(locked_match),
            previous_state="not_submitted",
            new_state="submitted",
            metadata={
                "submitting_team_id": submitting_team.id,
                "score_signature": [
                    {
                        "set_order": item["set_order"],
                        "set_type": item["set_type"],
                        "team_a_score": item["team_a_score"],
                        "team_b_score": item["team_b_score"],
                    }
                    for item in score["sets"]
                ],
            },
        )
        if get_match_status(locked_match) == "confirmed":
            finalize_match_result(locked_match)
        elif get_match_status(locked_match) == "conflict":
            create_admin_notification_for_conflict(locked_match, actor=actor)
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


def create_admin_notification_for_conflict(match, actor=None):
    with transaction.atomic():
        locked_match = Match.objects.select_for_update().get(pk=match.pk)
        if get_match_status(locked_match) != "conflict":
            return None
        notification, created = AdminNotification.objects.get_or_create(
            match=locked_match,
            notification_type=AdminNotification.TYPE_SCORE_CONFLICT,
            is_resolved=False,
            defaults={"message": "Result submissions do not match. Admin review is required."},
        )
        if created:
            event, event_created = record_workflow_event(
                event_type=WorkflowEvent.EventType.SCORE_CONFLICT_CREATED,
                dedupe_key=f"score_conflict_created:{notification.id}",
                actor=actor,
                match=locked_match,
                recipient_user_ids=match_participant_user_ids(locked_match) | active_staff_user_ids(),
                previous_state="waiting_for_submissions",
                new_state="conflict",
                metadata={"admin_notification_id": notification.id},
            )
            if event_created:
                queue_email_notifications(event, active_staff_user_ids(), EmailNotificationDelivery.TYPE_SCORE_CONFLICT)
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
    affected_teams = [winner, loser, match.team_a]
    if previous_result:
        affected_teams.extend([previous_result.winning_team, previous_result.losing_team])
    _lock_standings_divisions([team.division for team in affected_teams])
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
        locked_match = Match.objects.select_for_update(of=("self",)).select_related("team_a", "team_b").get(pk=match.pk)
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
