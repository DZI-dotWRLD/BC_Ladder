from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models 





class Team(models.Model):
    DIVISION_MENS = "mens"
    DIVISION_WOMENS = "womens"

    DIVISION_CHOICES = [
        (DIVISION_MENS,"Men's"),
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

    GENDER_CHOICES = [(GENDER_MALE, "Male"),
                      (GENDER_FEMALE, "Female")]
    
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="player_profile"
    )
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="players"
    )


    def clean(self):
        super().clean()

        if self.team is None:
            return 
        
        if self.gender == self.GENDER_MALE and self.team.division != Team.DIVISION_MENS:
            raise ValidationError("Male players can only join men's teams.")
        
        elif self.gender == self.GENDER_FEMALE and self.team.division != Team.DIVISION_WOMENS:
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
        related_name="standing")
    
    position = models.PositiveIntegerField()

    matches_played = models.PositiveIntegerField(default=0)

    wins = models.PositiveIntegerField(default=0)

    losses = models.PositiveIntegerField(default=0)

    points = models.IntegerField(default=0)

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


    player = models.ForeignKey(PlayerProfile, on_delete=models.CASCADE, related_name="availability_slots")

    week_start_date = models.DateField()
    
    day_of_week = models.CharField(max_length=10, choices=DayOfWeek.choices,)

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
                name="unique_player_availability_slot"
            )
        ]


    def clean(self):
        super().clean()

        if self.start_time >= self.end_time:
            raise ValidationError("Start time should be before end time")
        


    def __str__(self):
        return (f"{self.player.user.username} - "
                f"{self.get_day_of_week_display()} "
                f"{self.start_time}-{self.end_time}"
                )