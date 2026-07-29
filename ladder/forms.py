from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import get_user_model

from .models import PlayerProfile, Team
from .services import CLUB_TIMEZONE


class AvailabilityForm(forms.Form):
    starts_at = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    ends_at = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    def clean_starts_at(self):
        return self.cleaned_data["starts_at"].replace(tzinfo=CLUB_TIMEZONE)

    def clean_ends_at(self):
        return self.cleaned_data["ends_at"].replace(tzinfo=CLUB_TIMEZONE)


class TeamJoinForm(forms.Form):
    team = forms.ModelChoiceField(queryset=Team.active.none())

    def __init__(self, *args, profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Team.active.order_by("division", "name")
        if profile:
            if profile.gender == PlayerProfile.GENDER_MALE:
                queryset = queryset.filter(division=Team.DIVISION_MENS)
            elif profile.gender == PlayerProfile.GENDER_FEMALE:
                queryset = queryset.filter(division=Team.DIVISION_WOMENS)
        self.fields["team"].queryset = queryset


class ProfileSetupForm(forms.ModelForm):
    class Meta:
        model = PlayerProfile
        fields = ["gender"]


class PlayerRegistrationForm(UserCreationForm):
    gender = forms.ChoiceField(choices=PlayerProfile.GENDER_CHOICES)

    class Meta:
        model = get_user_model()
        fields = ("username", "gender", "password1", "password2")


class ScoreSubmissionForm(forms.Form):
    score_widget = forms.NumberInput(attrs={"inputmode": "numeric", "min": "0"})

    set1_team_a = forms.IntegerField(min_value=0, widget=score_widget)
    set1_team_b = forms.IntegerField(min_value=0, widget=score_widget)
    set2_team_a = forms.IntegerField(min_value=0, widget=score_widget)
    set2_team_b = forms.IntegerField(min_value=0, widget=score_widget)
    set3_team_a = forms.IntegerField(min_value=0, required=False, widget=score_widget)
    set3_team_b = forms.IntegerField(min_value=0, required=False, widget=score_widget)

    def normalized_sets(self):
        sets = [
            (self.cleaned_data["set1_team_a"], self.cleaned_data["set1_team_b"]),
            (self.cleaned_data["set2_team_a"], self.cleaned_data["set2_team_b"]),
        ]
        set3_a = self.cleaned_data.get("set3_team_a")
        set3_b = self.cleaned_data.get("set3_team_b")
        if set3_a is not None or set3_b is not None:
            sets.append((set3_a, set3_b))
        return sets
