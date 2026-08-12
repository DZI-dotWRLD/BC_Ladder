from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import AvailabilityForm, PlayerRegistrationForm, ProfileSetupForm, ScoreSubmissionForm, TeamCreateForm, TeamJoinForm
from .models import (
    AvailabilitySlot,
    LadderStanding,
    Match,
    MatchSuggestion,
    PlayerProfile,
    SuggestionParticipant,
    Team,
    TeamMembership,
)
from .services import (
    DomainError,
    accept_suggestion,
    cancel_availability,
    cancel_join_request,
    create_match_suggestion,
    create_team_for_player,
    find_opponent_suggestions,
    generate_team_lineups,
    get_match_status,
    request_membership_change,
    save_availability,
    submit_match_result,
)


def _profile_for_request(request):
    profile = PlayerProfile.objects.select_related("user", "team").filter(user=request.user).first()
    if profile is None:
        raise PlayerProfile.DoesNotExist
    return profile


def _profile_or_setup(request):
    try:
        return _profile_for_request(request)
    except PlayerProfile.DoesNotExist:
        messages.info(request, "Complete your player profile before using player features.")
        return None


def _active_membership(profile):
    return TeamMembership.objects.filter(player=profile, status=TeamMembership.STATUS_ACTIVE).select_related("team").first()


def _active_team(profile):
    membership = _active_membership(profile)
    return membership.team if membership else profile.team


def _message_domain_error(request, error):
    messages.error(request, str(error))


def _player_match_queryset(profile):
    team = _active_team(profile)
    if not team:
        return Match.objects.none()
    return (
        Match.objects.filter(Q(team_a=team) | Q(team_b=team))
        .select_related("team_a", "team_b")
        .prefetch_related("participants__player__user", "result_submissions__sets")
        .order_by("-scheduled_starts_at", "-scheduled_week_start_date", "-scheduled_start_time")
    )


def _setup_steps_for_profile(profile, team, active_availability_count, suggestion_count, match_count):
    pending_join_request = (
        None
        if team
        else TeamMembership.objects.filter(player=profile, status=TeamMembership.STATUS_JOIN_REQUESTED)
        .select_related("team")
        .order_by("created_at", "id")
        .first()
    )
    team_label = "Team selected"
    team_detail_text = team.name if team else "Create a team or request to join one."
    if pending_join_request:
        team_label = "Team request pending"
        team_detail_text = f"Waiting for admin approval to join {pending_join_request.team.name}."

    return [
        {
            "label": "Player profile",
            "detail": "Profile complete.",
            "complete": True,
            "url_name": "ladder:profile_setup",
        },
        {
            "label": team_label,
            "detail": team_detail_text,
            "complete": bool(team),
            "url_name": "ladder:team",
        },
        {
            "label": "Availability",
            "detail": "Add at least one active window."
            if not active_availability_count
            else f"{active_availability_count} active window(s).",
            "complete": active_availability_count > 0,
            "url_name": "ladder:availability",
        },
        {
            "label": "Suggestions",
            "detail": "Review compatible opponents once your team has shared availability."
            if not suggestion_count
            else f"{suggestion_count} suggestion(s) available.",
            "complete": suggestion_count > 0,
            "url_name": "ladder:suggestions",
        },
        {
            "label": "Matches",
            "detail": "Confirmed matches will appear here." if not match_count else f"{match_count} match(es) scheduled or completed.",
            "complete": match_count > 0,
            "url_name": "ladder:matches",
        },
    ]


def _active_member_count(team):
    membership_count = TeamMembership.objects.filter(team=team, status=TeamMembership.STATUS_ACTIVE).count()
    legacy_count = team.players.count()
    return max(membership_count, legacy_count)


def _suggestion_empty_state(profile, team, interval, options):
    if not team:
        return {
            "title": "Join or create a team first",
            "detail": "Suggestions require an active team in your ladder.",
            "url_name": "ladder:team",
            "action": "Go to team",
        }

    if _active_member_count(team) < 2:
        return {
            "title": "Your team needs two active members",
            "detail": "A doubles match needs exactly two active players from your team.",
            "url_name": "ladder:team",
            "action": "Manage team",
        }

    if not AvailabilitySlot.objects.filter(
        player__team_memberships__team=team,
        player__team_memberships__status=TeamMembership.STATUS_ACTIVE,
        status=AvailabilitySlot.STATUS_ACTIVE,
        starts_at__lt=interval[1],
        ends_at__gt=interval[0],
    ).exists():
        return {
            "title": "Add active availability",
            "detail": "At least two teammates need overlapping active availability before opponents can be suggested.",
            "url_name": "ladder:availability",
            "action": "Add availability",
        }

    if not generate_team_lineups(team, interval):
        return {
            "title": "No shared team availability",
            "detail": "Your team has availability, but no two active members currently overlap in the next 30 days.",
            "url_name": "ladder:availability",
            "action": "Adjust availability",
        }

    if not Team.active.filter(division=team.division).exclude(pk=team.pk).exists():
        return {
            "title": "No opponent teams in this ladder yet",
            "detail": "Suggestions require another active team in the same ladder.",
            "url_name": "ladder:ladder",
            "url_args": [team.division],
            "action": "View ladder",
        }

    if not options:
        return {
            "title": "No overlapping opponent availability",
            "detail": "Your team is ready, but no eligible opponent lineup has a compatible active window right now.",
            "url_name": "ladder:availability",
            "action": "Review availability",
        }
    return None


