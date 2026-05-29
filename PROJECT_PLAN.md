# Tennis Ladder App Project Plan

## 1. Problem

Club members need an easier way to find available opponents, schedule ladder matches, and submit results without lots of back-and-forth messages.

## 2. Main Users

- Club member
- Admin

## 3. Core Features

- Member registration and login
- Player profile with gender
- Create or join teams of 2 or 3 players
- Men's and women's ladder tables
- Weekly player availability
- Automatic compatible match suggestions
- Challenge another available team
- Match result submission by both teams
- Auto-confirm result if both submissions match
- Admin notification if submitted results conflict

## 4. Main Models

- User
- PlayerProfile
- Team
- AvailabilitySlot
- Challenge
- Match
- MatchResultSubmission
- AdminNotification
- LadderStanding

## 5. First Development Phase

Build account/profile foundation:

- Create Django project
- Create `ladder` app
- Add `PlayerProfile`
- Connect profile to Django `User`
- Add profile to Django admin
- Run migrations
- Create superuser

## 6. Model Relationships

User
  |
  | one-to-one
  v
PlayerProfile
  |
  | many-to-one
  v
Team
  |
  | has many
  v
Challenge / Match

User
  |
  | has many
  v
AvailabilitySlot

Match
  |
  | has many
  v
MatchResultSubmission

Match
  |
  | may create
  v
AdminNotification