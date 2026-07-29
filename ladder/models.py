from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class ActiveTeamManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(status=Team.STATUS_ACTIVE)

class Team(models.Model):
    DIVISION_MENS = "mens"
    DIVISION_WOMENS = "womens"
    STATUS_ACTIVE = "active"
    STATUS_RETIRED = "retired"

    DIVISION_CHOICES = [
        (DIVISION_MENS, "Men's"),
        (DIVISION_WOMENS, "Women's"),
    ]
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_RETIRED, "Retired"),
    ]

    name = models.CharField(max_length=50)
    division = models.CharField(max_length=20, choices=DIVISION_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = models.Manager()
    active = ActiveTeamManager()

    def __str__(self):
        return self.name
    


# Player profile class

class PlayerProfile(models.Model):
    GENDER_MALE = "male"
    GENDER_FEMALE = "female"

    GENDER_CHOICES = [
        (GENDER_MALE, "Male"),
        (GENDER_FEMALE, "Female"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="player_profile",
    )
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="players",
    )


    def clean(self):
        super().clean()

        if self.team is None:
            return

        if self.gender == self.GENDER_MALE and self.team.division != Team.DIVISION_MENS:
            raise ValidationError("Male players can only join men's teams.")

        if self.gender == self.GENDER_FEMALE and self.team.division != Team.DIVISION_WOMENS:
            raise ValidationError("Female players can only join women's teams.")

        existing_players = self.team.players.all()

        if self.pk:
            existing_players = existing_players.exclude(pk=self.pk)

        if existing_players.count() >= 3:
            raise ValidationError("Unfortunately, you can't join this team because it already has 3 players.")

    @property
    def active_team(self):
        membership = (
            self.team_memberships.filter(status=TeamMembership.STATUS_ACTIVE)
            .select_related("team")
            .first()
        )
        return membership.team if membership else self.team



    def __str__(self):
        return f"{self.user.username} profile"


class TeamMembership(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUS_REMOVAL_REQUESTED = "removal_requested"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_INACTIVE, "Inactive"),
        (STATUS_REMOVAL_REQUESTED, "Removal requested"),
    ]

    player = models.ForeignKey(
        PlayerProfile,
        on_delete=models.PROTECT,
        related_name="team_memberships",
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    removal_requested_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_team_memberships",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["player"],
                condition=Q(status="active"),
                name="unique_active_membership_per_player",
            ),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_from__lt=F("effective_to")),
                name="membership_effective_range_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["team", "status"], name="membership_team_status_idx"),
            models.Index(fields=["player", "status"], name="membership_player_status_idx"),
        ]

    def clean(self):
        super().clean()

        if self.player_id and self.team_id:
            if self.player.gender == PlayerProfile.GENDER_MALE and self.team.division != Team.DIVISION_MENS:
                raise ValidationError("Male players can only join men's teams.")
            if self.player.gender == PlayerProfile.GENDER_FEMALE and self.team.division != Team.DIVISION_WOMENS:
                raise ValidationError("Female players can only join women's teams.")
        if self.effective_to and self.effective_from >= self.effective_to:
            raise ValidationError("Membership end must be after start.")

    def __str__(self):
        return f"{self.player} on {self.team} ({self.status})"


class LadderStanding(models.Model):
    team = models.OneToOneField(
        Team,
        on_delete=models.CASCADE,
        related_name="standing",
    )

    position = models.PositiveIntegerField()
    matches_played = models.PositiveIntegerField(default=0)
    wins = models.PositiveIntegerField(default=0)
    losses = models.PositiveIntegerField(default=0)
    points = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.position}. {self.team.name}"


