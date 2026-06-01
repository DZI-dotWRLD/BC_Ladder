from collections import defaultdict
from itertools import combinations

from .models import PlayerProfile, Team

def get_team_players(team: Team):
    """Return all player profiles assigned to the given team."""
    return team.players.all()


def get_player_pairs(team: Team):
    """Return all possible two-player pairings for a team."""
    players = list(get_team_players(team))
    return list(combinations(players, 2))


def get_pair_matching_slots(player_one: PlayerProfile, player_two: PlayerProfile, week_start_date):
    """Return exact matching availability slots shared by two players in a week."""
    player_one_slots = player_one.availability_slots.filter(week_start_date=week_start_date)
    player_two_slot_keys = {
        get_slot_key(slot)
        for slot in player_two.availability_slots.filter(week_start_date=week_start_date)
    }
    matching_slots = []
    for slot_one in player_one_slots:
        if get_slot_key(slot_one) in player_two_slot_keys:
            matching_slots.append(slot_one)

    return matching_slots


def get_team_pair_availability(team: Team, week_start_date):
    result_list = []
    team_pairs = get_player_pairs(team)

    for pair in team_pairs:
        player_one = pair[0]
        player_two = pair[1]
        matching_slots = get_pair_matching_slots(player_one, player_two, week_start_date)
        if matching_slots:
            result_list.append(
                {
                    "players": (player_one, player_two),
                    "slots": matching_slots,
                }
            )
    return result_list


def get_slot_key(slot):
    """Return the values that define an exact availability time."""
    return (slot.day_of_week, slot.start_time, slot.end_time)


def find_team_match_options(team_a, team_b, week_start_date):
    match_options = []
    team_a_availability = get_team_pair_availability(team_a, week_start_date)
    team_b_availability = get_team_pair_availability(team_b, week_start_date)

    team_b_pairs_by_slot = defaultdict(list)
    for pair_availability in team_b_availability:
        for slot in pair_availability["slots"]:
            team_b_pairs_by_slot[get_slot_key(slot)].append(pair_availability["players"])

    for team_a_pair_availability in team_a_availability:
        for team_a_slot in team_a_pair_availability["slots"]:
            matching_team_b_pairs = team_b_pairs_by_slot[get_slot_key(team_a_slot)]
            for team_b_players in matching_team_b_pairs:
                match_options.append(
                    {
                        "team_a_players": team_a_pair_availability["players"],
                        "team_b_players": team_b_players,
                        "slot": team_a_slot,
                    }
                )

    return match_options

