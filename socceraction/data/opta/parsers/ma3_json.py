"""JSON parser for Stats Perform MA3 feeds."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from ...base import MissingDataError
from .base import OptaJSONParser, _get_end_x, _get_end_y, assertget


class MA3JSONParser(OptaJSONParser):
    def _get_match_info(self) -> Dict[str, Any]:
        if 'matchInfo' in self.root:
            return self.root['matchInfo']
        raise MissingDataError

    def _get_live_data(self) -> Dict[str, Any]:
        if 'liveData' in self.root:
            return self.root['liveData']
        raise MissingDataError

    def extract_competitions(self) -> Dict[int, Dict[str, Any]]:
        """Return a dictionary with all available competitions.

        Returns
        -------
        dict
            A mapping between competion IDs and the information available about
            each competition in the data stream.
        """
        match_info = self._get_match_info()
        tournament_calender = assertget(match_info, 'tournamentCalendar')
        competition = assertget(match_info, 'competition')
        season_id = assertget(tournament_calender, 'id')
        season = dict(
            season_id=assertget(tournament_calender, 'id'),
            season_name=assertget(tournament_calender, 'name'),
            competition_id=assertget(competition, 'id'),
            competition_name=assertget(competition, 'name'),
        )
        return {season_id: season}

    def extract_teams(self) -> Dict[int, Dict[str, Any]]:
        """Return a dictionary with all available teams.

        Returns
        -------
        dict
            A mapping between team IDs and the information available about
            each team in the data stream.
        """
        match_info = self._get_match_info()
        contestants = assertget(match_info, 'contestant')
        teams = {}
        for contestant in contestants:
            team_id = assertget(contestant, 'id')
            team = dict(
                team_id=team_id,
                team_name=assertget(contestant, 'name'),
            )
            teams[team_id] = team
        return teams

    def extract_players(self) -> Dict[int, Dict[str, Any]]:
        """Return a dictionary with all available players.

        Returns
        -------
        dict
            A mapping between player IDs and the information available about
            each player in the data stream.
        """
        live_data = self._get_live_data()
        events = assertget(live_data, 'event')

        game_duration = 90
        players = {}

        players_data = {
            'starting_position_id': [],
            'player_id': [],
            'team_id': [],
            'position_in_formation': [],
            'jersey_number': [],
        }

        substitutions = []

        for event in events:
            event_type = assertget(event, 'typeId')
            if event_type == 34:
                team_id = assertget(event, 'contestantId')
                qualifiers = assertget(event, 'qualifier')
                for q in qualifiers:
                    qualifier_id = assertget(q, 'qualifierId')
                    value = assertget(q, 'value')
                    value = value.split(', ')
                    if qualifier_id == 30:
                        players_data['player_id'] += value
                        team = [team_id for _ in range(len(value))]
                        players_data['team_id'] += team
                    elif qualifier_id == 44:
                        value = [int(v) for v in value]
                        players_data['starting_position_id'] += value
                    elif qualifier_id == 131:
                        value = [int(v) for v in value]
                        players_data['position_in_formation'] += value
                    elif qualifier_id == 59:
                        value = [int(v) for v in value]
                        players_data['jersey_number'] += value
            elif event_type in (18, 19):
                substitution_data = {
                    'player_id': assertget(event, 'playerId'),
                    'team_id': assertget(event, 'contestantId'),
                }
                if event_type == 18:
                    substitution_data['minute_end'] = assertget(event, 'timeMin')
                else:
                    substitution_data['minute_start'] = assertget(event, 'timeMin')
                substitutions.append(substitution_data)
            elif event_type == 30:
                # todo: add 1st half time
                qualifiers = assertget(event, 'qualifier')
                for q in qualifiers:
                    qualifier = assertget(q, 'qualifierId')
                    if qualifier == 209:
                        new_duration = assertget(event, 'timeMin')
                        if new_duration > game_duration:
                            game_duration = new_duration

            player_id = event.get('playerId')
            if player_id is None:
                continue
            player_name = unidecode.unidecode(assertget(event, 'playerName'))
            if player_id not in players:
                players[player_id] = player_name

        players_data = pd.DataFrame.from_dict(players_data)
        players_data['is_starter'] = (players_data['position_in_formation'] > 0).astype(int)

        substitutions_columns = ['player_id', 'team_id', 'minute_start', 'minute_end']
        substitutions = pd.DataFrame(substitutions, columns=substitutions_columns)
        substitutions = substitutions.groupby(['player_id', 'team_id']).max().reset_index()
        substitutions['minute_start'] = substitutions['minute_start'].fillna(0)
        substitutions['minute_end'] = substitutions['minute_end'].fillna(game_duration)

        if substitutions.empty:
            players_data['minute_start'] = 0
            players_data['minute_end'] = game_duration
        else:
            players_data = players_data.merge(
                substitutions, on=['team_id', 'player_id'], how='left'
            )

        players_data.loc[
            (players_data['is_starter'] == 1) & (players_data['minute_start'].isnull()),
            'minute_start',
        ] = 0

        players_data.loc[
            (players_data['is_starter'] == 1) & (players_data['minute_end'].isnull()), 'minute_end'
        ] = game_duration

        players_data['minutes_played'] = players_data['minute_end'] - players_data['minute_start']
        players_data['minutes_played'] = players_data['minutes_played'].fillna(0).astype(int)

        players_data = players_data[players_data['minutes_played'] > 0]

        players_data['player_name'] = players_data['player_id'].map(players)

        players_data = players_data.set_index('player_id')
        players_data = players_data[
            ['player_name', 'team_id', 'starting_position_id', 'minutes_played']
        ]

        players = {}
        for player_id, player_data in players_data.iterrows():
            player_data = player_data.to_dict()
            player_data['player_id'] = player_id
            players[player_id] = players_data
        return players

    def extract_games(self) -> Dict[int, Dict[str, Any]]:
        """Return a dictionary with all available games.

        Returns
        -------
        dict
            A mapping between game IDs and the information available about
            each game in the data stream.
        """
        match_info = self._get_match_info()
        live_data = self._get_live_data()
        tournament_calender = assertget(match_info, 'tournamentCalendar')
        competition = assertget(match_info, 'competition')
        contestant = assertget(match_info, 'contestant')
        game_id = assertget(match_info, 'id')
        match_details = assertget(live_data, 'matchDetails')
        scores = assertget(match_details, 'scores')
        score_total = assertget(scores, 'total')
        home_score = None
        away_score = None
        if isinstance(score_total, dict):
            home_score = assertget(score_total, 'home')
            away_score = assertget(score_total, 'away')

        game_date = assertget(match_info, 'date')[0:10]
        game_time = assertget(match_info, 'time')[0:8]
        game_datetime = f'{game_date} {game_time}'
        return {
            game_id: dict(
                competition_id=assertget(competition, 'id'),
                game_id=game_id,
                season_id=assertget(tournament_calender, 'id'),
                game_day=int(assertget(match_info, 'week')),
                game_date=game_datetime,
                home_team_id=self._extract_team_id(contestant, 'home'),
                away_team_id=self._extract_team_id(contestant, 'away'),
                home_score=home_score,
                away_score=away_score,
            )
        }

    def extract_events(self) -> Dict[int, Dict[str, Any]]:
        """Return a dictionary with all available events.

        Returns
        -------
        dict
            A mapping between event IDs and the information available about
            each event in the data stream.
        """
        match_info = self._get_match_info()
        live_data = self._get_live_data()
        game_id = assertget(match_info, 'id')

        events = {}
        for element in assertget(live_data, 'event'):
            timestamp_string = assertget(element, 'timeStamp')
            timestamp = self._convert_timestamp(timestamp_string)

            qualifiers = {
                int(q['qualifierId']): q.get('value') for q in element.get('qualifier', [])
            }
            start_x = float(assertget(element, 'x'))
            start_y = float(assertget(element, 'y'))
            end_x = _get_end_x(qualifiers)
            end_y = _get_end_y(qualifiers)
            if end_x is None:
                end_x = start_x
            if end_y is None:
                end_y = start_y

            event_id = int(assertget(element, 'id'))
            event = dict(
                game_id=game_id,
                event_id=event_id,
                type_id=int(assertget(element, 'typeId')),
                period_id=int(assertget(element, 'periodId')),
                minute=int(assertget(element, 'timeMin')),
                second=int(assertget(element, 'timeSec')),
                timestamp=timestamp,
                player_id=element.get('playerId'),
                team_id=assertget(element, 'contestantId'),
                outcome=bool(int(element.get('outcome', 1))),
                start_x=start_x,
                start_y=start_y,
                end_x=end_x,
                end_y=end_y,
                assist=bool(int(element.get('assist', 0))),
                keypass=bool(int(element.get('keyPass', 0))),
                qualifiers=qualifiers,
            )
            events[event_id] = event
        return events

    @staticmethod
    def _extract_team_id(teams: List[Dict[str, str]], side: str) -> Optional[str]:
        for team in teams:
            team_side = assertget(team, 'position')
            if team_side == side:
                team_id = assertget(team, 'id')
                return team_id
        raise MissingDataError

    @staticmethod
    def _convert_timestamp(timestamp_string: str) -> datetime:
        try:
            return datetime.strptime(timestamp_string, '%Y-%m-%dT%H:%M:%S.%fZ')
        except ValueError:
            return datetime.strptime(timestamp_string, '%Y-%m-%dT%H:%M:%SZ')