from django.conf import settings
from django.db import models





class Team(models.Model):
    DIVISION_MENS = "mens"
    DIVISION_WOMENS = 'womens'

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


    def __str__(self):
        return f"{self.user.username} profile"