def _lineup_label(players):
    return " / ".join(player.user.username for player in players)


def _suggestion_cards(suggestions, profile, team):
    cards = []
    open_statuses = {MatchSuggestion.STATUS_PROPOSED, MatchSuggestion.STATUS_PARTIALLY_ACCEPTED}
    for suggestion in suggestions:
        participants = list(suggestion.participants.all())
        players_by_side = {
            SuggestionParticipant.SIDE_A: [],
            SuggestionParticipant.SIDE_B: [],
        }
        for participant in sorted(participants, key=lambda item: (item.side, item.lineup_order, item.player_id)):
            players_by_side[participant.side].append(participant.player)

        accepted_team_ids = {acceptance.team_id for acceptance in suggestion.acceptances.all()}
        own_team_accepted = team.id in accepted_team_ids
        required_team_ids = {suggestion.team_a_id, suggestion.team_b_id}
        is_selected_player = profile.id in {participant.player_id for participant in participants}
        is_open = suggestion.status in open_statuses
        can_accept = is_open and is_selected_player and not own_team_accepted

        if suggestion.status == MatchSuggestion.STATUS_CONFIRMED:
            state_note = "Match confirmed."
        elif suggestion.status in {MatchSuggestion.STATUS_CANCELLED, MatchSuggestion.STATUS_DECLINED, MatchSuggestion.STATUS_EXPIRED}:
            state_note = f"This suggestion is {suggestion.get_status_display().lower()}."
        elif accepted_team_ids == required_team_ids:
            state_note = "Both teams accepted; confirmation is in progress."
        elif own_team_accepted:
            state_note = "Your team accepted. Waiting for the opponent."
        elif accepted_team_ids:
            state_note = "Opponent accepted. Your selected lineup can accept."
        else:
            state_note = "Waiting for both teams to accept."

        cards.append(
            {
                "suggestion": suggestion,
                "team_a_lineup": _lineup_label(players_by_side[SuggestionParticipant.SIDE_A]),
                "team_b_lineup": _lineup_label(players_by_side[SuggestionParticipant.SIDE_B]),
                "can_accept": can_accept,
                "is_open": is_open,
                "state_note": state_note,
                "is_selected_player": is_selected_player,
            }
        )
    return cards


def register(request):
    if request.user.is_authenticated:
        return redirect("ladder:dashboard")
    form = PlayerRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        PlayerProfile.objects.create(user=user, gender=form.cleaned_data["gender"])
        login(request, user)
        messages.success(request, "Account created.")
        return redirect("ladder:dashboard")
    return render(request, "registration/register.html", {"form": form})


@login_required
def profile_setup(request):
    existing = PlayerProfile.objects.filter(user=request.user).first()
    if existing:
        return redirect("ladder:dashboard")
    form = ProfileSetupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        profile = form.save(commit=False)
        profile.user = request.user
        profile.save()
        messages.success(request, "Player profile created.")
        return redirect("ladder:dashboard")
    return render(request, "ladder/profile_setup.html", {"form": form})


@login_required
def dashboard(request):
    profile = _profile_or_setup(request)
    if profile is None:
        return redirect("ladder:profile_setup")
    team = _active_team(profile)
    active_availability = profile.availability_slots.filter(status=AvailabilitySlot.STATUS_ACTIVE)
    availability = active_availability.order_by("starts_at")[:5]
    suggestions_qs = MatchSuggestion.objects.none()
    matches_qs = _player_match_queryset(profile)
    matches = matches_qs[:5]
    if team:
        suggestions_qs = (
            MatchSuggestion.objects.filter(Q(team_a=team) | Q(team_b=team))
            .select_related("team_a", "team_b")
            .prefetch_related("participants__player__user", "acceptances")
            .order_by("starts_at")[:5]
        )
    setup_steps = _setup_steps_for_profile(
        profile,
        team,
        active_availability.count(),
        suggestions_qs.count(),
        matches_qs.count(),
    )
    return render(
        request,
        "ladder/dashboard.html",
        {
            "profile": profile,
            "team": team,
            "availability": availability,
            "suggestions": suggestions_qs,
            "matches": matches,
            "setup_steps": setup_steps,
        },
    )


