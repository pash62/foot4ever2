from collections import OrderedDict

from telegram import InlineKeyboardButton
from constants import MotFr, Msg


def create_player_keyboard(players):
    """
    Create some virtual buttons on UI
    """
    keyboard = []
    row = []
    nb_btn_in_row = 1
    for idx, player in enumerate(players):
        row.append(InlineKeyboardButton(player, callback_data=player))
        if (idx + 1) % nb_btn_in_row == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton(MotFr.cancel, callback_data=MotFr.cancel)])
    return keyboard


def create_validation_keyboard():
    """
    Creates & display YES/NO buttons
    """
    keyboard = [[InlineKeyboardButton(MotFr.yes, callback_data=MotFr.yes),
                 InlineKeyboardButton(MotFr.no, callback_data=MotFr.no)]]
    return keyboard


class TeamKeshi():
    """
    Allows making two football teams with subscribed players
    """

    def __init__(self, all_players):
        """
        Load necessary info to arrange teams
        """
        self.players = [player for player in all_players if player.order_id >= 0]
        self.players = sorted(self.players, key=lambda x: x.order_id)[:10]
        self.teams = OrderedDict()
        self.who_validated = []

    def add_captain(self, captain_player):
        """
        Add a new coptain to arrange teams
        """
        if captain_player not in self.teams:
            self.teams[captain_player] = []
            self.teams[captain_player].append(captain_player)

    def add_player(self, captain_player, player):
        """
        Appens the selected player to the team
        """
        self.teams[captain_player].append(player)

    def format_number(self, number):
        """
        Format the rating number
        """
        return f'{number:.1f}' if int(number) != number else str(int(number))

    def create_player_keyboard(self):
        """
        Create buttons to select the next player
        """
        cur_players = [player.user_name for captain, players in self.teams.items() for player in players]
        players = []
        for player in self.players:
            if player.user_name in cur_players:
                continue
            if player.foot_rates is not None:
                rates = '|'.join([self.format_number(rate) for rate in player.foot_rates])
                players.append(f'{player.user_name}: {rates}')
            else:
                players.append(player.user_name)
        return create_player_keyboard(players)

    def is_finish(self):
        """
        Returns True if both teams are completed
        """
        if len(self.teams) == 0:
            return False
        for players in self.teams.values():
            if len(players) < 5:
                return False
        return True

    def is_both_validated(self):
        """
        Returns True if both teams are completed
        """
        return len(self.who_validated) == 2

    def print_teams(self, sort, show_rates):
        """
        Prints current state of teams
        """
        txt = ''
        for captain, players in self.teams.items():
            is_white = captain == list(self.teams.keys())[0]
            txt += 3 * '\u26aa' if is_white else 3 * '\U0001f534'  # Blue = \U0001f535
            txt += f' {MotFr.team} {MotFr.white if is_white else MotFr.red} '
            txt += 3 * '\u26aa' if is_white else 3 * '\U0001f534'  # Blue = \U0001f535
            txt += '\n'

            if sort:
                players = sorted(players, key=lambda x: x.first_name)  # Sort players alphebatically

            if show_rates:
                rates = [0, 0, 0, 0]
                nb_totals = 0
                for player in players:
                    if player.foot_rates is not None:
                        for idx, rate in enumerate(player.foot_rates):
                            rates[idx] += rate
                        nb_totals += 1
                rates[0] = rates[0] / nb_totals
                rates[1] = rates[1] / nb_totals
                rates[2] = rates[2] / nb_totals
                rates[3] = rates[3] / nb_totals
                rate_goa = self.format_number(rates[0])
                rate_def = self.format_number(rates[1])
                rate_att = self.format_number(rates[2])
                rate_run = self.format_number(rates[3])
                txt += f'{Msg.team_rates.format(rate_goa, rate_def, rate_att, rate_run)}\n'
            for idx, player in enumerate(players):
                txt += f'{idx + 1}. {player.user_name}\n'
            txt += '\n'
        return txt

    def whose_turn(self):
        """
        Indicates whose turn is when selecting players or validating
        """
        captain_1 = list(self.teams.keys())[0]
        captain_2 = list(self.teams.keys())[1]
        is_finish = self.is_finish()
        if is_finish:
            return captain_2 if captain_1 in self.who_validated else captain_1
        if len(self.teams[captain_1]) == len(self.teams[captain_2]):
            rate_1 = sum(captain_1.foot_rates)/len(captain_1.foot_rates)
            rate_2 = sum(captain_2.foot_rates)/len(captain_2.foot_rates)
            if rate_1 > 0 and rate_2 > 0:
                return captain_1 if rate_1 <= rate_2 else captain_2
        return captain_1 if len(self.teams[captain_1]) <= len(self.teams[captain_2]) else captain_2

    def set_validation(self, captain):
        """
        Keep the captain name who validates teams
        """
        self.who_validated.append(captain)

    def get_keyboard(self):
        """
        Returns appropriated keyboard depending on situation of team-keshi
        """
        is_finish = self.is_finish()
        if is_finish:
            keyboard = create_validation_keyboard()
        else:
            keyboard = self.create_player_keyboard()
        return keyboard

    def get_msg(self):
        """
        Returns appropriated message depending on situation of team-keshi
        """
        is_finish = self.is_finish()
        user_name = self.whose_turn().user_name
        return f'{user_name}, {Msg.ask_validation if is_finish else Msg.select_player}\n\n{self.print_teams(False, True)}'
