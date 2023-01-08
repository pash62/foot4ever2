import logging
import os
import html
import json
from datetime import datetime, timedelta
import pytz
import boto3
import traceback

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CommandHandler,
    ApplicationBuilder,
    ExtBot,
    CallbackQueryHandler,
    ContextTypes
)

from constants import public_cmds, admin_cmds, Msg, MotFr, day_names, chat_ids
from teamkeshi import TeamKeshi, create_player_keyboard, create_validation_keyboard

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the developer."""
    # Log the error before we do anything else, so we can see it even if something breaks.
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    # traceback.format_exception returns the usual python message about an exception, but as a
    # list of strings rather than a single string, so we have to join them together.
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    # Build the message with some markup and additional information about what happened.
    # You might need to add some logic to deal with messages longer than the 4096 character limit.
    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    message = (
        f"An exception was raised while handling an update\n"
        f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}"
        "</pre>\n\n"
        f"<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n"
        f"<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n"
        f"<pre>{html.escape(tb_string)}</pre>"
    )

    # Finally, send the message
    await context.bot.send_message(chat_id=chat_ids['Foot Admin'], text=message, parse_mode=ParseMode.HTML)


class FootUser():
    """
    Keeps all necessary information of the member of the foot4ever group
    """

    def __init__(self, user_id, first_name, last_name, players_info, foreign_players_rates):
        self.id = user_id
        self.first_name = first_name
        self.last_name = last_name
        self.user_name = self.make_camel_case(first_name, last_name)
        self.foot_rates = self.get_rates(players_info, foreign_players_rates)

        # They will be set later
        self.is_forbidden = False
        self.order_id = -1  # if order id is bigger-equal than 0, it means user plays in the next match
        self.is_admin = False

    def get_rates(self, players_info, foreign_players_rates):
        """
        Returns rates of all users
        """
        try:
            if str(self.id) in list(players_info.keys()):
                return players_info[str(self.id)][1]
            if self.user_name and self.user_name.lower() in foreign_players_rates:
                return foreign_players_rates[self.user_name.lower()]
        except Exception:
            return [3.00, 3.00, 3.00, 3.00]

    @staticmethod
    def make_camel_case(first_name, last_name):
        """
        Format the given name to camel case
        """
        def to_camel_case(value):
            try:
                return f'{value[0].upper()}{value[1:].lower()}'
            except Exception:
                return value

        if first_name and last_name:
            return f'{to_camel_case(first_name)} {to_camel_case(last_name)}'
        if first_name:
            return to_camel_case(first_name)
        if last_name:
            return to_camel_case(last_name)
        return to_camel_case('inconnu')

    @staticmethod
    def get_foot_user(all_players, user_id=None, user_name=None):
        """
        Returns FootUser by the given user_id
        """
        for user in all_players:
            if user_id and user.id == user_id:
                return user
            try:
                if user_name and user.user_name.lower() == user_name.lower():
                    return user
            except Exception:
                pass
        return None


class Foot4Ever():
    """
    Manages everything about weekly foot sessions
    """

    def __init__(self):
        bot_token = os.getenv('TOKEN')
        app = ApplicationBuilder().token(bot_token).build()
        self.bot = ExtBot(bot_token)

        self.foot_chat_id = chat_ids['Urban Football']
        self.admins = None
        self.is_timkeshi_running = False
        self.cube_name = None
        self.bucket_name = None
        self.s3_storage = None
        self.match_info = None
        self.match_info_s3 = None
        self.user_rates = None
        self.user_rates_s3 = None
        self.players_info = None
        self.foreign_players_rates = None

        # Define commands
        self.init_commands(app)
        app.add_error_handler(error_handler)

        self.init_dates()
        self.init_users_and_chats()
        self.reset_teams()

        app.run_polling()

    async def set_prog(self, update, context):
        """
        Command to set date, time and center of the next session
        """
        cur_chat_id = update.effective_message.chat_id
        try:
            parts = ' '.join(context.args).split(',')
            date = parts[0]
            center_index = int(parts[1])
        except Exception:
            await context.bot.send_message(chat_id=cur_chat_id, text=Msg.bad_set_prog_msg)
            return

        await self.load_users()
        self.init_dates(date, center_index)
        self.reset_teams()
        for user in self.all_players:
            user.order_id = self.admins.index(user.id) if user.id in self.admins else -1
        await context.bot.send_message(chat_id=cur_chat_id, text=Msg.bad_set_prog_succeed)
        await self.get_prog(update, context)
        self.save_match_info()

    def init_dates(self, date='20/06/2018 19:30', center_index=2):
        """
        Information about next foot session
        """
        self.next_date = datetime.strptime(date, '%d/%m/%Y %H:%M')
        self.next_center_index = center_index
        self.centers = {'Urbansoccer,Aubervilliers': (48.907591, 2.375871),
                        'Urbansoccer,La Defense': (48.899902, 2.221698),
                        "Urbansoccer,Porte d'Ivry": (48.820167, 2.393684),
                        "Stade du,Pré Saint-Jean": (48.841287, 2.2000618),
                        'Urbansoccer,Evry': (48.629227, 2.405759),
                        'Stade de,La Muette': (48.8647587, 2.2695797)}

    def init_commands(self, app):
        """
        Init available commands
        """
        app.add_handler(CommandHandler('start', self.start))
        app.add_handler(CommandHandler('add', self.add_player))
        app.add_handler(CommandHandler('del', self.del_player))
        app.add_handler(CommandHandler('prog', self.get_prog))
        app.add_handler(CommandHandler('players', self.get_next_players))
        app.add_handler(CommandHandler('help', self.help))
        app.add_handler(CommandHandler('help_admins', self.help_admins))
        app.add_handler(CommandHandler('all', self.get_all_players_username))
        app.add_handler(CommandHandler('next', self.get_next_date))
        app.add_handler(CommandHandler('add_susp', self.show_add_forbidden_player_keyboard))
        app.add_handler(CommandHandler('del_susp', self.show_del_forbidden_player_keyboard))
        app.add_handler(CommandHandler('arrange', self.show_timkeshi_buttons))
        app.add_handler(CommandHandler('set_prog', self.set_prog))
        app.add_handler(CallbackQueryHandler(self.on_btn_callback))

    def init_users_and_chats(self):
        """
        Loads all users and chat IDs
        """
        self.all_players = []
        self.cur_players = []
        self.load_s3_storage()
        self.load_user_rates()
        self.load_match_info()
        self.user_info_path = os.path.join(os.path.split(__file__)[0], 'user_info.txt')

    async def load_users(self):
        """
        Load all users info via user info file which contains user Ids
        """
        if self.all_players:
            return # Already loaded

        self.admins = []
        admins = await self.bot.get_chat_administrators(self.foot_chat_id)
        print(f'admins are: {admins}')
        for chat_member in admins:
            user = chat_member.user
            user_id, first_name, last_name = user.id, user.first_name, user.last_name
            print(f'{user_id}: {first_name} {last_name}')
            user = FootUser(user_id, first_name, last_name, self.players_info, self.foreign_players_rates)
            if user.id in self.cur_players:
                user.order_id = self.cur_players.index(user.id)
            if user.first_name.lower() in ['pasha', 'saman']:
                self.admins.append(user.id)
                user.is_admin = True
            self.all_players.append(user)
        # Add foreign players as well
        for player in self.cur_players:
            if isinstance(player, str):
                user = self.add_foreign_player(player, False)
                user.order_id = self.cur_players.index(player)
        # self.save_all_users_info()

    def reset_teams(self):
        """
        Reset arranging teams
        """
        self.is_timkeshi_running = False
        self.team_keshi = TeamKeshi(self.all_players)

    async def help(self, update, context):  # pylint:disable=unused-argument
        """
        Display help menu for normal members
        """
        help_txt = ''
        for cmd, desc in public_cmds.items():
            help_txt += f'{desc}: /{cmd}\n'
        await update.message.reply_text(help_txt)

    async def help_admins(self, update, context):  # pylint:disable=unused-argument
        """
        Display help menu for admins
        """
        help_txt = ''
        for cmd, desc in admin_cmds.items():
            help_txt += f'{desc}: /{cmd}\n'
        await update.message.reply_text(help_txt)

    def error(self, bot, update, err):  # pylint:disable=unused-argument
        """
        Log Errors caused by Updates.
        """
        logger.warning(f'Update "{update}" caused error "{err}"')

    async def get_prog(self, update, context):
        """
        Return the next match details
        """
        if self.next_date < datetime.now():
            await context.bot.send_message(chat_id=update.message.chat_id, text=Msg.sign_up_not_started)
            return

        msg = f'{Msg.next_week_prog}\n{self.get_next_program()}'
        await context.bot.send_message(chat_id=update.message.chat_id, text=msg, parse_mode='HTML')
        
        lat, lon = list(self.centers.values())[self.next_center_index]
        await context.bot.send_location(chat_id=update.message.chat_id, latitude=lat, longitude=lon)

    def get_next_program(self):
        """
        Next session date & center
        """
        # another calendar icon: \U0001f4c6
        cur_day = day_names[self.next_date.weekday()]
        next_date = self.next_date.strftime("%d/%m/%Y")
        next_start = self.next_date.strftime('%Hh%M')
        next_opening = (self.next_date + timedelta(minutes=90)).strftime('%Hh%M')
        msg = f'\U0001f4c5 <b>{cur_day}</b> - {next_date} \n'
        msg += f'\u23f0 <b>{next_start}</b> - {next_opening} \n'
        centre = list(self.centers.keys())[self.next_center_index].split(',')
        msg += f'\U0001f4cd {centre[0]} <b>{centre[1]}</b> \n'
        return msg

    async def get_next_players(self, update, context):
        """
        Next session program & players
        """
        cur_chat_id = update.effective_message.chat_id
        await context.bot.send_message(chat_id=cur_chat_id, text=self.get_program_and_players(), parse_mode='HTML')

    def get_program_and_players(self):
        """
        Next session program & players
        """
        msg = f'{self.get_next_program()}\n'
        next_players = sorted(self.all_players, key=lambda x: x.order_id)
        next_players = [player.user_name for player in next_players if player.order_id >= 0]

        for index, player in enumerate(next_players):
            if index == 10:
                msg += f'\n{Msg.reserve}:\n'
            idx = index + 1 if index < 10 else index - 9
            msg += f'{idx}. {player}\n'
        return msg

    async def start(self, update, context):
        """
        Welcome message
        """
        await context.bot.send_message(chat_id=update.message.chat_id, text='Bienvenu à Foot4ever')

    def get_user_from_update(self, update):
        """
        Get current user name
        """
        e_user = update.effective_user
        user = FootUser.get_foot_user(self.all_players, user_id=e_user.id)
        if not user:
            user = FootUser(e_user.id, e_user.first_name, e_user.last_name, self.players_info, self.foreign_players_rates)
            user.is_admin = user.id in self.admins
            self.all_players.append(user)
            # self.save_all_users_info()
        return user

    def is_admin(self, bot, update):
        """
        Returns True if the user is admin, else returns False with an alert message
        """
        user = self.get_user_from_update(update)
        if not user.is_admin:
            bot.send_message(chat_id=update.effective_message.chat_id, text=Msg.missing_permission)
            return False
        return True

    def get_next_order_id(self):
        """
        Returns the next order Id for players who play in the next match
        """
        order_id = -1
        for player in self.all_players:
            if player.order_id > order_id:
                order_id = player.order_id
        return 0 if order_id == -1 else order_id + 1

    async def add_player(self, update, context):  # pylint:disable=inconsistent-return-statements
        """
        Adds a new subscribed player in the list
        """
        await self.load_users()

        cur_chat_id = update.effective_message.chat_id
        user = self.get_user_from_update(update)
        if not user.user_name:
            await context.bot.send_message(chat_id=cur_chat_id, text=Msg.sign_up_not_authorized)

        is_pasha = user.first_name.lower() == 'pasha'
        if self.next_date < datetime.now():
            await context.bot.send_message(chat_id=cur_chat_id, text=Msg.sign_up_not_started)
            return

        if not is_pasha and cur_chat_id != self.foot_chat_id:
            await context.bot.send_message(chat_id=cur_chat_id, text=Msg.wrong_page_add_del)
            return

        if len(context.args) > 0:
            return await self.add_del_forced_player(context.bot, update, context.args, True)

        if user.is_forbidden:
            await context.bot.send_message(chat_id=cur_chat_id, text=f'{user.user_name}, {Msg.you_are_forbidden}')
            return

        if user.order_id < 0:
            user.order_id = self.get_next_order_id()
            await context.bot.send_message(chat_id=cur_chat_id, text=self.get_program_and_players(), parse_mode='HTML')
            self.save_match_info()

    async def del_player(self, update, context):  # pylint:disable=inconsistent-return-statements
        """
        Deletes a new subscribed player in the list
        """
        await self.load_users()

        cur_chat_id = update.effective_message.chat_id
        user = self.get_user_from_update(update)
        is_pasha = user.first_name.lower() == 'pasha'
        if self.next_date < datetime.now():
            await context.bot.send_message(chat_id=cur_chat_id, text=Msg.sign_up_not_started)
            return

        if cur_chat_id != self.foot_chat_id and user.first_name.lower() != 'pasha':
            await context.bot.send_message(chat_id=cur_chat_id, text=Msg.wrong_page_add_del)
            return

        if len(context.args) > 0:
            return await self.add_del_forced_player(context.bot, update, context.args, False)

        if not is_pasha and datetime.now() + timedelta(days=2) > self.next_date:
            await context.bot.send_message(chat_id=cur_chat_id, text=Msg.too_late_del)
            await context.bot.send_message(chat_id=chat_ids['Foot Admin'],
                                     text=f'{user.user_name} {Msg.try_to_del}')
            return

        if user.order_id >= 0:
            user.order_id = -1
            await context.bot.send_message(chat_id=cur_chat_id, text=self.get_program_and_players(), parse_mode='HTML')
            self.save_match_info()

    async def add_del_forced_player(self, bot, update, args, is_in_next_match):
        """
        ADMIN ONLY: Add forced player
        """
        if not self.is_admin(bot, update):
            return

        for player in ' '.join(args).split(','):
            self.add_foreign_player(player, is_in_next_match)

        await bot.send_message(chat_id=update.effective_message.chat_id, text=self.get_program_and_players(), parse_mode='HTML')
        self.save_match_info()

    def add_foreign_player(self, player, is_in_next_match):
        """
        Add manually a player in the list of all players and for the next match
        """
        user = FootUser.get_foot_user(self.all_players, user_name=player)
        if not user:
            names = player.split(' ')
            user = FootUser(0, names[0], names[1] if len(names) > 1 else '', self.players_info, self.foreign_players_rates)
            self.all_players.append(user)
        if is_in_next_match:
            user.order_id = self.get_next_order_id()
        else:
            user.order_id = -1
        return user

    def save_all_users_info(self):
        """
        Keep user names & Ids in a file
        """
        if len(self.all_players) > 0:
            with open(self.user_info_path, mode='w', encoding='utf8') as f:
                f.write(json.dumps({user.id: user.user_name for user in self.all_players if user.user_name}))

    def load_s3_storage(self):
        """
        Load user files from S3 storage
        """
        access_key = os.getenv('CLOUDCUBE_ACCESS_KEY_ID')
        secret_key = os.getenv('CLOUDCUBE_SECRET_ACCESS_KEY')
        url = os.getenv('CLOUDCUBE_URL')
        self.cube_name = url.split('/')[-1]
        self.bucket_name = url.split('https://')[-1].split('.')[0]
        self.s3_storage = boto3.client('s3', aws_access_key_id=access_key, aws_secret_access_key=secret_key)

    def load_match_info(self):
        """
        loads match date and participants
        """
        self.match_info = os.path.join(os.path.split(__file__)[0], 'match_info.txt')
        self.match_info_s3 = os.path.join(self.cube_name, 'match_info.txt')

        try:
            self.s3_storage.download_file(self.bucket_name, self.match_info_s3, self.match_info)
        except Exception as e:
            print(f'Failed to get the match info file: {str(e)}')
            return

        if self.match_info and os.path.exists(self.match_info):
            with open(self.match_info, mode='r', encoding='utf8') as f:
                content = json.load(f)
            if content:
                self.init_dates(date=content['date'], center_index=content['center_index'])
                self.cur_players = content['cur_players'][:]

    def load_user_rates(self):
        """
        Load user rates if the file is available
        """
        self.user_rates = os.path.join(os.path.split(__file__)[0], 'user_rates.json')
        self.user_rates_s3 = os.path.join(self.cube_name, 'user_rates.json')

        try:
            self.s3_storage.download_file(self.bucket_name, self.user_rates_s3, self.user_rates)
        except Exception as e:
            print(f'Failed to get the user rates file: {str(e)}')
            return

        with open(self.user_rates, mode='r', encoding='utf8') as f:
            content = json.load(f)

        print(content)
        self.players_info = content['subscribed']
        self.foreign_players_rates = content['unsubscribed']

    def save_match_info(self):
        """
        saves match date and participants
        """
        content = {}
        content['date'] = datetime.strftime(self.next_date, '%d/%m/%Y %H:%M')
        content['center_index'] = self.next_center_index
        content['cur_players'] = []
        for user in sorted(self.all_players, key=lambda x: x.order_id):
            if user.order_id < 0:
                continue
            content['cur_players'].append(user.id if user.id > 0 else user.user_name)

        with open(self.match_info, mode='w', encoding='utf8') as f:
            f.write(json.dumps(content))

        try:
            self.s3_storage.upload_file(self.match_info, self.bucket_name, self.match_info_s3)
        except Exception as e:
            print(f'Failed to upload the match info file: {str(e)}')
            return

    async def show_add_forbidden_player_keyboard(self, update, context):
        """
        ADMIN ONLY: Shows a keyboard to select forbidden player from the next session
        """
        if self.is_admin(context.bot, update):
            players = [user.user_name for user in self.all_players if not user.is_forbidden]
            reply_markup = InlineKeyboardMarkup(create_player_keyboard(players))
            await update.message.reply_text(Msg.select_forbidden_player, reply_markup=reply_markup)

    async def show_del_forbidden_player_keyboard(self, update, context):
        """
        ADMIN ONLY: Shows a keyboard to delete a forbidden player from the next session
        """
        if self.is_admin(context.bot, update):
            forbidden_players = [user.user_name for user in self.all_players if user.is_forbidden]
            if not forbidden_players:
                await context.bot.send_message(text=Msg.no_forbidden_player, chat_id=update.message.chat_id)
            else:
                reply_markup = InlineKeyboardMarkup(create_player_keyboard(forbidden_players))
                await update.message.reply_text(Msg.select_unforbidden_player, reply_markup=reply_markup)

    async def on_btn_add_forbidden_player(self, bot, update):
        """
        Add forbidden player from the next session
        """
        query = update.callback_query
        if query.data == MotFr.cancel:
            await bot.edit_message_text(text=Msg.operation_cancelled, message_id=query.message.message_id, chat_id=query.message.chat_id)
            return

        user = FootUser.get_foot_user(self.all_players, user_name=query.data)
        user.is_forbidden = True
        user.order_id = -1

        players = ', '.join([user.user_name for user in self.all_players if user.is_forbidden])
        msg = f'{Msg.forbidden_player}\n{players}'
        await bot.edit_message_text(text=msg, message_id=query.message.message_id, chat_id=query.message.chat_id)
        await bot.send_message(chat_id=query.message.chat_id, text=self.get_program_and_players(), parse_mode='HTML')

    async def on_btn_del_forbidden_player(self, bot, update):
        """
        Delete forbidden player from the next session
        """
        query = update.callback_query
        if query.data == MotFr.cancel:
            await bot.edit_message_text(text=Msg.operation_cancelled, message_id=query.message.message_id, chat_id=query.message.chat_id)
            return

        user = FootUser.get_foot_user(self.all_players, user_name=query.data)
        user.is_forbidden = False

        forbidden_players = [user.user_name for user in self.all_players if user.is_forbidden]
        msg = f'{Msg.forbidden_player}\n{", ".join(forbidden_players)}' if forbidden_players else Msg.no_forbidden_player
        await bot.edit_message_text(text=msg, message_id=query.message.message_id, chat_id=query.message.chat_id)

    async def get_all_players_username(self, update, context):  # pylint:disable=unused-argument
        """
        Returns all registered players
        """
        await update.message.reply_text(text='\n'.join([user.user_name for user in self.all_players]))

    async def show_timkeshi_buttons(self, update, context):
        """
        Creates inline keyboard for team keshi
        """
        await self.load_users()
        if update.message.chat.title == 'Urban Football':
            await context.bot.send_message(chat_id=update.message.chat_id, text=Msg.wrong_place_timkeshi)
            return

        if self.is_timkeshi_running:
            await context.bot.send_message(chat_id=update.message.chat_id, text=Msg.timkeshi_is_running)
            return

        self.reset_teams()
        self.is_timkeshi_running = True
        cur_user = self.get_user_from_update(update)
        self.team_keshi.add_captain(cur_user)  # Add first captain
        reply_markup = InlineKeyboardMarkup(create_validation_keyboard())
        await update.message.reply_text(f'{cur_user.user_name}, {Msg.teamkeshi_welcome}', reply_markup=reply_markup)

    async def on_show_timkeshi_buttons(self, bot, update):
        """
        Creates inline keyboard for team keshi
        """
        reply_markup = InlineKeyboardMarkup(self.team_keshi.get_keyboard())
        message = update.effective_message
        await bot.edit_message_text(text=self.team_keshi.get_msg(), message_id=message.message_id, chat_id=message.chat_id, reply_markup=reply_markup)

    async def on_btn_callback(self, update, context):
        """
        Callback button for arranging teams
        """
        text = update.callback_query.message.text
        if text == Msg.select_forbidden_player:
            await self.on_btn_add_forbidden_player(context.bot, update)
        elif text == Msg.select_unforbidden_player:
            await self.on_btn_del_forbidden_player(context.bot, update)
        else:
            await self.on_btn_teamkeshi(context.bot, update)

    async def on_btn_teamkeshi(self, bot, update):
        """
        Performs the related action when user touch on of the team-keshi buttons
        """
        query = update.callback_query
        if query.data in [MotFr.cancel, MotFr.no]:
            await bot.edit_message_text(text=Msg.operation_cancelled, message_id=query.message.message_id, chat_id=query.message.chat_id)
            self.reset_teams()
            return

        cur_user = self.get_user_from_update(update)
        if query.data == MotFr.yes:
            if self.team_keshi.is_finish():
                self.team_keshi.set_validation(self.team_keshi.whose_turn())
                if self.team_keshi.is_both_validated():
                    await self.bot.send_message(chat_ids['Teste team keshi'], self.team_keshi.print_teams(False, True))
                    final_teams = self.team_keshi.print_teams(True, False)
                    msg = f'{Msg.validation_finish}\n{final_teams}'
                    await bot.edit_message_text(text=msg, chat_id=query.message.chat_id, message_id=query.message.message_id)
                    captain1, captain2 = list(self.team_keshi.teams.keys())[0].user_name, list(self.team_keshi.teams.keys())[1].user_name
                    # self.bot.send_message(chat_ids['Foot Admin'], Msg.validation_finish2.format(captain1, captain2))##
                    await self.bot.send_message(chat_ids['Teste team keshi'], Msg.validation_finish2.format(captain1, captain2))
                    msg = f'{self.get_next_program()}\n{final_teams}'
                    # self.bot.send_message(chat_ids['Foot Admin'], msg, parse_mode='HTML')##
                    await self.bot.send_message(chat_ids['Teste team keshi'], msg, parse_mode='HTML')
                    self.reset_teams()
                    return
            else:
                if cur_user.first_name.lower() == 'pasha':
                    cur_user = FootUser.get_foot_user(self.all_players, user_id=240732760)
                if cur_user.id == list(self.team_keshi.teams.keys())[0].id:
                    msg = f'{cur_user.user_name} {Msg.restart_timkeshi}\n'
                    msg += f'{cur_user.user_name}, {Msg.teamkeshi_welcome}'
                    await update.effective_message.reply_text(msg)
                    return
                self.team_keshi.add_captain(cur_user)  # Add 2nd captain
        else:
            cur_user = self.team_keshi.whose_turn()
            if cur_user.id == self.team_keshi.whose_turn().id:
                self.team_keshi.add_player(cur_user, FootUser.get_foot_user(self.all_players, user_name=query.data.split(':')[0]))

        await self.on_show_timkeshi_buttons(bot, update)

    async def get_next_date(self, update, context):
        """
        Returns next date in 45 days if it is a football day (Monday, Tuesday, Wednesday)
        """
        if self.is_admin(context.bot, update):
            days = {0: 'Monday', 1: 'Tuesday', 2: 'Wednesday', 3: 'Thursday', 4: 'Friday', 5: 'Saturday', 6: 'Sunday'}
            next_date = (datetime.now(pytz.timezone('Europe/Paris')) + timedelta(days=45))
            # if weekday in (0, 1, 2): # Monday, Tuesday, Wednesday
            await update.message.reply_text(text=Msg.next_potential_date.format(days[next_date.weekday()], next_date.strftime("%d/%m/%Y")))


def main():
    """ Entry point """
    try:
        Foot4Ever()
    except Exception as e:
        str(e)
        raise e


if __name__ == '__main__':
    main()