class AvailabilitySlot(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_CANCELLED = "cancelled"
    STATUS_RESERVED = "reserved"
    STATUS_CONSUMED = "consumed"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_RESERVED, "Reserved"),
        (STATUS_CONSUMED, "Consumed"),
    ]

    class DayOfWeek(models.TextChoices):
        MONDAY = "monday", "Monday"
        TUESDAY = "tuesday", "Tuesday"
        WEDNESDAY = "wednesday", "Wednesday"
        THURSDAY = "thursday", "Thursday"
        FRIDAY = "friday", "Friday"
        SATURDAY = "saturday", "Saturday"
        SUNDAY = "sunday", "Sunday"

    player = models.ForeignKey(
        PlayerProfile,
        on_delete=models.CASCADE,
        related_name="availability_slots",
    )

    week_start_date = models.DateField()
    day_of_week = models.CharField(max_length=10, choices=DayOfWeek.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    starts_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ends_at = models.DateTimeField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "player",
                    "week_start_date",
                    "day_of_week",
                    "start_time",
                    "end_time",
                ],
                name="unique_player_availability_slot",
            ),
            models.UniqueConstraint(
                fields=["player", "starts_at", "ends_at"],
                condition=Q(status="active"),
                name="unique_active_player_availability_interval",
            ),
            models.CheckConstraint(
                condition=Q(starts_at__isnull=True) | Q(ends_at__isnull=True) | Q(starts_at__lt=F("ends_at")),
                name="availability_interval_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["player", "status", "starts_at", "ends_at"], name="availability_player_window_idx"),
            models.Index(fields=["status", "starts_at", "ends_at"], name="availability_window_idx"),
        ]

    def clean(self):
        super().clean()

        if self.start_time >= self.end_time:
            raise ValidationError("Start time must be before end time.")
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValidationError("Availability start must be before end.")

    def __str__(self):
        return (
            f"{self.player.user.username} - "
            f"{self.get_day_of_week_display()} "
            f"{self.start_time}-{self.end_time}"
        )


class Challenge(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_DECLINED = "declined"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    challenger_team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="sent_challenges",
    )
    opponent_team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="received_challenges",
    )
    challenger_players = models.ManyToManyField(
        PlayerProfile,
        related_name="challenger_challenges",
        blank=True,
    )
    opponent_players = models.ManyToManyField(
        PlayerProfile,
        related_name="opponent_challenges",
        blank=True,
    )
    proposed_week_start_date = models.DateField()
    proposed_day_of_week = models.CharField(
        max_length=10,
        choices=AvailabilitySlot.DayOfWeek.choices,
    )
    proposed_start_time = models.TimeField()
    proposed_end_time = models.TimeField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()

        if self.challenger_team_id and self.opponent_team_id:
            if self.challenger_team_id == self.opponent_team_id:
                raise ValidationError("A team cannot challenge itself.")

            if self.challenger_team.division != self.opponent_team.division:
                raise ValidationError("Teams must be in the same division.")

        if self.proposed_start_time >= self.proposed_end_time:
            raise ValidationError("Proposed start time must be before proposed end time.")

    def __str__(self):
        return (
            f"{self.challenger_team} vs {self.opponent_team} - "
            f"{self.get_proposed_day_of_week_display()} "
            f"{self.proposed_start_time}-{self.proposed_end_time}"
        )


class MatchSuggestion(models.Model):
    STATUS_PROPOSED = "proposed"
    STATUS_PARTIALLY_ACCEPTED = "partially_accepted"
    STATUS_CONFIRMED = "confirmed"
    STATUS_DECLINED = "declined"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PROPOSED, "Proposed"),
        (STATUS_PARTIALLY_ACCEPTED, "Partially accepted"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    team_a = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="team_a_suggestions")
    team_b = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="team_b_suggestions")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PROPOSED)
    version = models.PositiveIntegerField(default=1)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(starts_at__lt=F("ends_at")), name="suggestion_interval_valid"),
            models.CheckConstraint(condition=~Q(team_a=F("team_b")), name="suggestion_teams_distinct"),
        ]
        indexes = [
            models.Index(fields=["team_a", "status", "expires_at"], name="suggestion_team_a_status_idx"),
            models.Index(fields=["team_b", "status", "expires_at"], name="suggestion_team_b_status_idx"),
            models.Index(fields=["status", "starts_at", "ends_at"], name="suggestion_status_window_idx"),
        ]

    def clean(self):
        super().clean()
        if self.team_a_id and self.team_b_id:
            if self.team_a_id == self.team_b_id:
                raise ValidationError("A team cannot play itself.")
            if self.team_a.division != self.team_b.division:
                raise ValidationError("Teams must be in the same division.")
        if self.starts_at >= self.ends_at:
            raise ValidationError("Suggestion start must be before end.")


