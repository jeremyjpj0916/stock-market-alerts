import csv
import io

import pysbr
import requests
import classes
from pysbr import *
from datetime import datetime
import time


def process_betting_game(game, event_list, filtered_games_list):
    print(game.league + " LEAGUE GAME PROCESSING")
    for event_match in event_list:
        team_name = ""
        if isinstance(event_match['participant full name'], str):
            # Default as well as NBA for now:
            team_name = event_match['participant full name'].split(" ")[1:][
                0].lower()  # Drop the leading city to match analytics site name
            if game.league == "NBA":
                team_name = event_match['participant full name'].split(" ")[1:][
                    0].lower()  # Drop the leading city to match analytics site name
            if game.league == "EPL":
                team_name = event_match['participant full name'].split(" ")[0:][
                    0].lower()  # Drop the trailing info just take first word (city)

        if team_name == game.team_chosen.lower():  # match found
            if event_match['american odds'] >= int(game.american_odds):
                if isinstance(game.bookie_odds, int):  # If another bookie has better value replace again
                    if event_match['american odds'] > game.bookie_odds:
                        game.bookie = event_match['sportsbook']
                        game.bookie_odds = event_match['american odds']
                        filtered_games_list = filtered_games_list[:-1]  # Remove last appended odds for best odds
                        filtered_games_list.append(game)
                else:
                    game.bookie = event_match['sportsbook']
                    game.bookie_odds = event_match['american odds']
                    filtered_games_list.append(game)


if __name__ == '__main__':

    # Find leagues test
    # league = pysbr.SearchLeagues("Turkish")

    ### First portion, scrape all games we want to bet on ###

    url = 'https://raw.githubusercontent.com/axz2000/sportsbook/main/masterScript/masterPush.csv'
    response = requests.get(url)
    csv_bytes = response.content
    str_file = io.StringIO(csv_bytes.decode('utf-8'), newline='\n')
    csvReader = csv.reader(str_file)

    header = next(csvReader)
    teamChosenIndex = header.index("Bet State Chosen")
    percentageIndex = header.index("Allocation Percentage")
    leagueIndex = header.index("League")
    per_dollar_payoutIndex = header.index("Payouts (per Dollar)")
    dateIndex = header.index("Date")
    american_oddsIndex = header.index("American Odds")
    # Make an empty games list
    gamesList = []

    # Loop through the lines in the file and get each element we want for sportsbetgame object and add it to the list of games.
    for row in csvReader:
        tempGame = classes.SportsBetGame(row[teamChosenIndex], row[percentageIndex], row[leagueIndex],
                                         row[per_dollar_payoutIndex], row[dateIndex], row[american_oddsIndex], "", "")
        gamesList.append(tempGame)

    ### Second portion, check if games we want to bet on exist at favorable odds with bookies ###

    ### Configurable variables ###

    # Example options found here: https://github.com/JeMorriso/PySBR/blob/main/pysbr/config/sportsbooks.yaml
    bookie_list = ['BetOnline', 'Bovada']
    game_type = ['moneyline']

    ### END OF Configurable variables ###

    dt = datetime.strptime(datetime.today().strftime('%Y-%m-%d'), '%Y-%m-%d')
    nfl = NFL()
    nba = NBA()
    epl = EPL()
    npl = Eredivisie()
    sb = Sportsbook()
    # Sleep for two seconds between various API queries to try to avoid hard traffic hitting
    delay = 2
    e_nfl = EventsByDate(nfl.league_id, dt)
    time.sleep(delay)
    e_nba = EventsByDate(nba.league_id, dt)
    time.sleep(delay)
    e_epl = EventsByDate(epl.league_id, dt)
    time.sleep(delay)
    e_npl = EventsByDate(npl.league_id, dt)
    time.sleep(delay)
    cl_nfl = CurrentLines(e_nfl.ids(), nfl.market_ids(game_type), sb.ids(bookie_list))
    time.sleep(delay)
    cl_nba = CurrentLines(e_nba.ids(), nba.market_ids(game_type), sb.ids(bookie_list))
    time.sleep(delay)
    cl_epl = CurrentLines(e_epl.ids(), epl.market_ids(game_type), sb.ids(bookie_list))
    time.sleep(delay)
    cl_npl = CurrentLines(e_npl.ids(), npl.market_ids(game_type), sb.ids(bookie_list))

    # List to contain matches that meet our bookie criteria.
    filtered_games_list = []

    # NOTE, need to adjust search for the participant full name vs event to get right odds

    for game in gamesList:
        if game.team_chosen.startswith('Draw '):  # Ignore "Draw" picks for now, unsure how to handle.
            continue

        if game.league == "KHL":
            print(game)
        elif game.league == "NHL":
            print(game)
        elif game.league == "NFL":
            process_betting_game(game, cl_nfl.list(e_nfl), filtered_games_list)
        elif game.league == "NBA":
            process_betting_game(game, cl_nba.list(e_nba), filtered_games_list)
        elif game.league == "LLA":
            print(game)
        elif game.league == "PPL":
            print(game)
        elif game.league == "ELO":
            print(game)
        elif game.league == "EPL":
            process_betting_game(game, cl_epl.list(e_epl), filtered_games_list)
        elif game.league == "GPL":
            print(game)
        elif game.league == "ISA":
            print(game)
        elif game.league == "TSL":
            print(game)
        elif game.league == "NPL":
            process_betting_game(game, cl_npl.list(e_npl), filtered_games_list)
        elif game.league == "FL1":
            print(game)
        elif game.league == "BPL":
            print(game)

    # Print the games list as a test.
    print(filtered_games_list)
