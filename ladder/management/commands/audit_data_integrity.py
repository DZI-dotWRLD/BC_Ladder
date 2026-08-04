from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, F, Q

from ladder.models import (
    AvailabilitySlot,
    ConfirmedMatchResult,
    LadderStanding,
    MatchReservation,
    PointLedger,
    Team,
    TeamMembership,
)
from ladder.services import WIN_POINTS


class Command(BaseCommand):
    help = "Audit ladder data for production-blocking integrity problems."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fail-level",
            choices=["error", "warning"],
            default="error",
            help="Exit non-zero for findings at this level or higher.",
        )

    def handle(self, *args, **options):
        findings = []
        findings.extend(audit_memberships())
        findings.extend(audit_availability())
        findings.extend(audit_reservations())
        findings.extend(audit_standings())
        findings.extend(audit_point_ledgers())

        errors = [finding for finding in findings if finding["level"] == "error"]
        warnings = [finding for finding in findings if finding["level"] == "warning"]

        if not findings:
            self.stdout.write(self.style.SUCCESS("Data integrity audit passed: no findings."))
            return

        self.stdout.write(self.style.ERROR(f"Data integrity audit found {len(errors)} error(s) and {len(warnings)} warning(s)."))
        for finding in findings:
            line = f"[{finding['level'].upper()}] {finding['code']}: {finding['message']}"
            if finding["level"] == "error":
                self.stdout.write(self.style.ERROR(line))
            else:
                self.stdout.write(self.style.WARNING(line))

        if errors or (options["fail_level"] == "warning" and warnings):
            raise CommandError("Data integrity audit failed.")


def audit_memberships():
    findings = []
    duplicate_players = (
        TeamMembership.objects.filter(status=TeamMembership.STATUS_ACTIVE)
        .values("player_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
        .order_by("player_id")
    )
    for row in duplicate_players:
        findings.append(
            error(
                "membership.multiple_active_player",
                f"player_id={row['player_id']} has {row['total']} active memberships.",
            )
        )

    oversized_teams = (
        TeamMembership.objects.filter(status=TeamMembership.STATUS_ACTIVE)
        .values("team_id")
        .annotate(total=Count("id"))
        .filter(total__gt=3)
        .order_by("team_id")
    )
    for row in oversized_teams:
        findings.append(
            error(
                "membership.team_over_capacity",
                f"team_id={row['team_id']} has {row['total']} active memberships.",
            )
        )

    wrong_division = TeamMembership.objects.filter(status=TeamMembership.STATUS_ACTIVE).exclude(
        Q(player__gender="male", team__division=Team.DIVISION_MENS)
        | Q(player__gender="female", team__division=Team.DIVISION_WOMENS)
    ).select_related("player", "team")
    for membership in wrong_division.order_by("id"):
        findings.append(
            error(
                "membership.wrong_division",
                f"membership_id={membership.id} player_id={membership.player_id} team_id={membership.team_id} has incompatible gender/division.",
            )
        )
    return findings


def audit_availability():
    findings = []
    half_null = AvailabilitySlot.objects.filter(
        Q(starts_at__isnull=True, ends_at__isnull=False) | Q(starts_at__isnull=False, ends_at__isnull=True)
    ).order_by("id")
    for slot in half_null:
        findings.append(error("availability.half_null_interval", f"availability_id={slot.id} has only one timestamp bound."))

    invalid_ranges = AvailabilitySlot.objects.filter(
        starts_at__isnull=False,
        ends_at__isnull=False,
        starts_at__gte=F("ends_at"),
    ).only("id")
    for slot in invalid_ranges.order_by("id"):
        findings.append(error("availability.invalid_interval", f"availability_id={slot.id} starts at or after it ends."))

    findings.extend(
        audit_overlaps(
            AvailabilitySlot.objects.filter(
                status=AvailabilitySlot.STATUS_ACTIVE,
                starts_at__isnull=False,
                ends_at__isnull=False,
            ).order_by("player_id", "starts_at", "ends_at", "id"),
            "availability.overlap_active",
            "availability_id",
        )
    )
    return findings


def audit_reservations():
    findings = []
    invalid_ranges = MatchReservation.objects.filter(starts_at__gte=F("ends_at")).only("id")
    for reservation in invalid_ranges.order_by("id"):
        findings.append(error("reservation.invalid_interval", f"reservation_id={reservation.id} starts at or after it ends."))

    findings.extend(
        audit_overlaps(
            MatchReservation.objects.filter(status=MatchReservation.STATUS_ACTIVE).order_by(
                "player_id", "starts_at", "ends_at", "id"
            ),
            "reservation.overlap_active",
            "reservation_id",
        )
    )
    return findings


def audit_overlaps(rows, code, id_label):
    findings = []
    previous_by_player = {}
    for row in rows:
        previous = previous_by_player.get(row.player_id)
        if previous and row.starts_at < previous.ends_at:
            findings.append(
                error(
                    code,
                    f"player_id={row.player_id} has overlapping rows {id_label}={previous.id} and {id_label}={row.id}.",
                )
            )
            if row.ends_at > previous.ends_at:
                previous_by_player[row.player_id] = row
        else:
            previous_by_player[row.player_id] = row
    return findings


def audit_standings():
    findings = []
    stats = defaultdict(lambda: {"matches_played": 0, "wins": 0, "losses": 0, "points": 0})
    for result in ConfirmedMatchResult.objects.select_related("winning_team", "losing_team").order_by("id"):
        stats[result.winning_team_id]["matches_played"] += 1
        stats[result.winning_team_id]["wins"] += 1
        stats[result.winning_team_id]["points"] += WIN_POINTS
        stats[result.losing_team_id]["matches_played"] += 1
        stats[result.losing_team_id]["losses"] += 1

    standings = {standing.team_id: standing for standing in LadderStanding.objects.select_related("team")}
    for team in Team.objects.order_by("id"):
        expected = stats[team.id]
        standing = standings.get(team.id)
        if standing is None:
            if any(expected.values()):
                findings.append(error("standing.missing", f"team_id={team.id} has confirmed results but no standing row."))
            continue
        actual = {
            "matches_played": standing.matches_played,
            "wins": standing.wins,
            "losses": standing.losses,
            "points": standing.points,
        }
        if actual != expected:
            findings.append(
                error(
                    "standing.stats_mismatch",
                    f"team_id={team.id} standing={actual} expected={expected}.",
                )
            )
    return findings


def audit_point_ledgers():
    findings = []
    ledgers_by_match_team = {
        (ledger.match_id, ledger.team_id): ledger
        for ledger in PointLedger.objects.filter(reason="match_result").order_by("match_id", "team_id", "id")
    }
    for result in ConfirmedMatchResult.objects.order_by("id"):
        expected = {
            result.winning_team_id: WIN_POINTS,
            result.losing_team_id: 0,
        }
        for team_id, points_delta in expected.items():
            ledger = ledgers_by_match_team.get((result.match_id, team_id))
            if ledger is None:
                findings.append(
                    error("ledger.missing_match_result", f"match_id={result.match_id} team_id={team_id} is missing a match_result ledger row.")
                )
            elif ledger.points_delta != points_delta:
                findings.append(
                    error(
                        "ledger.points_mismatch",
                        f"match_id={result.match_id} team_id={team_id} ledger points={ledger.points_delta} expected={points_delta}.",
                    )
                )
    return findings


def error(code, message):
    return {"level": "error", "code": code, "message": message}