@login_required
def team_detail(request):
    profile = _profile_or_setup(request)
    if profile is None:
        return redirect("ladder:profile_setup")
    membership = _active_membership(profile)
    team = membership.team if membership else profile.team
    members = []
    member_count = 0
    team_capacity = 3
    team_is_full = False
    pending_join_request = None
    if team:
        member_ids = TeamMembership.objects.filter(team=team, status=TeamMembership.STATUS_ACTIVE).values_list("player_id", flat=True)
        members = (
            PlayerProfile.objects.filter(Q(id__in=member_ids) | Q(team=team)).select_related("user").distinct().order_by("user__username")
        )
        member_count = len(members)
        team_is_full = member_count >= team_capacity
    else:
        pending_join_request = (
            TeamMembership.objects.filter(player=profile, status=TeamMembership.STATUS_JOIN_REQUESTED)
            .select_related("team")
            .order_by("created_at", "id")
            .first()
        )
    return render(
        request,
        "ladder/team.html",
        {
            "profile": profile,
            "team": team,
            "members": members,
            "member_count": member_count,
            "team_capacity": team_capacity,
            "team_is_full": team_is_full,
            "membership": membership,
            "pending_join_request": pending_join_request,
            "join_form": TeamJoinForm(profile=profile),
            "create_form": TeamCreateForm(),
        },
    )


@login_required
def create_team(request):
    if request.method != "POST":
        return redirect("ladder:team")
    profile = _profile_or_setup(request)
    if profile is None:
        return redirect("ladder:profile_setup")
    form = TeamCreateForm(request.POST)
    if form.is_valid():
        try:
            team, _membership = create_team_for_player(request.user, profile, form.cleaned_data["name"])
            messages.success(request, f"Team {team.name} created.")
        except DomainError as error:
            _message_domain_error(request, error)
    else:
        messages.error(request, "Enter a valid, unique team name.")
    return redirect("ladder:team")


@login_required
def join_team(request):
    if request.method != "POST":
        return redirect("ladder:team")
    profile = _profile_or_setup(request)
    if profile is None:
        return redirect("ladder:profile_setup")
    form = TeamJoinForm(request.POST, profile=profile)
    if form.is_valid():
        try:
            request_membership_change(request.user, profile, form.cleaned_data["team"], "request_join")
            messages.success(request, "Team join request sent to the administrators.")
        except DomainError as error:
            _message_domain_error(request, error)
    else:
        messages.error(request, "Choose a valid team.")
    return redirect("ladder:team")


@login_required
def cancel_join_request_view(request):
    if request.method != "POST":
        return redirect("ladder:team")
    profile = _profile_or_setup(request)
    if profile is None:
        return redirect("ladder:profile_setup")
    try:
        cancel_join_request(request.user, profile)
        messages.success(request, "Team join request cancelled.")
    except DomainError as error:
        _message_domain_error(request, error)
    return redirect("ladder:team")


@login_required
def request_team_removal(request):
    if request.method != "POST":
        return redirect("ladder:team")
    profile = _profile_or_setup(request)
    if profile is None:
        return redirect("ladder:profile_setup")
    try:
        request_membership_change(request.user, profile, action="request_removal")
        messages.success(request, "Removal request sent to the administrators.")
    except DomainError as error:
        _message_domain_error(request, error)
    return redirect("ladder:team")


@login_required
def availability(request):
    profile = _profile_or_setup(request)
    if profile is None:
        return redirect("ladder:profile_setup")
    form = AvailabilityForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            try:
                save_availability(request.user, form.cleaned_data["starts_at"], form.cleaned_data["ends_at"])
                messages.success(request, "Availability saved.")
                return redirect("ladder:availability")
            except DomainError as error:
                _message_domain_error(request, error)
        else:
            messages.error(request, "Enter a valid start and end.")
    slots = profile.availability_slots.filter(status=AvailabilitySlot.STATUS_ACTIVE).order_by("starts_at", "created_at")
    cancelled_count = profile.availability_slots.filter(status=AvailabilitySlot.STATUS_CANCELLED).count()
    return render(request, "ladder/availability.html", {"form": form, "slots": slots, "cancelled_count": cancelled_count})


