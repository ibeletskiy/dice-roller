import aiohttp
import asyncio
import dateparser
import logging
import sys
import os
import random
import re
import pytz
import string

from db import DataBase

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
from random import randint

from config import API_TOKEN
from config import MAGIC_HANDLERS

dp = Dispatcher()

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

db = DataBase()

scheduler = AsyncIOScheduler(timezone=pytz.utc)

def get_dices(text: str):
    dices = []
    text = text.replace(' ', '')
    print(text)
    if text[0] != '+' and text[0] != '-':
        text = "+" + text
    tokens = re.split(r'[+\-]', text)
    if len(tokens[0]) == 0:
        tokens = tokens[1:]
    pos = 0
    for token in tokens:
        if len(token) == 0:
            raise("Invalid roll format")
        multiplier = 1
        if text[pos] == '-':
            multiplier = -1
        if token.isnumeric():
            count, dice = int(token), 1
        elif token[0] not in "dDдД":
            count, dice = list(map(int, re.split(r'[dDдД]', token)))
        else:
            count, dice = 1, int(token[1:])

        count *= multiplier
        dices.append([dice, count])

        pos += len(token) + 1

    return dices

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
    db.add_user(message.from_user.username)
    await reply(message, "Welcome to DnD Dice Roller Bot!")

async def roll_pattern(message: Message, command: CommandObject, line_prefix: str, func):
    sign = lambda x: -1 if x < 0 else 1 if x > 0 else 0 
    db.add_user(message.from_user.username)
    try:
        dices = get_dices(command.args)
        text = ""
        total_sum = 0
        if len(dices) > 100:
            await reply(message, "Go fuck yourself ❤️") # ibeletskiy really should rewrite it
            return

        for dice, signed_count in dices:
            count = abs(signed_count)
            if count > 100 or dice > 100 or dice <= 0:
                await reply(message, "Go fuck yourself ❤️")
                return
            if dice != 1:
                result = []
                for i in range(count):
                    if (db.is_magic_roll(message.from_user.username, dice)):
                        print("magic!")
                        mn, mx = db.get_magic_min_max(message.from_user.username, dice)
                        if mn > mx:
                            swap(mn, mx)
                        act_mn = min(max(1, mn), dice)
                        act_mx = max(min(dice, mx), 1)
                        result.append(func(act_mn, act_mx))
                        db.decrease_magic_rolls(message.from_user.username, dice)
                    else:
                        print("no magic :(")
                        result.append(func(1, dice))
                print(f"result is {result}")
                text += f"{line_prefix}{count}d{dice}: {", ".join(list(map(str, result)))} = {sign(signed_count) * sum(result)}\n"
            else:
                text += f"{signed_count}\n"
            total_sum += sign(signed_count) * sum(result)

        if len(dices) != 1:
            text += "_____________________________________\n"
            text += f"Total sum = {total_sum}"
        await reply(message, text)
    except Exception as e:
        await reply(message, f"An error occurred. Please make sure you provided the details in the correct format")
        print(e)

@dp.message(Command("roll"))
async def roll_handler(message: Message, command: CommandObject):
    await roll_pattern(message, command, "", lambda mn, mx: randint(mn, mx))

@dp.message(Command("roll_a"))
async def roll_a_handler(message: Message, command: CommandObject):
    await roll_pattern(message, command, "max ", lambda mn, mx: max(randint(mn, mx), randint(mn, mx)))

@dp.message(Command("roll_d"))
async def roll_d_handler(message: Message, command: CommandObject):
    await roll_pattern(message, command, "min ", lambda mn, mx: min(randint(mn, mx), randint(mn, mx)))

@dp.message(Command("set_delete_time"))
async def set_delete_time_handler(message: Message, command: CommandObject):
    db.add_user(message.from_user.username)
    try:
        time = int(command.args)
        db.set_delete_time(message.from_user.username, time)
    except Exception as e:
        await reply(message, "An error occurred. Please make sure you provided the details in the correct format")

@dp.message(Command("magic_set_dice"))
async def magic_set_dice(message: Message, command: CommandObject):
    if db.is_master(message.from_user.username):
        values = command.args.split()
        if len(values) < 4 or len(values) > 5:
            await reply(message, "There is not right amount of arguments (format: username, dice, min, max, count)")
            return
        if (len(values) == 4):
            user, dice, mn, mx = values
            count = 1
        else:
            user, dice, mn, mx, count = values
        db.set_magic_rolls(user, dice, mn, mx, count)
        await reply(message, "Abracadabra")

@dp.message(Command("magic_clear"))
async def magic_clear(message: Message, command: CommandObject):
    if db.is_master(message.from_user.username):
        values = command.args.split()
        if len(values) == 1:
            db.clear_magic(command.args)
        else:
            dices = list(map(int, values[1:]))
            db.clear_magic(values[0], dices)
        await reply(message, "Abracadabra")

def revoke_magic(username):
    db.set_master_role(username, 0)

@dp.message(Command("give_me_magic")) 
async def give_me_magic(message: Message, command: CommandObject):
    db.add_user(message.from_user.username)
    if db.is_password(command.args):
        access_time = db.get_password_time(command.args)
        print(datetime.now(pytz.utc))
        print(dateparser.parse(access_time, settings={'RELATIVE_BASE': datetime.now(pytz.utc), 'PREFER_DATES_FROM': 'future'}))
        scheduler.add_job(revoke_magic,
            trigger='date',
            run_date=dateparser.parse(access_time, settings={'RELATIVE_BASE': datetime.now(pytz.utc), 'PREFER_DATES_FROM': 'future'}),
            args=(message.from_user.username,),
            timezone=pytz.utc)
        db.set_master_role(message.from_user.username, 1)
        db.delete_password(command.args)
        await reply(message, f"Magic is given to you for {access_time}")

@dp.message(Command("magic_keys"))
async def magic_keys(message: Message, command: CommandObject):
    db.add_user(message.from_user.username)
    time = command.args
    if not time:
        time = "1d"
    if message.from_user.username in MAGIC_HANDLERS:
        new_password = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(12))
        check_time = dateparser.parse(time, settings={'RELATIVE_BASE': datetime.now(pytz.utc), 'PREFER_DATES_FROM': 'future'})
        print(datetime.now(pytz.utc))
        print(check_time)
        if check_time:
            db.add_password(new_password, time)
            await reply(message, new_password)
        else:
            await reply(message, "Something wrong with date format")

@dp.message()
async def main():
    scheduler.start() 
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
