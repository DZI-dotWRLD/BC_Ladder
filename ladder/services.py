from itertools import combinations
from .models import Team, PlayerProfile



def get_team_players(team: Team):
    """Return all player profiles assigned to the given team."""
    return team.players.all()


def get_player_pairs(team):
    """Return list of possible pairs in a team"""
    players = list(get_team_players(team))
    pairs_list = list(combinations(players, 2))

    return pairs_list


def get_pair_matching_slots(player_one: PlayerProfile, player_two: PlayerProfile, week_start_date):
    """Return exact matching availability slots shared by two players in a week."""
    player_one_slots = player_one.availability_slots.filter(week_start_date=week_start_date)
    player_two_slots = player_two.availability_slots.filter(week_start_date=week_start_date)
    matching_slots = []
    for slot_one in player_one_slots:
        for slot_two in player_two_slots:
            if slot_one.day_of_week == slot_two.day_of_week and slot_one.start_time == slot_two.start_time and slot_one.end_time == slot_two.end_time:
                matching_slots.append(slot_one)
    
    return matching_slots


def get_team_pair_availability(team, week_start_date):
    
    result_list = []
    team_pairs = get_player_pairs(team)
    
    for pair in team_pairs:
        player_one = pair[0]
        player_two = pair[1]
        matching_slots = get_pair_matching_slots(player_one, player_two, week_start_date)
        if matching_slots:
            result_list.append({"players": (player_one,player_two),
                                "slots": matching_slots,})
    return result_list
        

    


