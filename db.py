import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", "dnd_bot.db")


def connect():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    return sqlite3.connect(DB_PATH)

class DataBase:

    def __init__(self):
        conn = connect()
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            user_id INTEGER,
            delete_time INTEGER DEFAULT 60,
            is_master BIT DEFAULT 0
        )''')
        #magic part
        c.execute('''CREATE TABLE IF NOT EXISTS active_passwords (
            password TEXT PRIMARY KEY,
            access_time TEXT default "1d"
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS magic (
            username TEXT PRIMARY KEY,
            magic_used BIT DEFAULT 0
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS magic_rolls (
            roll_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            dice INTEGER,
            mn INTEGER,
            mx INTEGER,
            count INTEGER
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS groups (
            group_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_by TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS group_members (
            group_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            is_master BIT DEFAULT 0,
            PRIMARY KEY (group_id, username)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS group_invites (
            group_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            invited_by TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (group_id, username)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS chat_default_groups (
            chat_id INTEGER PRIMARY KEY,
            group_id INTEGER NOT NULL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS characters (
            character_id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT NOT NULL,
            name TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS group_characters (
            group_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            approved_by TEXT NOT NULL,
            approved_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (group_id, character_id)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS group_character_requests (
            request_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            requested_by TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            resolved_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS battles (
            battle_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            master_username TEXT NOT NULL,
            status TEXT DEFAULT 'selecting',
            round_number INTEGER DEFAULT 1,
            group_message_id INTEGER,
            master_message_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS battle_entities (
            entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
            battle_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            owner_username TEXT NOT NULL,
            base_formula TEXT NOT NULL,
            current_modifier INTEGER DEFAULT 0,
            next_modifier INTEGER DEFAULT 0,
            initiative_value INTEGER,
            has_rolled BIT DEFAULT 0,
            UNIQUE (battle_id, character_id)
        )''')
        conn.commit()
        conn.close()

    def add_user(self, username, user_id):
        conn = connect()
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (username, user_id, delete_time) VALUES (?, ?, ?)", (username, user_id, 60))
        c.execute("INSERT OR IGNORE INTO magic (username, magic_used) VALUES (?, ?)", (username, 0))
        conn.commit()
        conn.close()

    def set_master_role(self, username, value):
        conn = connect()
        c = conn.cursor()
        c.execute("UPDATE users SET is_master = ? WHERE username = ?", (value, username))
        conn.commit()
        conn.close()

    def is_master(self, username):
        conn = connect()
        c = conn.cursor()
        c.execute("SELECT is_master FROM users WHERE username = ?", (username,))
        result = c.fetchone()
        conn.close()
        return result[0] if result else False

    def set_delete_time(self, username, time):
        conn = connect()
        c = conn.cursor()
        c.execute("UPDATE users SET delete_time = ? WHERE username = ?", (time, username))
        conn.commit()
        conn.close()

    def get_delete_time(self, username):
        conn = connect()
        c = conn.cursor()
        c.execute("SELECT delete_time FROM users WHERE username = ?", (username,))
        time = c.fetchone()
        conn.close()
        return time[0] if time else 60

    def get_user_id(self, username):
        conn = connect()
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE username = ?", (username,))
        uid = c.fetchone()
        conn.close()
        return uid[0] if uid else None

    def add_password(self, password, time):
        conn = connect()
        c = conn.cursor()
        c.execute("INSERT INTO active_passwords (password, access_time) VALUES (?, ?)", (password, time))
        conn.commit()
        conn.close()

    def is_password(self, password):
        conn = connect()
        c = conn.cursor()
        c.execute("SELECT 1 FROM active_passwords WHERE password = ?", (password,))
        ans = c.fetchone() is not None
        conn.close()
        return ans

    def get_password_time(self, password):
        conn = connect()
        c = conn.cursor()
        c.execute("SELECT access_time FROM active_passwords WHERE password = ?", (password,))
        result = c.fetchone()
        conn.close()
        return result[0]

    def delete_password(self, password):
        conn = connect()
        c = conn.cursor()
        c.execute("DELETE FROM active_passwords WHERE password = ?", (password,))
        conn.commit()
        conn.close()

    def set_magic_rolls(self, username, dice, mn, mx, count=1):
        conn = connect()
        c = conn.cursor()
        c.execute("UPDATE magic SET magic_used = ? WHERE username = ?", (1, username))

        c.execute('''INSERT INTO magic_rolls (username, dice, mn, mx, count) 
                    VALUES (?, ?, ?, ?, ?)''', (username, dice, mn, mx, count))
        conn.commit()
        conn.close()

    def get_magic_info(self, username):
        conn = connect()
        c = conn.cursor()
        c.execute("SELECT magic_used FROM magic WHERE username = ?", (username,))
        result = c.fetchone()
        if result and result[0]:
            c.execute("select dice, mn, mx, count from   magic_rolls where username = ? order by dice, roll_id", (username,))
            result = c.fetchall()
            conn.close()
            return result
        conn.close()
        return None

    def is_magic_user(self, username):
        conn = connect()
        c = conn.cursor()
        c.execute("SELECT magic_used FROM magic WHERE username = ?", (username,))
        result = c.fetchone()
        conn.close()
        return result[0] if result else False

    def is_magic_roll(self, username, dice):
        conn = connect()
        c = conn.cursor()
        c.execute("SELECT magic_used FROM magic WHERE username = ?", (username,))
        result = c.fetchone()
        if result and result[0]:
            c.execute("SELECT count FROM magic_rolls WHERE username = ? AND dice = ?", (username, dice))
            count = c.fetchone()
            conn.close()
            return count and (count[0] >= 1)
        conn.close()
        return False

    def get_magic_min_max(self, username, dice):
        conn = connect()
        c = conn.cursor()
        c.execute("SELECT magic_used FROM magic WHERE username = ?", (username,))
        result = c.fetchone()
        if result and result[0]:
            c.execute("SELECT mn FROM magic_rolls WHERE username = ? AND dice = ?", (username, dice))
            mn = c.fetchone()[0]
            c.execute("SELECT mx FROM magic_rolls WHERE username = ? AND dice = ?", (username, dice))
            mx = c.fetchone()[0]
            conn.close()
            return [mn, mx]
        conn.close()
        return None

    def decrease_magic_rolls(self, username, dice):
        conn = connect()
        c = conn.cursor()

        c.execute("SELECT magic_used FROM magic WHERE username = ?", (username,))
        result = c.fetchone()
        if result and result[0]:
            c.execute("select roll_id from magic_rolls where username = ? and dice = ? order by roll_id limit 1", (username, dice))
            id = c.fetchone()[0]

            c.execute('''UPDATE magic_rolls SET count = count - 1 
                WHERE roll_id = ? AND count > 0''', (id,))

            c.execute("SELECT count FROM magic_rolls WHERE roll_id = ?", (id,))
            updated_count = c.fetchone()

            if updated_count and updated_count[0] == 0:
                c.execute("delete from magic_rolls where roll_id = ?", (id,))

            c.execute("select count(*) as last from magic_rolls where username = ?", (username,))
            last = c.fetchone()

            if last and last[0] == 0:
                c.execute("UPDATE magic SET magic_used = 0 WHERE username = ?", (username,))
        conn.commit()
        conn.close()

    def clear_magic(self, username, dice=[]):
        conn = connect()
        c = conn.cursor()
        if len(dice) == 0:
            c.execute("DELETE FROM magic_rolls WHERE username = ?", (username,))
            c.execute("UPDATE magic SET magic_used = 0 WHERE username = ?", (username,))
        else:
            placeholders = ", ".join("?" for _ in dice)
            query = f"DELETE FROM magic_rolls WHERE username = ? AND dice IN ({placeholders})"
            c.execute(query, (username, *dice))
        conn.commit()
        conn.close()

    def create_group(self, name, creator):
        conn = connect()
        c = conn.cursor()
        c.execute("INSERT INTO groups (name, created_by) VALUES (?, ?)", (name, creator))
        group_id = c.lastrowid
        c.execute(
            "INSERT INTO group_members (group_id, username, is_master) VALUES (?, ?, 1)",
            (group_id, creator)
        )
        conn.commit()
        conn.close()
        return group_id

    def get_group(self, group_ref):
        conn = connect()
        c = conn.cursor()
        if str(group_ref).isdigit():
            c.execute("SELECT group_id, name FROM groups WHERE group_id = ?", (int(group_ref),))
        else:
            c.execute("SELECT group_id, name FROM groups WHERE name = ?", (group_ref,))
        result = c.fetchone()
        conn.close()
        return result

    def get_default_group(self, chat_id):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT g.group_id, g.name
                     FROM chat_default_groups cdg
                     JOIN groups g ON g.group_id = cdg.group_id
                     WHERE cdg.chat_id = ?''', (chat_id,))
        result = c.fetchone()
        conn.close()
        return result

    def set_default_group(self, chat_id, group_id):
        conn = connect()
        c = conn.cursor()
        c.execute('''INSERT INTO chat_default_groups (chat_id, group_id)
                     VALUES (?, ?)
                     ON CONFLICT(chat_id) DO UPDATE SET group_id = excluded.group_id''', (chat_id, group_id))
        conn.commit()
        conn.close()

    def get_user_groups(self, username):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT g.group_id, g.name, gm.is_master
                     FROM group_members gm
                     JOIN groups g ON g.group_id = gm.group_id
                     WHERE gm.username = ?
                     ORDER BY g.name''', (username,))
        result = c.fetchall()
        conn.close()
        return result

    def get_single_group_for_user(self, username):
        groups = self.get_user_groups(username)
        if len(groups) == 1:
            group_id, name, _ = groups[0]
            return group_id, name
        return None

    def is_group_member(self, username, group_id):
        conn = connect()
        c = conn.cursor()
        c.execute("SELECT 1 FROM group_members WHERE username = ? AND group_id = ?", (username, group_id))
        result = c.fetchone() is not None
        conn.close()
        return result

    def is_group_master(self, username, group_id):
        conn = connect()
        c = conn.cursor()
        c.execute(
            "SELECT is_master FROM group_members WHERE username = ? AND group_id = ?",
            (username, group_id)
        )
        result = c.fetchone()
        conn.close()
        return bool(result and result[0])

    def get_group_masters(self, group_id):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT username FROM group_members
                     WHERE group_id = ? AND is_master = 1
                     ORDER BY username''', (group_id,))
        result = [row[0] for row in c.fetchall()]
        conn.close()
        return result

    def get_group_members(self, group_id):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT username, is_master
                     FROM group_members
                     WHERE group_id = ?
                     ORDER BY username COLLATE NOCASE''', (group_id,))
        result = c.fetchall()
        conn.close()
        return result

    def add_group_invite(self, group_id, username, invited_by):
        conn = connect()
        c = conn.cursor()
        c.execute('''INSERT INTO group_invites (group_id, username, invited_by, status)
                     VALUES (?, ?, ?, 'pending')
                     ON CONFLICT(group_id, username) DO UPDATE
                     SET invited_by = excluded.invited_by,
                         status = 'pending',
                         created_at = CURRENT_TIMESTAMP''', (group_id, username, invited_by))
        conn.commit()
        conn.close()

    def get_group_invite(self, group_id, username):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT group_id, username, invited_by, status
                     FROM group_invites
                     WHERE group_id = ? AND username = ?''', (group_id, username))
        result = c.fetchone()
        conn.close()
        return result

    def set_group_invite_status(self, group_id, username, status):
        conn = connect()
        c = conn.cursor()
        c.execute(
            "UPDATE group_invites SET status = ? WHERE group_id = ? AND username = ?",
            (status, group_id, username)
        )
        conn.commit()
        conn.close()

    def add_group_member(self, group_id, username, is_master=0):
        conn = connect()
        c = conn.cursor()
        c.execute('''INSERT INTO group_members (group_id, username, is_master)
                     VALUES (?, ?, ?)
                     ON CONFLICT(group_id, username) DO UPDATE
                     SET is_master = MAX(group_members.is_master, excluded.is_master)''',
                  (group_id, username, is_master))
        conn.commit()
        conn.close()

    def set_group_master(self, group_id, username, is_master):
        conn = connect()
        c = conn.cursor()
        c.execute(
            "UPDATE group_members SET is_master = ? WHERE group_id = ? AND username = ?",
            (is_master, group_id, username)
        )
        conn.commit()
        conn.close()

    def count_group_masters(self, group_id):
        conn = connect()
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM group_members WHERE group_id = ? AND is_master = 1",
            (group_id,)
        )
        result = c.fetchone()[0]
        conn.close()
        return result

    def add_character(self, owner_username, name, payload):
        conn = connect()
        c = conn.cursor()
        c.execute(
            "INSERT INTO characters (owner_username, name, payload) VALUES (?, ?, ?)",
            (owner_username, name, payload)
        )
        character_id = c.lastrowid
        conn.commit()
        conn.close()
        return character_id

    def get_character(self, character_id):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT character_id, owner_username, name, payload
                     FROM characters
                     WHERE character_id = ?''', (character_id,))
        result = c.fetchone()
        conn.close()
        return result

    def get_user_characters(self, owner_username):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT character_id, name, created_at
                     FROM characters
                     WHERE owner_username = ?
                     ORDER BY character_id DESC''', (owner_username,))
        result = c.fetchall()
        conn.close()
        return result

    def get_user_characters_full(self, owner_username):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT character_id, name, payload
                     FROM characters
                     WHERE owner_username = ?
                     ORDER BY name''', (owner_username,))
        result = c.fetchall()
        conn.close()
        return result

    def is_group_character(self, group_id, character_id):
        conn = connect()
        c = conn.cursor()
        c.execute(
            "SELECT 1 FROM group_characters WHERE group_id = ? AND character_id = ?",
            (group_id, character_id)
        )
        result = c.fetchone() is not None
        conn.close()
        return result

    def add_group_character_request(self, group_id, character_id, requested_by):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT request_id FROM group_character_requests
                     WHERE group_id = ? AND character_id = ? AND status = 'pending' ''',
                  (group_id, character_id))
        pending_request = c.fetchone()
        if pending_request:
            conn.close()
            return pending_request[0]

        c.execute('''INSERT INTO group_character_requests (group_id, character_id, requested_by)
                     VALUES (?, ?, ?)''', (group_id, character_id, requested_by))
        request_id = c.lastrowid
        conn.commit()
        conn.close()
        return request_id

    def get_group_character_request(self, request_id):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT request_id, group_id, character_id, requested_by, status
                     FROM group_character_requests
                     WHERE request_id = ?''', (request_id,))
        result = c.fetchone()
        conn.close()
        return result

    def approve_group_character_request(self, request_id, approved_by):
        request = self.get_group_character_request(request_id)
        if not request:
            return None

        _, group_id, character_id, _, status = request
        if status != "pending":
            return request

        conn = connect()
        c = conn.cursor()
        c.execute('''INSERT OR IGNORE INTO group_characters (group_id, character_id, approved_by)
                     VALUES (?, ?, ?)''', (group_id, character_id, approved_by))
        c.execute('''UPDATE group_character_requests
                     SET status = 'accepted',
                         resolved_by = ?,
                         resolved_at = CURRENT_TIMESTAMP
                     WHERE request_id = ?''', (approved_by, request_id))
        conn.commit()
        conn.close()
        return request

    def decline_group_character_request(self, request_id, declined_by):
        conn = connect()
        c = conn.cursor()
        c.execute('''UPDATE group_character_requests
                     SET status = 'declined',
                         resolved_by = ?,
                         resolved_at = CURRENT_TIMESTAMP
                     WHERE request_id = ? AND status = 'pending' ''', (declined_by, request_id))
        conn.commit()
        conn.close()

    def get_group_characters(self, group_id):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT c.character_id, c.name, c.owner_username
                     FROM group_characters gc
                     JOIN characters c ON c.character_id = gc.character_id
                     WHERE gc.group_id = ?
                     ORDER BY c.name''', (group_id,))
        result = c.fetchall()
        conn.close()
        return result

    def get_group_characters_for_user(self, group_id, username):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT c.character_id, c.name, c.payload
                     FROM group_characters gc
                     JOIN characters c ON c.character_id = gc.character_id
                     WHERE gc.group_id = ? AND c.owner_username = ?
                     ORDER BY c.name''', (group_id, username))
        result = c.fetchall()
        conn.close()
        return result

    def create_battle(self, group_id, chat_id, master_username):
        conn = connect()
        c = conn.cursor()
        c.execute('''INSERT INTO battles (group_id, chat_id, master_username)
                     VALUES (?, ?, ?)''', (group_id, chat_id, master_username))
        battle_id = c.lastrowid
        conn.commit()
        conn.close()
        return battle_id

    def get_battle(self, battle_id):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT battle_id, group_id, chat_id, master_username, status,
                            round_number, group_message_id, master_message_id
                     FROM battles
                     WHERE battle_id = ?''', (battle_id,))
        result = c.fetchone()
        conn.close()
        return result

    def get_active_battle_by_group(self, group_id):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT battle_id, group_id, chat_id, master_username, status,
                            round_number, group_message_id, master_message_id
                     FROM battles
                     WHERE group_id = ? AND status != 'finished'
                     ORDER BY battle_id DESC
                     LIMIT 1''', (group_id,))
        result = c.fetchone()
        conn.close()
        return result

    def get_active_battle_by_master(self, master_username):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT battle_id, group_id, chat_id, master_username, status,
                            round_number, group_message_id, master_message_id
                     FROM battles
                     WHERE master_username = ? AND status != 'finished'
                     ORDER BY battle_id DESC
                     LIMIT 1''', (master_username,))
        result = c.fetchone()
        conn.close()
        return result

    def set_battle_messages(self, battle_id, group_message_id=None, master_message_id=None):
        conn = connect()
        c = conn.cursor()
        if group_message_id is not None:
            c.execute("UPDATE battles SET group_message_id = ? WHERE battle_id = ?", (group_message_id, battle_id))
        if master_message_id is not None:
            c.execute("UPDATE battles SET master_message_id = ? WHERE battle_id = ?", (master_message_id, battle_id))
        conn.commit()
        conn.close()

    def set_battle_status(self, battle_id, status):
        conn = connect()
        c = conn.cursor()
        c.execute("UPDATE battles SET status = ? WHERE battle_id = ?", (status, battle_id))
        conn.commit()
        conn.close()

    def finish_battle(self, battle_id):
        self.set_battle_status(battle_id, "finished")

    def add_battle_entity(self, battle_id, character_id, name, owner_username, base_formula):
        conn = connect()
        c = conn.cursor()
        c.execute('''INSERT OR IGNORE INTO battle_entities
                     (battle_id, character_id, name, owner_username, base_formula)
                     VALUES (?, ?, ?, ?, ?)''',
                  (battle_id, character_id, name, owner_username, base_formula))
        conn.commit()
        conn.close()

    def remove_battle_entity_by_character(self, battle_id, character_id):
        conn = connect()
        c = conn.cursor()
        c.execute("DELETE FROM battle_entities WHERE battle_id = ? AND character_id = ?", (battle_id, character_id))
        conn.commit()
        conn.close()

    def remove_battle_entity(self, battle_id, entity_id):
        conn = connect()
        c = conn.cursor()
        c.execute("DELETE FROM battle_entities WHERE battle_id = ? AND entity_id = ?", (battle_id, entity_id))
        conn.commit()
        conn.close()

    def is_battle_character_selected(self, battle_id, character_id):
        conn = connect()
        c = conn.cursor()
        c.execute("SELECT 1 FROM battle_entities WHERE battle_id = ? AND character_id = ?", (battle_id, character_id))
        result = c.fetchone() is not None
        conn.close()
        return result

    def get_battle_entities(self, battle_id, rolled_first=False):
        conn = connect()
        c = conn.cursor()
        order = "has_rolled DESC, initiative_value DESC, name COLLATE NOCASE" if rolled_first else "name COLLATE NOCASE"
        c.execute(f'''SELECT entity_id, character_id, name, owner_username, base_formula,
                             current_modifier, next_modifier, initiative_value, has_rolled
                      FROM battle_entities
                      WHERE battle_id = ?
                      ORDER BY {order}''', (battle_id,))
        result = c.fetchall()
        conn.close()
        return result

    def get_battle_entities_for_user(self, battle_id, username):
        conn = connect()
        c = conn.cursor()
        c.execute('''SELECT entity_id, character_id, name, owner_username, base_formula,
                            current_modifier, next_modifier, initiative_value, has_rolled
                     FROM battle_entities
                     WHERE battle_id = ? AND owner_username = ?
                     ORDER BY name COLLATE NOCASE''', (battle_id, username))
        result = c.fetchall()
        conn.close()
        return result

    def set_battle_entity_roll(self, entity_id, initiative_value):
        conn = connect()
        c = conn.cursor()
        c.execute('''UPDATE battle_entities
                     SET initiative_value = ?, has_rolled = 1
                     WHERE entity_id = ?''', (initiative_value, entity_id))
        conn.commit()
        conn.close()

    def set_battle_entity_modifier(self, entity_id, modifier_type, value):
        column = "current_modifier" if modifier_type == "current" else "next_modifier"
        conn = connect()
        c = conn.cursor()
        if modifier_type == "current":
            c.execute("SELECT current_modifier, initiative_value, has_rolled FROM battle_entities WHERE entity_id = ?", (entity_id,))
            current = c.fetchone()
            if current and current[2]:
                old_modifier, initiative_value, _ = current
                initiative_value = initiative_value - old_modifier + value
                c.execute("UPDATE battle_entities SET initiative_value = ? WHERE entity_id = ?", (initiative_value, entity_id))
        c.execute(f"UPDATE battle_entities SET {column} = ? WHERE entity_id = ?", (value, entity_id))
        conn.commit()
        conn.close()

    def advance_battle_round(self, battle_id):
        conn = connect()
        c = conn.cursor()
        c.execute('''UPDATE battle_entities
                     SET current_modifier = next_modifier,
                         next_modifier = 0,
                         initiative_value = NULL,
                         has_rolled = 0
                     WHERE battle_id = ?''', (battle_id,))
        c.execute("UPDATE battles SET round_number = round_number + 1 WHERE battle_id = ?", (battle_id,))
        conn.commit()
        conn.close()
