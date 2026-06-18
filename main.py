import aiohttp
import asyncio
import dateparser
import logging
import sys
import os
import sqlite3
import json
import html
import random
import secrets
import re
import pytz
import string

from db import DataBase

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent
from aiogram.client.default import DefaultBotProperties
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
from random import randint

try:
    from config import API_TOKEN as CONFIG_API_TOKEN
    from config import MAGIC_HANDLERS as CONFIG_MAGIC_HANDLERS
except ImportError:
    CONFIG_API_TOKEN = None
    CONFIG_MAGIC_HANDLERS = []


def parse_magic_handlers(value: str):
    return [handler.strip().lstrip("@") for handler in value.split(",") if handler.strip()]


API_TOKEN = os.getenv("API_TOKEN") or CONFIG_API_TOKEN
MAGIC_HANDLERS = parse_magic_handlers(os.getenv("MAGIC_HANDLERS", "")) or CONFIG_MAGIC_HANDLERS

if not API_TOKEN:
    raise RuntimeError("API_TOKEN is required. Set it in .env or config.py.")

dp = Dispatcher()

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

db = DataBase()

scheduler = AsyncIOScheduler(timezone=pytz.utc)


def normalize_username(username: str):
    return username.strip().lstrip("@") if username else None


def has_magic_access(username: str):
    return username in MAGIC_HANDLERS or db.is_master(username)


def parse_character_json(raw_data: bytes):
    payload = json.loads(raw_data.decode("utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("jsonType") != "character":
        raise ValueError("JSON file must have jsonType=character.")

    inner_payload = payload.get("data")
    if isinstance(inner_payload, str):
        character_data = json.loads(inner_payload)
    elif isinstance(inner_payload, dict):
        character_data = inner_payload
    else:
        raise ValueError("Character JSON must contain data object.")

    name_data = character_data.get("name", {})
    name = name_data.get("value") if isinstance(name_data, dict) else None
    if not name:
        raise ValueError("Character JSON must contain data.name.value.")

    return name, json.dumps(payload, ensure_ascii=False)


def get_character_data(payload: str):
    outer = json.loads(payload)
    inner = outer.get("data")
    if isinstance(inner, str):
        return json.loads(inner)
    if isinstance(inner, dict):
        return inner
    return {}


def stat_modifier(score):
    return (int(score) - 10) // 2


def character_initiative_formula(payload: str):
    data = get_character_data(payload)
    dex_score = data.get("stats", {}).get("dex", {}).get("score", 10)
    modifier = stat_modifier(dex_score)
    lower_payload = payload.lower()
    if "бдитель" in lower_payload or "alert" in lower_payload:
        modifier += 5
    sign = "+" if modifier >= 0 else ""
    return f"d20{sign}{modifier}" if modifier else "d20"


def signed_modifier(value):
    value = int(value)
    return f"+{value}" if value > 0 else str(value)


def formula_with_modifier(base_formula, modifier):
    modifier = int(modifier)
    if modifier == 0:
        return base_formula
    return f"{base_formula}{signed_modifier(modifier)}"


def active_battle_for_message(message: Message):
    if message.chat.type == "private":
        return db.get_active_battle_by_master(message.from_user.username)

    group = resolve_group(message)
    if not group:
        return None
    return db.get_active_battle_by_group(group[0])


def battle_status_text(battle, for_master=False):
    battle_id = battle[0]
    entities = db.get_battle_entities(battle_id)
    all_rolled = bool(entities) and all(entity[8] for entity in entities)
    if for_master and all_rolled:
        entities = db.get_battle_entities(battle_id, rolled_first=True)

    headers = f"{'Name':<24} {'Init':<8} {'Base':<10} {'Round':<7}"
    lines = [headers]
    for entity in entities:
        _, _, name, _, base_formula, current_modifier, next_modifier, initiative_value, has_rolled = entity
        escaped_name = html.escape(name)[:24]
        if for_master:
            init_state = str(initiative_value) if has_rolled else "❌"
        else:
            init_state = "✅" if has_rolled else "❌"
        lines.append(
            f"{escaped_name:<24} {init_state:<8} {base_formula:<10} {signed_modifier(current_modifier):<7}"
        )
    if for_master:
        lines.append("")
        lines.append("Next round modifiers:")
        for entity in entities:
            _, _, name, _, _, _, next_modifier, _, _ = entity
            lines.append(f"{html.escape(name)}: {signed_modifier(next_modifier)}")
    return "<pre>" + "\n".join(lines) + "</pre>"


def battle_roll_keyboard(battle_id):
    entities = db.get_battle_entities(battle_id)
    if entities and all(entity[8] for entity in entities):
        return None

    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Бросить на инициативу", callback_data=f"battle_roll:{battle_id}")
    ]])


async def refresh_battle_messages(battle_id):
    battle = db.get_battle(battle_id)
    if not battle or battle[4] != "active":
        return

    _, _, chat_id, master_username, _, _, group_message_id, master_message_id = battle
    if group_message_id:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=group_message_id,
            text=battle_status_text(battle, for_master=False),
            reply_markup=battle_roll_keyboard(battle_id),
            parse_mode="HTML"
        )

    master_id = db.get_user_id(master_username)
    if master_id and master_message_id:
        await bot.edit_message_text(
            chat_id=master_id,
            message_id=master_message_id,
            text=battle_status_text(battle, for_master=True),
            parse_mode="HTML"
        )


def selection_text(battle, group_name):
    battle_id = battle[0]
    entities = db.get_battle_entities(battle_id)
    lines = [f'"{html.escape(group_name)}"', "", "Selected:"]
    if entities:
        for entity in entities:
            _, _, name, owner, *_ = entity
            lines.append(f"- {html.escape(name)} (@{html.escape(owner)})")
    else:
        lines.append("- none")
    return "\n".join(lines)