class SuggestionParticipant(models.Model):
    SIDE_A = "a"
    SIDE_B = "b"
    SIDE_CHOICES = [(SIDE_A, "Team A"), (SIDE_B, "Team B")]

    suggestion = models.ForeignKey(MatchSuggestion, on_delete=models.CASCADE, related_name="participants")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="suggestion_participants")
    player = models.ForeignKey(PlayerProfile, on_delete=models.PROTECT, related_name="suggestion_participants")
    side = models.CharField(max_length=1, choices=SIDE_CHOICES)
    lineup_order = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["suggestion", "player"], name="unique_player_per_suggestion"),
            models.UniqueConstraint(fields=["suggestion", "side", "lineup_order"], name="unique_suggestion_side_order"),
        ]
        indexes = [models.Index(fields=["suggestion", "side", "lineup_order"], name="suggest_part_order_idx")]


class SuggestionAcceptance(models.Model):
    suggestion = models.ForeignKey(MatchSuggestion, on_delete=models.CASCADE, related_name="acceptances")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="suggestion_acceptances")
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="suggestion_acceptances")
    accepted_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["suggestion", "team"], name="unique_suggestion_acceptance_per_team")
        ]
        indexes = [models.Index(fields=["suggestion", "team"], name="suggestion_acceptance_idx")]




class Match(models.Model):

    STATUS_SCHEDULED = "scheduled"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    

    STATUS_CHOICES = [
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    team_a = models.ForeignKey(Team,
                               related_name="team_a",
                               on_delete=models.CASCADE,
                               )
    team_b = models.ForeignKey(Team,
                               related_name="team_b",
                               on_delete=models.CASCADE,
                               )
    scheduled_week_start_date = models.DateField()
    
    scheduled_day_of_week = models.CharField(max_length=20,
                                 choices=AvailabilitySlot.DayOfWeek.choices,
                                 )
    scheduled_start_time = models.TimeField()
    scheduled_end_time = models.TimeField()
    scheduled_starts_at = models.DateTimeField(null=True, blank=True, db_index=True)
    scheduled_ends_at = models.DateTimeField(null=True, blank=True, db_index=True)
    source_suggestion = models.OneToOneField(
        MatchSuggestion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="confirmed_match",
    )

    status = models.CharField(max_length=30,
                              choices=STATUS_CHOICES,
                              default=STATUS_SCHEDULED,
                               )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()

        if self.team_a_id and self.team_b_id:
            if self.team_a_id == self.team_b_id:
                raise ValidationError("A team cannot play itself.")

            if self.team_a.division != self.team_b.division:
                raise ValidationError("Teams must be in the same division.")

        if self.scheduled_start_time >= self.scheduled_end_time:
            raise ValidationError("Scheduled start time must be before scheduled end time.")
        if self.scheduled_starts_at and self.scheduled_ends_at and self.scheduled_starts_at >= self.scheduled_ends_at:
            raise ValidationError("Scheduled start must be before scheduled end.")
        
    def __str__(self):
        return (
            f"{self.team_a} vs {self.team_b} - "
            f"{self.get_scheduled_day_of_week_display()} "
            f"{self.scheduled_start_time}-{self.scheduled_end_time}"
        )


class MatchParticipant(models.Model):
    SIDE_A = "a"
    SIDE_B = "b"
    SIDE_CHOICES = [(SIDE_A, "Team A"), (SIDE_B, "Team B")]

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="participants")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="match_participants")
    player = models.ForeignKey(PlayerProfile, on_delete=models.PROTECT, related_name="match_participations")
    side = models.CharField(max_length=1, choices=SIDE_CHOICES)
    lineup_order = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["match", "player"], name="unique_player_per_match"),
            models.UniqueConstraint(fields=["match", "side", "lineup_order"], name="unique_match_side_order"),
        ]
        indexes = [models.Index(fields=["match", "side", "lineup_order"], name="match_participant_order_idx")]


class MatchReservation(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_RELEASED = "released"
    STATUS_CHOICES = [(STATUS_ACTIVE, "Active"), (STATUS_RELEASED, "Released")]

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="reservations")
    player = models.ForeignKey(PlayerProfile, on_delete=models.PROTECT, related_name="match_reservations")
    availability = models.ForeignKey(
        AvailabilitySlot,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="match_reservations",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(starts_at__lt=F("ends_at")), name="reservation_interval_valid"),
            models.UniqueConstraint(fields=["match", "player"], name="unique_reservation_per_match_player"),
        ]
        indexes = [
            models.Index(fields=["player", "status", "starts_at", "ends_at"], name="reservation_conflict_idx"),
        ]
        


