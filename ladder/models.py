from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

class Team(models.Model):
    DIVISION_MENS = "mens"
    DIVISION_WOMENS = "womens"

    DIVISION_CHOICES = [
        (DIVISION_MENS, "Men's"),
        (DIVISION_WOMENS, "Women's"),
    ]

    name = models.CharField(max_length=50)
    division = models.CharField(max_length=20, choices=DIVISION_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

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



    def __str__(self):
        return f"{self.user.username} profile"


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
            )
        ]

    def clean(self):
        super().clean()

        if self.start_time >= self.end_time:
            raise ValidationError("Start time must be before end time.")

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