def selection_keyboard(battle, group_name):
    battle_id, group_id, _, master_username, *_ = battle
    entities = db.get_battle_entities(battle_id)
    selected_by_owner = {}
    for entity in entities:
        selected_by_owner.setdefault(entity[3], 0)
        selected_by_owner[entity[3]] += 1

    rows = []
    for member, _ in db.get_group_members(group_id):
        chars = db.get_group_characters_for_user(group_id, member)
        if not chars:
            continue
        marker = "✓ " if selected_by_owner.get(member) else ""
        rows.append([InlineKeyboardButton(
            text=f"{marker}@{member}",
            callback_data=f"battle_select:player:{battle_id}:{member}"
        )])

    master_chars = db.get_user_characters_full(master_username)
    if master_chars:
        marker = "✓ " if selected_by_owner.get(master_username) else ""
        rows.append([InlineKeyboardButton(
            text=f"{marker}Мастер",
            callback_data=f"battle_select:master:{battle_id}"
        )])

    rows.append([InlineKeyboardButton(text="Start", callback_data=f"battle_select:done:{battle_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def character_picker_keyboard(battle, owner_username, is_master_picker=False):
    battle_id, group_id, *_ = battle
    if is_master_picker:
        chars = db.get_user_characters_full(owner_username)
    else:
        chars = db.get_group_characters_for_user(group_id, owner_username)

    rows = []
    for character_id, name, _ in chars:
        marker = "✓ " if db.is_battle_character_selected(battle_id, character_id) else ""
        rows.append([InlineKeyboardButton(
            text=f"{marker}{name}",
            callback_data=f"battle_select:char:{battle_id}:{character_id}"
        )])
    rows.append([InlineKeyboardButton(text="Back", callback_data=f"battle_select:back:{battle_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def add_or_toggle_battle_character(battle, character):
    battle_id = battle[0]
    character_id, owner, name, payload = character
    if db.is_battle_character_selected(battle_id, character_id):
        db.remove_battle_entity_by_character(battle_id, character_id)
    else:
        db.add_battle_entity(battle_id, character_id, name, owner, character_initiative_formula(payload))


def modifier_selection_keyboard(battle_id, modifier_type, value):
    rows = []
    for entity in db.get_battle_entities(battle_id):
        entity_id, _, name, *_ = entity
        rows.append([InlineKeyboardButton(
            text=name,
            callback_data=f"battle_modify:{modifier_type}:{battle_id}:{entity_id}:{value}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def remove_entity_keyboard(battle_id):
    rows = []
    for entity in db.get_battle_entities(battle_id):
        entity_id, _, name, *_ = entity
        rows.append([InlineKeyboardButton(
            text=name,
            callback_data=f"battle_remove:{battle_id}:{entity_id}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def resolve_group(message: Message, group_ref: str = None):
    if group_ref:
        return db.get_group(group_ref)

    default_group = db.get_default_group(message.chat.id)
    if default_group:
        return default_group

    return db.get_single_group_for_user(message.from_user.username)


async def require_group_context(message: Message, group_ref: str = None):
    group = resolve_group(message, group_ref)
    if not group:
        await reply(message, "Group is not selected. Use /group_set_default <group> in this chat or pass group name/id.")
        return None
    return group


async def require_group_master(message: Message, group_ref: str = None):
    group = await require_group_context(message, group_ref)
    if not group:
        return None

    group_id, _ = group
    if not db.is_group_master(message.from_user.username, group_id):
        await reply(message, "Only group masters can use this command.")
        return None
    return group


async def require_magic_master(message: Message, group_ref: str = None):
    group = await require_group_master(message, group_ref)
    if not group:
        return None

    if not has_magic_access(message.from_user.username):
        await reply(message, "You do not have magic access.")
        return None
    return group


@dp.message(Command("help"))
async def help_handler(message: Message):
    username = message.from_user.username
    db.add_user(username, message.from_user.id)

    base_help = """
<b>🎲 Основные команды:</b>
/roll &lt;формула&gt; — бросить кубики (например, <i>/roll 2d6+1</i>)
/roll_a &lt;формула&gt; — бросок с преимуществом
/roll_d &lt;формула&gt; — бросок с помехой 
/roll поддерживает inline преимущество/помеху: a(d20), а(d20), d(d20), д(d20)
/roll_h &lt;формула&gt; — скрытый бросок мастерам текущей группы
/set_delete_time &lt;секунды&gt; — установить время удаления
/help — показать эту справку
    
<b>Группы:</b>
/group_create &lt;name&gt; — создать группу, в чате сразу делает ее группой по умолчанию
/group_invite &lt;user&gt; [group] — пригласить игрока; в чате группы можно без [group]
/group_set_default &lt;group&gt; — сделать группу группой по умолчанию в этом чате
/group_add_master &lt;user&gt; [group] — добавить мастера
/group_remove_master &lt;user&gt; [group] — удалить мастера
/groups — показать мои группы

<b>Персонажи:</b>
Отправьте JSON-файл в личку боту — добавить персонажа
/characters — показать моих персонажей
/character_use &lt;character_id&gt; [group] — запросить добавление персонажа в группу
/group_characters [group] — показать персонажей группы

<b>Боевка:</b>
/start_battle — начать настройку битвы
/finish_battle — закончить битву
/next_round — перейти к следующему раунду
/modify_current &lt;число&gt; — изменить модификатор текущего раунда
/modify_next &lt;число&gt; — изменить модификатор следующего раунда
/remove_entity — удалить существо из инициативы
"""

    magic_help = """
\n\n<b>🧙 Магия:</b>
/magic_set_dice &lt;user&gt; &lt;dice&gt; &lt;min&gt; &lt;max&gt; &lt;count&gt; — задать магию пользователю
/magic_clear &lt;user&gt; dice1 dice2 ... — очистить магические кости
/magic_info &lt;user&gt — информации о магии
/give_me_magic &lt;ключ&gt; — получить временный доступ к магии
/magic_keys [время] — сгенерировать ключ на время
"""

    full_help = base_help
    if username in MAGIC_HANDLERS and message.chat.type == "private":
        full_help += magic_help

    await reply(message, full_help.strip())


MAX_DICE_COUNT = 100
MAX_DICE_SIDES = 1000000
DICE_LETTERS = "dDдДkKкК"
ADVANTAGE_LETTERS = "aAаА"
DISADVANTAGE_LETTERS = "dDдД"


class RollResult:
    def __init__(self, value, details=None, parts_count=1):
        self.value = value
        self.details = details or []
        self.parts_count = parts_count


def split_roll_sentences(text: str):
    sentences = []
    start = 0
    depth = 0
    for i, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("Invalid roll format")
        elif char == "," and depth == 0:
            sentences.append(text[start:i])
            start = i + 1
    if depth != 0:
        raise ValueError("Invalid roll format")
    sentences.append(text[start:])
    return sentences


def invert_roll_details(details):
    result = []
    for detail in details:
        if detail.startswith("- "):
            result.append(detail[2:])
        else:
            result.append(f"- {detail}")
    return result


def underline_selected(first_value, second_value, selected_value):
    first = str(first_value)
    second = str(second_value)
    if first_value == selected_value:
        first = f"<u>{first}</u>"
    elif second_value == selected_value:
        second = f"<u>{second}</u>"
    return first, second


class RollParser:
    def __init__(self, username: str, text: str, func):
        self.username = username
        self.text = text.replace(" ", "")
        self.func = func
        self.pos = 0

    def parse(self):
        if not self.text:
            raise ValueError("Invalid roll format")
        result = self.parse_expression()
        if self.pos != len(self.text):
            raise ValueError("Invalid roll format")
        return result

    def parse_expression(self, stop_char=None):
        result = self.parse_factor(stop_char)
        while self.pos < len(self.text) and self.text[self.pos] != stop_char:
            op = self.text[self.pos]
            if op not in "+-":
                break
            self.pos += 1
            next_result = self.parse_factor(stop_char)
            if op == "+":
                result.value += next_result.value
                result.details.extend(next_result.details)
            else:
                result.value -= next_result.value
                result.details.extend(invert_roll_details(next_result.details))
            result.parts_count += next_result.parts_count
        return result

    def parse_factor(self, stop_char=None):
        if self.pos >= len(self.text) or self.text[self.pos] == stop_char:
            raise ValueError("Invalid roll format")

        char = self.text[self.pos]
        if char in "+-":
            self.pos += 1
            result = self.parse_factor(stop_char)
            if char == "-":
                result.value = -result.value
                result.details = invert_roll_details(result.details)
            return result

        if self.is_unary_roll():
            return self.parse_unary_roll()

        if char == "(":
            self.pos += 1
            result = self.parse_expression(")")
            if self.pos >= len(self.text) or self.text[self.pos] != ")":
                raise ValueError("Invalid roll format")
            self.pos += 1
            return result

        return self.parse_number_or_dice()

    def is_unary_roll(self):
        if self.pos + 1 >= len(self.text):
            return False
        char = self.text[self.pos]
        return (char in ADVANTAGE_LETTERS or char in DISADVANTAGE_LETTERS) and self.text[self.pos + 1] == "("

    def parse_unary_roll(self):
        op = self.text[self.pos]
        start = self.pos
        inner_start = self.pos + 2
        inner_end = self.find_matching_parenthesis(self.pos + 1)
        inner_text = self.text[inner_start:inner_end]
        self.pos = inner_end + 1

        first = RollParser(self.username, inner_text, self.func).parse().value
        second = RollParser(self.username, inner_text, self.func).parse().value
        is_advantage = op in ADVANTAGE_LETTERS
        selected = max(first, second) if is_advantage else min(first, second)
        first_text, second_text = underline_selected(first, second, selected)
        formula = self.text[start:self.pos]
        details = [f"{formula}: {first_text}, {second_text} = {selected}"]
        return RollResult(selected, details)

    def find_matching_parenthesis(self, open_pos):
        depth = 0
        for i in range(open_pos, len(self.text)):
            if self.text[i] == "(":
                depth += 1
            elif self.text[i] == ")":
                depth -= 1
                if depth == 0:
                    return i
        raise ValueError("Invalid roll format")

    def parse_number_or_dice(self):
        count_text = self.consume_digits()
        if self.pos < len(self.text) and self.text[self.pos] in DICE_LETTERS:
            count = int(count_text) if count_text else 1
            self.pos += 1
            sides_text = self.consume_digits()
            if not sides_text:
                raise ValueError("Invalid roll format")
            sides = int(sides_text)
            return self.roll_dice(count, sides)

        if not count_text:
            raise ValueError("Invalid roll format")
        value = int(count_text)
        return RollResult(value, [str(value)])

    def consume_digits(self):
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos].isdigit():
            self.pos += 1
        return self.text[start:self.pos]

    def roll_dice(self, count, sides):
        if count > MAX_DICE_COUNT or sides > MAX_DICE_SIDES or count <= 0 or sides <= 0:
            raise ValueError("Roll limits exceeded")

        result = []
        for _ in range(count):
            if db.is_magic_roll(self.username, sides):
                mn, mx = db.get_magic_min_max(self.username, sides)
                if mn > mx:
                    mn, mx = mx, mn
                act_mn = min(max(1, mn), sides)
                act_mx = max(min(sides, mx), 1)
                result.append(self.func(act_mn, act_mx))
                db.decrease_magic_rolls(self.username, sides)
            else:
                result.append(self.func(1, sides))

        total = sum(result)
        details = [f"{count}d{sides}: {', '.join(list(map(str, result)))} = {total}"]
        return RollResult(total, details)

async def delete_message(message: Message, bot_message: Message):
    try:
        await bot_message.delete()
        await message.delete()
    except Exception as e:
        message.answer("Cannot delete message!")

async def reply(message: Message, text: str):
    bot_message = await message.answer(text)
    time = db.get_delete_time(message.from_user.username)
    if (time <= 3600):
        scheduler.add_job(delete_message,
                      trigger='date', 
                      run_date=datetime.now(pytz.utc) + timedelta(seconds=time), 
                      args=(message, bot_message),
                      timezone=pytz.utc)

@dp.message(CommandStart())
async def command_start_handler(message: Message):
    db.add_user(message.from_user.username, message.from_user.id)
    await reply(message, "Welcome to DnD Dice Roller Bot!")


@dp.message(Command("group_create"))
async def group_create_handler(message: Message, command: CommandObject):
    username = message.from_user.username
    db.add_user(username, message.from_user.id)
    name = (command.args or "").strip()
    if not name:
        await reply(message, "Usage: /group_create <group_name>")
        return

    try:
        group_id = db.create_group(name, username)
    except sqlite3.IntegrityError:
        await reply(message, f'Group "{name}" already exists.')
        return

    if message.chat.type != "private":
        db.set_default_group(message.chat.id, group_id)
        await reply(message, f'Group "{name}" created. You are its master.\nThis group is now default for this chat.')
        return

    await reply(message, f'Group "{name}" created. You are its master.')


@dp.message(Command("groups"))
async def groups_handler(message: Message):
    username = message.from_user.username
    db.add_user(username, message.from_user.id)
    groups = db.get_user_groups(username)
    if not groups:
        await reply(message, "You are not a member of any group.")
        return

    lines = ["Your groups:"]
    for group_id, name, is_master in groups:
        role = "master" if is_master else "player"
        lines.append(f"{group_id}: {name} ({role})")
    await reply(message, "\n".join(lines))


@dp.message(Command("group_set_default"))
async def group_set_default_handler(message: Message, command: CommandObject):
    username = message.from_user.username
    db.add_user(username, message.from_user.id)
    group_ref = (command.args or "").strip()
    if not group_ref:
        await reply(message, "Usage: /group_set_default <group_name_or_id>")
        return

    group = db.get_group(group_ref)
    if not group:
        await reply(message, "Group was not found.")
        return

    group_id, name = group
    if not db.is_group_master(username, group_id):
        await reply(message, "Only group masters can set this group as default.")
        return

    db.set_default_group(message.chat.id, group_id)
    await reply(message, f'Default group for this chat is now "{name}".')


@dp.message(Command("group_invite"))
async def group_invite_handler(message: Message, command: CommandObject):
    username = message.from_user.username
    db.add_user(username, message.from_user.id)
    values = (command.args or "").split()
    if not values:
        await reply(message, "Usage: /group_invite <username> [group_name_or_id]")
        return

    target_username = normalize_username(values[0])
    group_ref = values[1] if len(values) > 1 else None
    group = await require_group_master(message, group_ref)
    if not group:
        return

    group_id, group_name = group
    if db.is_group_member(target_username, group_id):
        await reply(message, f"@{target_username} is already in this group.")
        return

    target_id = db.get_user_id(target_username)
    if not target_id:
        await reply(message, f"@{target_username} must start the bot in private messages before invitation.")
        return

    db.add_group_invite(group_id, target_username, username)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Accept", callback_data=f"group_invite:accept:{group_id}"),
        InlineKeyboardButton(text="Decline", callback_data=f"group_invite:decline:{group_id}")
    ]])

    try:
        await bot.send_message(
            chat_id=target_id,
            text=f'@{username} invites you to group "{group_name}".',
            reply_markup=keyboard
        )
    except Exception:
        await reply(message, f"Could not send private invitation to @{target_username}.")
        return

    await reply(message, f"Invitation sent to @{target_username}.")


@dp.callback_query(lambda callback: callback.data and callback.data.startswith("group_invite:"))
async def group_invite_callback(callback: CallbackQuery):
    username = callback.from_user.username
    db.add_user(username, callback.from_user.id)

    _, action, group_id_text = callback.data.split(":")
    group_id = int(group_id_text)
    invite = db.get_group_invite(group_id, username)
    group = db.get_group(str(group_id))

    if not invite or invite[3] != "pending" or not group:
        await callback.answer("This invitation is no longer active.", show_alert=True)
        return

    _, group_name = group
    if action == "accept":
        db.add_group_member(group_id, username)
        db.set_group_invite_status(group_id, username, "accepted")
        await callback.message.edit_text(f'You joined group "{group_name}".')
        await callback.answer("Invitation accepted.")
    else:
        db.set_group_invite_status(group_id, username, "declined")
        await callback.message.edit_text(f'You declined invitation to group "{group_name}".')
        await callback.answer("Invitation declined.")


@dp.message(Command("group_add_master"))
async def group_add_master_handler(message: Message, command: CommandObject):
    values = (command.args or "").split()
    if not values:
        await reply(message, "Usage: /group_add_master <username> [group_name_or_id]")
        return

    target_username = normalize_username(values[0])
    group_ref = values[1] if len(values) > 1 else None
    group = await require_group_master(message, group_ref)
    if not group:
        return

    group_id, _ = group
    if not db.is_group_member(target_username, group_id):
        await reply(message, f"@{target_username} is not a member of this group.")
        return

    db.set_group_master(group_id, target_username, 1)
    await reply(message, f"@{target_username} is now a group master.")


@dp.message(Command("group_remove_master"))
async def group_remove_master_handler(message: Message, command: CommandObject):
    values = (command.args or "").split()
    if not values:
        await reply(message, "Usage: /group_remove_master <username> [group_name_or_id]")
        return

    target_username = normalize_username(values[0])
    group_ref = values[1] if len(values) > 1 else None
    group = await require_group_master(message, group_ref)
    if not group:
        return

    group_id, _ = group
    if not db.is_group_master(target_username, group_id):
        await reply(message, f"@{target_username} is not a master of this group.")
        return

    if db.count_group_masters(group_id) <= 1:
        await reply(message, "Group must have at least one master.")
        return

    db.set_group_master(group_id, target_username, 0)
    await reply(message, f"@{target_username} is no longer a group master.")


@dp.message(Command("characters"))
async def characters_handler(message: Message):
    username = message.from_user.username
    db.add_user(username, message.from_user.id)
    characters = db.get_user_characters(username)
    if not characters:
        await reply(message, "You have no characters. Send a character JSON file to this private chat.")
        return

    lines = ["Your characters:"]
    for character_id, name, _ in characters:
        lines.append(f"{character_id}: {name}")
    await reply(message, "\n".join(lines))


@dp.message(Command("group_characters"))
async def group_characters_handler(message: Message, command: CommandObject):
    group_ref = (command.args or "").strip() or None
    group = await require_group_context(message, group_ref)
    if not group:
        return

    group_id, group_name = group
    if not db.is_group_member(message.from_user.username, group_id):
        await reply(message, "Only group members can view group characters.")
        return

    characters = db.get_group_characters(group_id)
    if not characters:
        await reply(message, f'Group "{group_name}" has no characters.')
        return

    lines = [f'Characters in "{group_name}":']
    for character_id, name, owner in characters:
        lines.append(f"{character_id}: {name} (@{owner})")
    await reply(message, "\n".join(lines))


@dp.message(Command("character_use"))
async def character_use_handler(message: Message, command: CommandObject):
    username = message.from_user.username
    db.add_user(username, message.from_user.id)

    values = (command.args or "").split()
    if not values:
        await reply(message, "Usage: /character_use <character_id> [group_name_or_id]")
        return

    try:
        character_id = int(values[0])
    except ValueError:
        await reply(message, "character_id must be a number.")
        return

    character = db.get_character(character_id)
    if not character:
        await reply(message, "Character was not found.")
        return

    _, owner, character_name, _ = character
    if owner != username:
        await reply(message, "You can only use your own characters.")
        return

    group_ref = values[1] if len(values) > 1 else None
    group = await require_group_context(message, group_ref)
    if not group:
        return

    group_id, group_name = group
    if not db.is_group_member(username, group_id):
        await reply(message, "You must be a member of this group to use a character in it.")
        return

    if db.is_group_character(group_id, character_id):
        await reply(message, f'"{character_name}" is already added to "{group_name}".')
        return

    if db.is_group_master(username, group_id):
        request_id = db.add_group_character_request(group_id, character_id, username)
        db.approve_group_character_request(request_id, username)
        await reply(message, f'"{character_name}" was added to "{group_name}".')
        return

    request_id = db.add_group_character_request(group_id, character_id, username)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Accept", callback_data=f"character_request:accept:{request_id}"),
        InlineKeyboardButton(text="Decline", callback_data=f"character_request:decline:{request_id}")
    ]])

    sent_count = 0
    for master_name in db.get_group_masters(group_id):
        master_id = db.get_user_id(master_name)
        if master_id:
            await bot.send_message(
                chat_id=master_id,
                text=f'@{username} wants to add character "{character_name}" to group "{group_name}".',
                reply_markup=keyboard
            )
            sent_count += 1

    if sent_count == 0:
        await reply(message, "No group masters are available in private messages.")
        return

    await reply(message, f'Request to add "{character_name}" to "{group_name}" was sent to group masters.')


@dp.callback_query(lambda callback: callback.data and callback.data.startswith("character_request:"))
async def character_request_callback(callback: CallbackQuery):
    username = callback.from_user.username
    db.add_user(username, callback.from_user.id)

    _, action, request_id_text = callback.data.split(":")
    request_id = int(request_id_text)
    request = db.get_group_character_request(request_id)
    if not request:
        await callback.answer("This request does not exist.", show_alert=True)
        return

    _, group_id, character_id, requested_by, status = request
    group = db.get_group(str(group_id))
    character = db.get_character(character_id)
    if not group or not character:
        await callback.answer("Group or character was not found.", show_alert=True)
        return

    if status != "pending":
        await callback.answer("This request is already resolved.", show_alert=True)
        return

    if not db.is_group_master(username, group_id):
        await callback.answer("Only group masters can resolve this request.", show_alert=True)
        return

    _, group_name = group
    _, owner, character_name, _ = character
    owner_id = db.get_user_id(owner)

    if action == "accept":
        db.approve_group_character_request(request_id, username)
        await callback.message.edit_text(f'"{character_name}" was added to group "{group_name}".')
        await callback.answer("Character accepted.")
        if owner_id and owner != username:
            await bot.send_message(
                chat_id=owner_id,
                text=f'Your character "{character_name}" was accepted in group "{group_name}".'
            )
    else:
        db.decline_group_character_request(request_id, username)
        await callback.message.edit_text(f'"{character_name}" was declined for group "{group_name}".')
        await callback.answer("Character declined.")
        if owner_id and owner != username:
            await bot.send_message(
                chat_id=owner_id,
                text=f'Your character "{character_name}" was declined for group "{group_name}".'
            )


@dp.message(lambda message: message.chat.type == "private" and message.document)
async def character_document_handler(message: Message):
    username = message.from_user.username
    db.add_user(username, message.from_user.id)

    document = message.document
    if not document.file_name or not document.file_name.lower().endswith(".json"):
        await reply(message, "Send a .json character file.")
        return

    if document.file_size and document.file_size > 2 * 1024 * 1024:
        await reply(message, "Character JSON file is too large.")
        return

    try:
        file = await bot.get_file(document.file_id)
        downloaded = await bot.download_file(file.file_path)
        raw_data = downloaded.read()
        character_name, payload = parse_character_json(raw_data)
    except Exception as e:
        await reply(message, f"Could not parse character JSON: {e}")
        return

    character_id = db.add_character(username, character_name, payload)
    await reply(
        message,
        f'Character "{character_name}" was added. ID: {character_id}\n'
        f'Use /character_use {character_id} <group> to request adding it to a game.'
    )


@dp.message(Command("start_battle"))
async def start_battle_handler(message: Message):
    username = message.from_user.username
    db.add_user(username, message.from_user.id)

    if message.chat.type == "private":
        await reply(message, "Start battle in the group chat where /group_set_default is configured.")
        return

    group = await require_group_master(message)
    if not group:
        return

    group_id, group_name = group
    if db.get_active_battle_by_group(group_id):
        await reply(message, "This group already has an active battle.")
        return

    battle_id = db.create_battle(group_id, message.chat.id, username)
    battle = db.get_battle(battle_id)
    master_id = db.get_user_id(username)

    try:
        setup_message = await bot.send_message(
            chat_id=master_id,
            text=selection_text(battle, group_name),
            reply_markup=selection_keyboard(battle, group_name)
        )
    except Exception:
        db.finish_battle(battle_id)
        await reply(message, "Could not send battle setup to your private messages. Start the bot in private chat first.")
        return

    db.set_battle_messages(battle_id, master_message_id=setup_message.message_id)
    await reply(message, "Battle setup was sent to the master in private messages.")


@dp.callback_query(lambda callback: callback.data and callback.data.startswith("battle_select:"))
async def battle_select_callback(callback: CallbackQuery):
    username = callback.from_user.username
    parts = callback.data.split(":")
    action = parts[1]
    battle_id = int(parts[2])
    battle = db.get_battle(battle_id)
    if not battle or battle[4] != "selecting":
        await callback.answer("Battle setup is no longer active.", show_alert=True)
        return

    _, group_id, chat_id, master_username, *_ = battle
    group = db.get_group(str(group_id))
    group_name = group[1] if group else "group"
    if username != master_username:
        await callback.answer("Only battle master can edit setup.", show_alert=True)
        return

    if action == "player":
        owner = parts[3]
        chars = db.get_group_characters_for_user(group_id, owner)
        if len(chars) == 1:
            character = db.get_character(chars[0][0])
            add_or_toggle_battle_character(battle, character)
            await callback.message.edit_text(
                selection_text(battle, group_name),
                reply_markup=selection_keyboard(battle, group_name)
            )
        else:
            await callback.message.edit_text(
                f"Choose characters for @{owner}:",
                reply_markup=character_picker_keyboard(battle, owner)
            )
        await callback.answer()
        return

    if action == "master":
        await callback.message.edit_text(
            "Choose master characters:",
            reply_markup=character_picker_keyboard(battle, master_username, is_master_picker=True)
        )
        await callback.answer()
        return

    if action == "char":
        character_id = int(parts[3])
        character = db.get_character(character_id)
        if character:
            add_or_toggle_battle_character(battle, character)
            _, owner, _, _ = character
            await callback.message.edit_reply_markup(
                reply_markup=character_picker_keyboard(
                    battle,
                    owner,
                    is_master_picker=(owner == master_username)
                )
            )
        await callback.answer()
        return

    if action == "back":
        await callback.message.edit_text(
            selection_text(battle, group_name),
            reply_markup=selection_keyboard(battle, group_name)
        )
        await callback.answer()
        return

    if action == "done":
        if not db.get_battle_entities(battle_id):
            await callback.answer("Select at least one character.", show_alert=True)
            return
        db.set_battle_status(battle_id, "active")
        battle = db.get_battle(battle_id)
        group_message = await bot.send_message(
            chat_id=chat_id,
            text=battle_status_text(battle, for_master=False),
            reply_markup=battle_roll_keyboard(battle_id),
            parse_mode="HTML"
        )
        db.set_battle_messages(battle_id, group_message_id=group_message.message_id)
        battle = db.get_battle(battle_id)
        await callback.message.edit_text(battle_status_text(battle, for_master=True), parse_mode="HTML")
        await callback.answer("Battle started.")
        return


@dp.callback_query(lambda callback: callback.data and callback.data.startswith("battle_roll:"))
async def battle_roll_callback(callback: CallbackQuery):
    username = callback.from_user.username
    db.add_user(username, callback.from_user.id)
    battle_id = int(callback.data.split(":")[1])
    battle = db.get_battle(battle_id)
    if not battle or battle[4] != "active":
        await callback.answer("Battle is not active.", show_alert=True)
        return

    entities = [entity for entity in db.get_battle_entities_for_user(battle_id, username) if not entity[8]]
    if not entities:
        await callback.answer("You have no pending initiative rolls.", show_alert=True)
        return

    for entity in entities:
        entity_id, _, _, owner, base_formula, current_modifier, *_ = entity
        formula = formula_with_modifier(base_formula, current_modifier)
        initiative = RollParser(owner, formula, randint).parse().value
        db.set_battle_entity_roll(entity_id, initiative)

    await callback.answer("Initiative rolled.")
    await refresh_battle_messages(battle_id)


@dp.message(Command("finish_battle"))
async def finish_battle_handler(message: Message):
    battle = active_battle_for_message(message)
    if not battle:
        await reply(message, "No active battle was found.")
        return

    battle_id, _, chat_id, master_username, _, _, group_message_id, master_message_id = battle
    if message.from_user.username != master_username:
        await reply(message, "Only battle master can finish battle.")
        return

    db.finish_battle(battle_id)
    if group_message_id:
        await bot.edit_message_text(chat_id=chat_id, message_id=group_message_id, text="Battle finished.")
    master_id = db.get_user_id(master_username)
    if master_id and master_message_id:
        await bot.edit_message_text(chat_id=master_id, message_id=master_message_id, text="Battle finished.")
    await reply(message, "Battle finished.")


@dp.message(Command("next_round"))
async def next_round_handler(message: Message):
    battle = active_battle_for_message(message)
    if not battle:
        await reply(message, "No active battle was found.")
        return

    battle_id, _, _, master_username, status, *_ = battle
    if status != "active":
        await reply(message, "Battle is not active yet.")
        return
    if message.from_user.username != master_username:
        await reply(message, "Only battle master can start next round.")
        return

    db.advance_battle_round(battle_id)
    await refresh_battle_messages(battle_id)
    await reply(message, "Next round started.")


async def modifier_command(message: Message, command: CommandObject, modifier_type: str):
    if message.chat.type != "private":
        await reply(message, "Use this command in private messages with the bot.")
        return

    battle = active_battle_for_message(message)
    if not battle:
        await reply(message, "No active battle was found.")
        return

    battle_id, _, _, master_username, status, *_ = battle
    if status != "active":
        await reply(message, "Battle is not active yet.")
        return
    if message.from_user.username != master_username:
        await reply(message, "Only battle master can modify battle entities.")
        return

    try:
        value = int((command.args or "").strip())
    except ValueError:
        await reply(message, f"Usage: /modify_{modifier_type} <integer_modifier>")
        return

    await message.answer(
        f"Choose entity for {modifier_type} modifier {signed_modifier(value)}:",
        reply_markup=modifier_selection_keyboard(battle_id, modifier_type, value)
    )


@dp.message(Command("modify_current"))
async def modify_current_handler(message: Message, command: CommandObject):
    await modifier_command(message, command, "current")


@dp.message(Command("modify_next"))
async def modify_next_handler(message: Message, command: CommandObject):
    await modifier_command(message, command, "next")


@dp.callback_query(lambda callback: callback.data and callback.data.startswith("battle_modify:"))
async def battle_modify_callback(callback: CallbackQuery):
    username = callback.from_user.username
    _, modifier_type, battle_id_text, entity_id_text, value_text = callback.data.split(":")
    battle_id = int(battle_id_text)
    entity_id = int(entity_id_text)
    value = int(value_text)
    battle = db.get_battle(battle_id)
    if not battle or battle[4] != "active":
        await callback.answer("Battle is not active.", show_alert=True)
        return
    if username != battle[3]:
        await callback.answer("Only battle master can modify entities.", show_alert=True)
        return

    db.set_battle_entity_modifier(entity_id, modifier_type, value)
    await callback.message.edit_text(f"{modifier_type.capitalize()} modifier set to {signed_modifier(value)}.")
    await refresh_battle_messages(battle_id)
    await callback.answer("Modifier updated.")


@dp.message(Command("remove_entity"))
async def remove_entity_handler(message: Message):
    if message.chat.type != "private":
        await reply(message, "Use this command in private messages with the bot.")
        return

    battle = active_battle_for_message(message)
    if not battle:
        await reply(message, "No active battle was found.")
        return

    battle_id, _, _, master_username, status, *_ = battle
    if status != "active":
        await reply(message, "Battle is not active yet.")
        return
    if message.from_user.username != master_username:
        await reply(message, "Only battle master can remove entities.")
        return

    await message.answer("Choose entity to remove:", reply_markup=remove_entity_keyboard(battle_id))


@dp.callback_query(lambda callback: callback.data and callback.data.startswith("battle_remove:"))
async def battle_remove_callback(callback: CallbackQuery):
    username = callback.from_user.username
    _, battle_id_text, entity_id_text = callback.data.split(":")
    battle_id = int(battle_id_text)
    entity_id = int(entity_id_text)
    battle = db.get_battle(battle_id)
    if not battle or battle[4] != "active":
        await callback.answer("Battle is not active.", show_alert=True)
        return
    if username != battle[3]:
        await callback.answer("Only battle master can remove entities.", show_alert=True)
        return

    db.remove_battle_entity(battle_id, entity_id)
    await callback.message.edit_text("Entity removed.")
    await refresh_battle_messages(battle_id)
    await callback.answer("Entity removed.")


def roll_text(username: str, args: str, line_prefix: str = "", func=lambda mn, mx: randint(mn, mx)):
    total_line = "_______________________________\n"
    divide_line = "_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _\n";

    sentences = split_roll_sentences(args or "")
    text = ""
    total_result = []
    last_parts_count = 0

    for i, sentence in enumerate(sentences):
        result = RollParser(username, sentence, func).parse()
        last_parts_count = result.parts_count
        total_result.append(result.value)

        if i != 0:
            text += divide_line

        text += "\n".join(f"{line_prefix}{detail}" for detail in result.details)
        if result.details:
            text += "\n"

    if len(total_result) > 1 or last_parts_count > 1:
        text += total_line
        total_result_str = ", ".join(str(x) for x in total_result)
        text += f"Total result = {total_result_str}"

    return text

@dp.inline_query()
async def inline_pattern(inline_query: InlineQuery):
    query = inline_query.query.strip()

    if query.lower().startswith("roll"):
        try:
            args = query[len(query.split()[0]):].strip()
            username = inline_query.from_user.username or "unknown_user"
            user_id = inline_query.from_user.id
            db.add_user(username, user_id)
            text = roll_text(username, args)
        except Exception:
            text = "An error occured. Use another format"
        input_content = InputTextMessageContent(message_text=text, parse_mode="HTML")
        if query.lower().startswith("roll_h"):
            input_content = InputTextMessageContent(message_text=f'<span class="tg-spoiler">{text}</span>', parse_mode="HTML")
        await bot.answer_inline_query(
            inline_query.id,
            results=[
                InlineQueryResultArticle(
                    id="roll_result",
                    title="🎲 Roll dices",
                    description="Click to roll",
                    input_message_content=input_content
                )
            ],
            cache_time=1,
            is_personal=True
        )

async def reply_pattern(message: Message, command: CommandObject, line_prefix: str, func):
    db.add_user(message.from_user.username, message.from_user.id)
    try:
        text = roll_text(message.from_user.username, command.args, line_prefix, func)
        await reply(message, text)
    except Exception as e:
        await reply(message, f"An error occurred. Please make sure you provided the details in the correct format")
        print(e)

async def hidden_pattern(message: Message, command: CommandObject, line_prefix: str, func):
    db.add_user(message.from_user.username, message.from_user.id)
    group = await require_group_context(message)
    if not group:
        return

    group_id, group_name = group
    try:
        text = roll_text(message.from_user.username, command.args, line_prefix, func)
        sent_count = 0
        for master_name in db.get_group_masters(group_id):
            master_id = db.get_user_id(master_name)
            if master_id:
                await bot.send_message(
                    chat_id=master_id,
                    text=f'hidden roll in "{group_name}" from {message.from_user.username}:\n{text}'
                )
                sent_count += 1
        if sent_count == 0:
            await reply(message, "No group masters are available in private messages.")
        else:
            await reply(message, "Hidden roll sent to group masters.")
    except Exception as e:
        await reply(message, f"An error occurred. Please make sure you provided the details in the correct format")
        print(e)

@dp.message(Command("roll_h"))
async def roll_h_handler(message: Message, command: CommandObject):
    await hidden_pattern(message, command, "", lambda mn, mx: randint(mn, mx))

@dp.message(Command("roll"))
async def roll_handler(message: Message, command: CommandObject):
    await reply_pattern(message, command, "", lambda mn, mx: randint(mn, mx))

@dp.message(Command("roll_a"))
async def roll_a_handler(message: Message, command: CommandObject):
    await reply_pattern(message, command, "max ", lambda mn, mx: max(randint(mn, mx), randint(mn, mx)))

@dp.message(Command("roll_d"))
async def roll_d_handler(message: Message, command: CommandObject):
    await reply_pattern(message, command, "min ", lambda mn, mx: min(randint(mn, mx), randint(mn, mx)))

@dp.message(Command("set_delete_time"))
async def set_delete_time_handler(message: Message, command: CommandObject):
    db.add_user(message.from_user.username, message.from_user.id)
    try:
        time = int(command.args)
        db.set_delete_time(message.from_user.username, time)
    except Exception as e:
        await reply(message, "An error occurred. Please make sure you provided the details in the correct format")


# -------------------------------------------- Magic part ----------------------------------------------------

@dp.message(Command("magic_set_dice", "msd", "set_dice"))
async def magic_set_dice(message: Message, command: CommandObject):
    group = await require_magic_master(message)
    if not group:
        return

    group_id, _ = group
    values = (command.args or "").split()
    if len(values) < 4 or len(values) > 5:
        await reply(message, "There is not right amount of arguments (format: username, dice, min, max, count)")
        return
    if (len(values) == 4):
        user = normalize_username(values[0])
        dice, mn, mx = list(map(int, values[1:]))
        count = 1
    else:
        user = normalize_username(values[0])
        dice, mn, mx, count = list(map(int, values[1:]))
    if not db.is_group_member(user, group_id):
        await reply(message, f"@{user} is not a member of this group.")
        return
    if mn > mx:
        await reply(message, "no Abracadabra shiz")
        return
    db.set_magic_rolls(user, dice, mn, mx, count)
    await reply(message, "Abracadabra")

@dp.message(Command("magic_info", "info"))
async def magic_info(message: Message, command: CommandObject):
    group = await require_magic_master(message)
    if not group:
        return

    group_id, _ = group
    values = (command.args or "").split()
    if len(values) != 1:
        await reply(message, "There is not right amount of arguments (format: username)")
        return
    user = normalize_username(values[0])
    if not db.is_group_member(user, group_id):
        await reply(message, f"@{user} is not a member of this group.")
        return
    info = db.get_magic_info(user)
    if (info):
        result = f"{user} info:"
        last = -1
        for dice, mn, mx, count in info:
            if dice != last:
                last = dice
                result += f'\n\nd{dice}:\n'
            result += f'    [{mn}, {mx}] x {count}\n'
        await reply(message, result)
    else:
        await reply(message, f'no magic on user {user}')

@dp.message(Command("magic_clear", "mc", "clear"))
async def magic_clear(message: Message, command: CommandObject):
    group = await require_magic_master(message)
    if not group:
        return

    group_id, _ = group
    values = (command.args or "").split()
    if len(values) < 1:
        await reply(message, "There is not right amount of arguments (format: username dice1 dice2 ...)")
        return
    user = normalize_username(values[0])
    if not db.is_group_member(user, group_id):
        await reply(message, f"@{user} is not a member of this group.")
        return
    if len(values) == 1:
        db.clear_magic(user)
    else:
        dices = list(map(int, values[1:]))
        db.clear_magic(user, dices)
    await reply(message, "Abracadabra")

def revoke_magic(username):
    db.set_master_role(username, 0)

@dp.message(Command("give_me_magic")) 
async def give_me_magic(message: Message, command: CommandObject):
    db.add_user(message.from_user.username, message.from_user.id)
    if db.is_password(command.args):
        access_time = db.get_password_time(command.args)
        for name in MAGIC_HANDLERS:
            if name == message.from_user.username:
                continue
            id = db.get_user_id(name)
            if id:
                await bot.send_message(chat_id=id, text=f"{message.from_user.username} used magic for {access_time}")
        scheduler.add_job(revoke_magic,
            trigger='date',
            run_date=dateparser.parse(access_time, settings={'RELATIVE_BASE': datetime.now(pytz.utc), 'PREFER_DATES_FROM': 'future'}),
            args=(message.from_user.username,),
            timezone=pytz.utc)
        db.set_master_role(message.from_user.username, 1)
        db.delete_password(command.args)
        await reply(message, f"Magic is given to you for {access_time}")

@dp.message(Command("magic_keys", "keys"))
async def magic_keys(message: Message, command: CommandObject):
    db.add_user(message.from_user.username, message.from_user.id)
    time = command.args
    if not time:
        time = "1d"
    if message.from_user.username in MAGIC_HANDLERS:
        new_password = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(12))
        check_time = dateparser.parse(time, settings={'RELATIVE_BASE': datetime.now(pytz.utc), 'PREFER_DATES_FROM': 'future'})
        if check_time:
            db.add_password(new_password, time)
            await reply(message, new_password)
        else:
            await reply(message, "Something wrong with date format")

async def main():
    scheduler.start() 
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