@login_required
def cancel_availability_view(request, slot_id):
    if request.method != "POST":
        return redirect("ladder:availability")
    profile = _profile_or_setup(request)
    if profile is None:
        return redirect("ladder:profile_setup")
    slot = get_object_or_404(AvailabilitySlot, pk=slot_id)
    try:
        cancel_availability(request.user, slot)
        messages.success(request, "Availability cancelled.")
    except DomainError as error:
        _message_domain_error(request, error)
    return redirect("ladder:availability")


@login_required
def suggestions(request):
    profile = _profile_or_setup(request)
    if profile is None:
        return redirect("ladder:profile_setup")
    team = _active_team(profile)
    options = []
    existing = MatchSuggestion.objects.none()
    existing_cards = []
    empty_state = None
    starts_at = timezone.now()
    ends_at = starts_at + timedelta(days=30)
    interval = (starts_at, ends_at)
    if team:
        options = find_opponent_suggestions(team, interval)[:10]
        existing = (
            MatchSuggestion.objects.filter(Q(team_a=team) | Q(team_b=team))
            .select_related("team_a", "team_b")
            .prefetch_related("participants__player__user", "acceptances")
            .order_by("starts_at", "id")
        )
        existing_cards = _suggestion_cards(existing, profile, team)
    empty_state = _suggestion_empty_state(profile, team, interval, options)
    return render(
        request,
        "ladder/suggestions.html",
        {"team": team, "options": options, "existing_cards": existing_cards, "empty_state": empty_state},
    )


@login_required
def create_suggestion_view(request, option_index):
    if request.method != "POST":
        return redirect("ladder:suggestions")
    profile = _profile_or_setup(request)
    if profile is None:
        return redirect("ladder:profile_setup")
    team = _active_team(profile)
    if not team:
        messages.error(request, "Join a team before creating suggestions.")
        return redirect("ladder:suggestions")

    starts_at = timezone.now()
    ends_at = starts_at + timedelta(days=30)
    options = find_opponent_suggestions(team, (starts_at, ends_at))[:10]
    try:
        option = options[option_index]
    except IndexError:
        messages.error(request, "Suggestion option is no longer available.")
        return redirect("ladder:suggestions")

    try:
        create_match_suggestion(option)
        messages.success(request, "Suggestion created.")
    except DomainError as error:
        _message_domain_error(request, error)
    return redirect("ladder:suggestions")


@login_required
def accept_suggestion_view(request, suggestion_id):
    if request.method != "POST":
        return redirect("ladder:suggestions")
    suggestion = get_object_or_404(MatchSuggestion, pk=suggestion_id)
    try:
        result = accept_suggestion(request.user, suggestion, int(request.POST.get("version", "0")))
        if isinstance(result, Match):
            messages.success(request, "Match confirmed.")
            return redirect("ladder:match_detail", match_id=result.id)
        messages.success(request, "Acceptance recorded.")
    except DomainError as error:
        _message_domain_error(request, error)
    return redirect("ladder:suggestions")


@login_required
def match_history(request):
    profile = _profile_or_setup(request)
    if profile is None:
        return redirect("ladder:profile_setup")
    return render(request, "ladder/matches.html", {"matches": _player_match_queryset(profile)})


@login_required
def match_detail(request, match_id):
    profile = _profile_or_setup(request)
    if profile is None:
        return redirect("ladder:profile_setup")
    match = get_object_or_404(_player_match_queryset(profile), pk=match_id)
    return render(
        request,
        "ladder/match_detail.html",
        {"match": match, "status": get_match_status(match), "score_form": ScoreSubmissionForm()},
    )


@login_required
def submit_score(request, match_id):
    if request.method != "POST":
        return redirect("ladder:match_detail", match_id=match_id)
    profile = _profile_or_setup(request)
    if profile is None:
        return redirect("ladder:profile_setup")
    match = get_object_or_404(_player_match_queryset(profile), pk=match_id)
    form = ScoreSubmissionForm(request.POST)
    if form.is_valid():
        try:
            submit_match_result(request.user, match, form.normalized_sets())
            messages.success(request, "Score submitted.")
        except DomainError as error:
            _message_domain_error(request, error)
    else:
        messages.error(request, "Enter valid numeric scores.")
    return redirect("ladder:match_detail", match_id=match.id)


@login_required
def ladder(request, division):
    if division not in {Team.DIVISION_MENS, Team.DIVISION_WOMENS}:
        raise PermissionDenied("Unknown ladder.")
    standings = (
        LadderStanding.objects.filter(team__division=division)
        .select_related("team")
        .order_by("-points", "-wins", "losses", "team__name", "team_id")
    )
    return render(request, "ladder/ladder.html", {"division": division, "standings": standings})
