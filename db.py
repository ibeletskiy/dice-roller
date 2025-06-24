import sqlite3

PATH = '/home/ibeletskiy/dice-roller/dnd_bot.db'
#PATH = '/Users/iabeletsky/Programming/dice-roller/dnd_bot.db'

class DataBase:

    def __init__(self):
        conn = sqlite3.connect(PATH)
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
        conn.commit()
        conn.close()

    def add_user(self, username, user_id):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (username, user_id, delete_time) VALUES (?, ?, ?)", (username, user_id, 60))
        c.execute("INSERT OR IGNORE INTO magic (username, magic_used) VALUES (?, ?)", (username, 0))
        conn.commit()
        conn.close()

    def set_master_role(self, username, value):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("UPDATE users SET is_master = ? WHERE username = ?", (value, username))
        conn.commit()
        conn.close()

    def is_master(self, username):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("SELECT is_master FROM users WHERE username = ?", (username,))
        result = c.fetchone()
        conn.close()
        return result[0] if result else False

    def set_delete_time(self, username, time):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("UPDATE users SET delete_time = ? WHERE username = ?", (time, username))
        conn.commit()
        conn.close()

    def get_delete_time(self, username):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("SELECT delete_time FROM users WHERE username = ?", (username,))
        time = c.fetchone()
        conn.close()
        return time[0] if time else 60

    def get_user_id(self, username):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE username = ?", (username,))
        uid = c.fetchone()
        conn.close()
        return uid[0] if uid else None

    def add_password(self, password, time):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("INSERT INTO active_passwords (password, access_time) VALUES (?, ?)", (password, time))
        conn.commit()
        conn.close()

    def is_password(self, password):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("SELECT 1 FROM active_passwords WHERE password = ?", (password,))
        ans = c.fetchone() is not None
        conn.close()
        return ans

    def get_password_time(self, password):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("SELECT access_time FROM active_passwords WHERE password = ?", (password,))
        result = c.fetchone()
        conn.close()
        return result[0]

    def delete_password(self, password):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("DELETE FROM active_passwords WHERE password = ?", (password,))
        conn.commit()
        conn.close()

    def set_magic_rolls(self, username, dice, mn, mx, count=1):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("UPDATE magic SET magic_used = ? WHERE username = ?", (1, username))

        c.execute('''INSERT INTO magic_rolls (username, dice, mn, mx, count) 
                    VALUES (?, ?, ?, ?, ?)''', (username, dice, mn, mx, count))
        conn.commit()
        conn.close()

    def get_magic_info(self, username):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("SELECT magic_used FROM magic WHERE username = ?", (username,))
        result = c.fetchone()
        if result and result[0]:
            c.execute("select dice, mn, mx, count from   magic_rolls where username = ? order by dice, roll_id", (username,))
            return c.fetchall()
        return None

    def is_magic_user(self, username):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("SELECT magic_used FROM magic WHERE username = ?", (username,))
        result = c.fetchone()
        return result[0]

    def is_magic_roll(self, username, dice):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("SELECT magic_used FROM magic WHERE username = ?", (username,))
        result = c.fetchone()
        if result and result[0]:
            c.execute("SELECT count FROM magic_rolls WHERE username = ? AND dice = ?", (username, dice))
            count = c.fetchone()
            return count and (count[0] >= 1)
        return False

    def get_magic_min_max(self, username, dice):
        conn = sqlite3.connect(PATH)
        c = conn.cursor()
        c.execute("SELECT magic_used FROM magic WHERE username = ?", (username,))
        result = c.fetchone()
        if result and result[0]:
            c.execute("SELECT mn FROM magic_rolls WHERE username = ? AND dice = ?", (username, dice))
            mn = c.fetchone()[0]
            c.execute("SELECT mx FROM magic_rolls WHERE username = ? AND dice = ?", (username, dice))
            mx = c.fetchone()[0]
            return [mn, mx]
        return None

    def decrease_magic_rolls(self, username, dice):
        conn = sqlite3.connect(PATH)
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
        conn = sqlite3.connect(PATH)
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