class MatchResultSubmission(models.Model):
    match = models.ForeignKey(Match,
                              on_delete=models.CASCADE,
                              related_name="result_submissions",
                              )
    submitting_team = models.ForeignKey(Team, 
                                        on_delete=models.CASCADE)
    submitting_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="match_result_submissions",
    )
    team_a_sets_won = models.PositiveIntegerField()
    team_b_sets_won = models.PositiveIntegerField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["match", "submitting_team"],
                 name="unique_result_submission_per_team_per_match",
            )
        ]

    def clean(self):
        super().clean()

        if self.match_id and self.submitting_team_id:
            valid_team_ids = [self.match.team_a_id, self.match.team_b_id]

            if self.submitting_team_id not in valid_team_ids:
                raise ValidationError("Submitting team must be one of the match teams.")


class MatchResultSet(models.Model):
    SET_TYPE_REGULAR = "regular"
    SET_TYPE_MATCH_TIEBREAK = "match_tiebreak"
    SET_TYPE_CHOICES = [
        (SET_TYPE_REGULAR, "Regular set"),
        (SET_TYPE_MATCH_TIEBREAK, "Match tie-break"),
    ]

    submission = models.ForeignKey(MatchResultSubmission, on_delete=models.CASCADE, related_name="sets")
    set_order = models.PositiveSmallIntegerField()
    set_type = models.CharField(max_length=30, choices=SET_TYPE_CHOICES)
    team_a_score = models.PositiveSmallIntegerField()
    team_b_score = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["submission", "set_order"], name="unique_result_set_order"),
            models.CheckConstraint(condition=~Q(team_a_score=F("team_b_score")), name="result_set_not_tied"),
        ]
        ordering = ("set_order",)


class ConfirmedMatchResult(models.Model):
    match = models.OneToOneField(Match, on_delete=models.PROTECT, related_name="confirmed_result")
    winning_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="confirmed_wins")
    losing_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="confirmed_losses")
    confirmed_from_submission = models.ForeignKey(
        MatchResultSubmission,
        on_delete=models.PROTECT,
        related_name="confirmed_results",
    )
    confirmed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=~Q(winning_team=F("losing_team")), name="confirmed_result_teams_distinct"),
        ]


class PointLedger(models.Model):
    match = models.ForeignKey(Match, on_delete=models.PROTECT, related_name="point_ledger_entries")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="point_ledger_entries")
    points_delta = models.IntegerField()
    reason = models.CharField(max_length=80, default="match_result")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["match", "team", "reason"], name="unique_point_ledger_entry"),
        ]
        indexes = [models.Index(fields=["team", "created_at"], name="point_ledger_team_created_idx")]
            

class ScoreCorrectionAudit(models.Model):
    REASON_CONFLICT_RESOLUTION = "conflict_resolution"
    REASON_CHOICES = [(REASON_CONFLICT_RESOLUTION, "Conflict resolution")]

    match = models.ForeignKey(Match, on_delete=models.PROTECT, related_name="score_correction_audits")
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="score_correction_audits",
    )
    official_submission = models.ForeignKey(
        MatchResultSubmission,
        on_delete=models.PROTECT,
        related_name="official_score_corrections",
    )
    previous_winning_team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="previous_score_correction_wins",
    )
    previous_losing_team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="previous_score_correction_losses",
    )
    new_winning_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="score_correction_wins")
    new_losing_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="score_correction_losses")
    reason = models.CharField(max_length=40, choices=REASON_CHOICES, default=REASON_CONFLICT_RESOLUTION)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["match", "created_at"], name="score_audit_match_created_idx")]


class AdminNotification(models.Model):
    TYPE_SCORE_CONFLICT = "score_conflict"
    TYPE_CHOICES = [(TYPE_SCORE_CONFLICT, "Score conflict")]
    
    match = models.ForeignKey(Match,
                              on_delete=models.CASCADE,
                              related_name="admin_notifications",
                              )
    notification_type = models.CharField(max_length=40, choices=TYPE_CHOICES, default=TYPE_SCORE_CONFLICT)
    
    message = models.TextField()
    is_resolved = models.BooleanField(
        default=False,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["match", "notification_type"],
                condition=Q(is_resolved=False),
                name="unique_unresolved_notification_per_match_type",
            )
        ]

    def __str__(self):
        return f"Notification for {self.match}"
