import asyncio
import sqlite3
import datetime
import hashlib
import secrets
import time
import os
import re
from typing import Optional, Dict, List, Tuple, Any
from functools import wraps

from aiogram import Bot, Dispatcher, F, types, BaseMiddleware
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from contextlib import contextmanager

# ==================== НАЛАШТУВАННЯ ====================
TOKEN = "8559036924:AAGV-L4qIWjGCACQLfGhPGVzBu0T0pqeC40"

DB_PATH = "lyceum_v3.db"

DAYS_ORDER = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця"]
DAYS_UA = {
    "Monday": "Понеділок",
    "Tuesday": "Вівторок",
    "Wednesday": "Середа",
    "Thursday": "Четвер",
    "Friday": "П'ятниця",
    "Saturday": "Субота",
    "Sunday": "Неділя"
}


def require_auth(role=None):
    """Декоратор для перевірки авторизації та ролі"""
    def decorator(func):
        @wraps(func)
        async def wrapper(message: types.Message, *args, **kwargs):
            user = get_user_by_tg_id(message.from_user.id)
            
            # Якщо користувач не авторизований
            if not user or user.get("role") == "unauthorized":
                await message.answer(
                    "🔐 <b>Спочатку авторизуйтесь</b>\n\n"
                    "Використайте команду /start для входу в систему.",
                    parse_mode="HTML"
                )
                return
            
            # Якщо потрібна конкретна роль
            if role and user.get("role") != role:
                await message.answer(
                    f"⛔ <b>Доступ заборонено!</b>\n\n"
                    f"Ця функція доступна тільки для {role}.",
                    parse_mode="HTML"
                )
                return
            
            return await func(message, *args, **kwargs)
        return wrapper
    return decorator


# ==================== КЛАС ДЛЯ ПЕРЕГЛЯДУ РОЗКЛАДУ ====================
class ScheduleView:
    def __init__(self):
        self.user_day_index = {}

    def get_today_ua(self) -> str:
        return DAYS_UA.get(datetime.datetime.now().strftime("%A"), "Понеділок")

    def get_current_day(self, user_id: int) -> str:
        if user_id not in self.user_day_index:
            today = self.get_today_ua()
            if today in DAYS_ORDER:
                self.user_day_index[user_id] = DAYS_ORDER.index(today)
            else:
                self.user_day_index[user_id] = 0
        return DAYS_ORDER[self.user_day_index[user_id]]

    def next_day(self, user_id: int) -> str:
        if user_id not in self.user_day_index:
            self.get_current_day(user_id)
        if self.user_day_index[user_id] < len(DAYS_ORDER) - 1:
            self.user_day_index[user_id] += 1
        return DAYS_ORDER[self.user_day_index[user_id]]

    def prev_day(self, user_id: int) -> str:
        if user_id not in self.user_day_index:
            self.get_current_day(user_id)
        if self.user_day_index[user_id] > 0:
            self.user_day_index[user_id] -= 1
        return DAYS_ORDER[self.user_day_index[user_id]]

    def set_today(self, user_id: int) -> str:
        today = self.get_today_ua()
        if today in DAYS_ORDER:
            self.user_day_index[user_id] = DAYS_ORDER.index(today)
        else:
            self.user_day_index[user_id] = 0
        return DAYS_ORDER[self.user_day_index[user_id]]

    def reset(self, user_id: int):
        self.user_day_index.pop(user_id, None)


schedule_view = ScheduleView()


class AntiDuplicateMiddleware(BaseMiddleware):
    def __init__(self):
        self.last_message = {}

    async def __call__(self, handler, event, data):
        if not isinstance(event, types.Message):
            return await handler(event, data)

        user_id = event.from_user.id
        text = event.text or ""

        if self.last_message.get(user_id) == text:
            return

        self.last_message[user_id] = text
        return await handler(event, data)


# ==================== FSM ====================
class AuthStates(StatesGroup):
    waiting_for_login = State()
    waiting_for_password = State()


class AdminStates(StatesGroup):
    waiting_for_teacher_login = State()
    waiting_for_teacher_password = State()
    waiting_for_teacher_full_name = State()
    waiting_for_teacher_subject = State()
    waiting_for_teacher_group = State()

    waiting_for_student_full_name = State()
    waiting_for_student_login = State()
    waiting_for_student_group = State()
    waiting_for_student_password = State()

    waiting_for_substitution_day = State()
    waiting_for_substitution_lesson = State()
    waiting_for_substitution_group = State()
    waiting_for_substitution_teacher = State()
    waiting_for_substitution_room = State()

    waiting_for_schedule_day = State()
    waiting_for_schedule_lesson = State()
    waiting_for_schedule_subject = State()
    waiting_for_schedule_group = State()
    waiting_for_schedule_teacher = State()
    waiting_for_schedule_room = State()

    waiting_for_schedule_delete_day = State()
    waiting_for_schedule_delete_lesson = State()
    waiting_for_schedule_delete_group = State()
    waiting_for_student_subgroup = State()
    waiting_for_schedule_subgroup = State()

    waiting_for_schedule_clone_source = State()
    waiting_for_schedule_clone_target = State()
    waiting_for_teacher_extra_days = State()


class TeacherSearchStates(StatesGroup):
    waiting_for_teacher_name = State()


class SettingsStates(StatesGroup):
    waiting_for_display_mode = State()


class ImportStates(StatesGroup):
    waiting_for_csv = State()
    waiting_for_json = State()
    waiting_for_schedule_clone_source = State()
    waiting_for_schedule_clone_target = State()


# ==================== ІНІЦІАЛІЗАЦІЯ ====================
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
dp.message.middleware(AntiDuplicateMiddleware())


@contextmanager
def db_connection():
    """Контекстний менеджер для роботи з базою даних"""
    conn = None
    try:
        conn = get_db_connection()
        yield conn
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


# ==================== БАЗА ДАНИХ ====================
def get_db_connection():
    max_retries = 5
    for i in range(max_retries):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=20.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            return conn
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and i < max_retries - 1:
                time.sleep(0.5)
                continue
            raise


def init_db():
    """Ініціалізує базу даних"""
    create_class_user_tables()

    conn = get_db_connection()
    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                login TEXT UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'unauthorized',
                full_name TEXT NOT NULL,
                subject TEXT,
                group_name TEXT,
                subgroup TEXT,
                display_mode TEXT DEFAULT 'auto'
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                lesson_num INTEGER NOT NULL,
                subject TEXT NOT NULL,
                group_name TEXT NOT NULL,
                subgroup TEXT,
                teacher TEXT NOT NULL,
                room TEXT,
                is_extra INTEGER DEFAULT 0 NOT NULL
            )
        """)

        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_schedule_unique
            ON schedule(day, lesson_num, group_name, subgroup)
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS substitutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                lesson_num INTEGER NOT NULL,
                group_name TEXT NOT NULL,
                subgroup TEXT,
                old_subject TEXT,
                old_teacher TEXT,
                old_room TEXT,
                new_subject TEXT,
                new_teacher TEXT,
                new_room TEXT,
                created_at TEXT NOT NULL
            )
        """)

        conn.commit()
    except Exception as e:
        print(f"Помилка при створенні таблиць: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

    create_default_admin()


def create_default_admin():
    """Створює адміністратора за замовчуванням"""
    max_retries = 3
    for attempt in range(max_retries):
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='admins'")
            if not cur.fetchone():
                return

            cur.execute("SELECT COUNT(*) AS cnt FROM admins")
            count = cur.fetchone()["cnt"]

            if count == 0:
                admin_password = hash_password("admin123")
                cur.execute("""
                    INSERT INTO admins (tg_id, username, login, password_hash, full_name, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (1, "admin", "admin", admin_password, "👑 Головний адміністратор",
                      datetime.datetime.now().isoformat(timespec="seconds")))

                cur.execute("""
                    INSERT OR REPLACE INTO users (tg_id, username, login, password_hash, role, full_name, group_name, subgroup)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (1, "admin", "admin", admin_password, "admin", "👑 Головний адміністратор", "", None))

                conn.commit()
                print("✅ Створено адміністратора за замовчуванням (логін: admin, пароль: admin123)")
                return
            else:
                # Синхронізуємо існуючих адмінів з users таблицею
                cur.execute("SELECT tg_id, username, login, password_hash, full_name FROM admins")
                admins = cur.fetchall()
                for admin in admins:
                    cur.execute("""
                        INSERT OR REPLACE INTO users (tg_id, username, login, password_hash, role, full_name, group_name, subgroup)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (admin["tg_id"], admin["username"], admin["login"], admin["password_hash"], 
                          "admin", admin["full_name"], "", None))
                conn.commit()
                return

        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < max_retries - 1:
                print(f"⚠️ База даних заблокована, спроба {attempt + 1} з {max_retries}...")
                time.sleep(1)
                continue
            else:
                print(f"Помилка при створенні адміна: {e}")
        except Exception as e:
            print(f"Помилка при створенні адміна: {e}")
        finally:
            if conn:
                conn.close()


def get_user_by_login(login: str) -> Optional[Dict]:
    """Шукає користувача в усіх таблицях (учні, вчителі, адміни)"""
    try:
        # Спочатку шукаємо в users
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT tg_id, username, login, password_hash, role, full_name, subject, group_name, subgroup
                FROM users 
                WHERE login = ?
            """, (login,))
            row = cur.fetchone()
            if row:
                return dict(row)

        # Якщо не знайшли, шукаємо в інших таблицях
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT tg_id, username, login, password_hash, 'admin' as role, 
                   full_name, NULL as subject, NULL as group_name, NULL as subgroup
            FROM admins WHERE login = ?
        """, (login,))
        row = cur.fetchone()
        if row:
            conn.close()
            return dict(row)

        cur.execute("""
            SELECT tg_id, username, login, password_hash, 'teacher' as role, 
                   full_name, subject, class_teacher as group_name, NULL as subgroup
            FROM teachers WHERE login = ?
        """, (login,))
        row = cur.fetchone()
        if row:
            conn.close()
            return dict(row)

        classes = get_all_classes()
        for class_name in classes:
            table_name = sanitize_table_name(class_name)
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
            if not cur.fetchone():
                continue

            cur.execute(f"""
                SELECT tg_id, username, login, password_hash, 'student' as role, 
                       full_name, '{class_name}' as group_name, subgroup
                FROM {table_name} WHERE login = ?
            """, (login,))
            row = cur.fetchone()
            if row:
                conn.close()
                return dict(row)

        conn.close()
        return None
    except Exception as e:
        print(f"Помилка в get_user_by_login: {e}")
        return None


def get_user_by_tg_id(tg_id: int) -> Optional[Dict]:
    """Отримує користувача за tg_id"""
    try:
        # Спочатку шукаємо в users
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT tg_id, username, login, password_hash, role, full_name, subject, group_name, subgroup
            FROM users 
            WHERE tg_id = ?
        """, (tg_id,))
        row = cur.fetchone()
        
        if row:
            conn.close()
            return dict(row)
        
        # Якщо не знайшли, шукаємо в інших таблицях
        cur.execute("""
            SELECT tg_id, username, login, password_hash, 'admin' as role, 
                   full_name, NULL as subject, NULL as group_name, NULL as subgroup
            FROM admins WHERE tg_id = ?
        """, (tg_id,))
        row = cur.fetchone()
        if row:
            conn.close()
            return dict(row)

        cur.execute("""
            SELECT tg_id, username, login, password_hash, 'teacher' as role, 
                   full_name, subject, class_teacher as group_name, NULL as subgroup
            FROM teachers WHERE tg_id = ?
        """, (tg_id,))
        row = cur.fetchone()
        if row:
            conn.close()
            return dict(row)

        classes = get_all_classes()
        for class_name in classes:
            table_name = sanitize_table_name(class_name)
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
            if not cur.fetchone():
                continue

            cur.execute(f"""
                SELECT tg_id, username, login, password_hash, 'student' as role, 
                       full_name, '{class_name}' as group_name, subgroup
                FROM {table_name} WHERE tg_id = ?
            """, (tg_id,))
            row = cur.fetchone()
            if row:
                conn.close()
                return dict(row)

        conn.close()
        return None
    except Exception as e:
        print(f"Помилка в get_user_by_tg_id: {e}")
        return None


def register_user(tg_id: int, login: str, password_hash: str, role: str,
                  full_name: str, username: str = None, subject: str = None,
                  group_name: str = None, subgroup: str = None):
    """Реєструє або оновлює користувача в таблиці users"""
    try:
        with db_connection() as conn:
            cur = conn.cursor()
            # Використовуємо ON CONFLICT(login) оскільки login має UNIQUE constraint
            cur.execute("""
                INSERT INTO users (tg_id, username, login, password_hash, role, full_name, subject, group_name, subgroup)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(login) DO UPDATE SET
                    tg_id = COALESCE(excluded.tg_id, users.tg_id),
                    username = COALESCE(excluded.username, users.username),
                    password_hash = COALESCE(excluded.password_hash, users.password_hash),
                    role = COALESCE(excluded.role, users.role),
                    full_name = COALESCE(excluded.full_name, users.full_name),
                    subject = COALESCE(excluded.subject, users.subject),
                    group_name = COALESCE(excluded.group_name, users.group_name),
                    subgroup = COALESCE(excluded.subgroup, users.subgroup)
            """, (
                tg_id, username, login, password_hash, role, full_name, subject, group_name, subgroup
            ))
    except Exception as e:
        print(f"Помилка в register_user: {e}")


# ==================== ДОПОМІЖНІ ФУНКЦІЇ ====================
def get_corps_for_class(group_name: str, room: str = None) -> str:
    import re
    match = re.match(r'(\d+)', group_name)
    class_num = int(match.group(1)) if match else 0

    if 5 <= class_num <= 6:
        return "2"

    return "1"


def get_lesson_time(lesson_num: int, corps: str = "1") -> str:
    if corps == "1":
        times = {
            1: ("9:00", "9:40"),
            2: ("9:45", "10:25"),
            3: ("10:45", "11:25"),
            4: ("11:30", "12:10"),
            5: ("13:00", "13:40"),
            6: ("13:45", "14:25"),
            7: ("14:45", "15:25"),
            8: ("15:30", "16:10")
        }
    else:
        times = {
            1: ("9:00", "9:40"),
            2: ("9:45", "10:25"),
            3: ("11:15", "11:55"),
            4: ("12:00", "12:40"),
            5: ("13:00", "13:40"),
            6: ("13:45", "14:25"),
            7: ("14:45", "15:25"),
            8: ("15:30", "16:10")
        }

    if lesson_num in times:
        return f"{times[lesson_num][0]} – {times[lesson_num][1]}"
    return ""


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    hash_value = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{hash_value}:{salt}"


def verify_password(password: str, hashed: str) -> bool:
    if ":" not in hashed:
        return False
    hash_val, salt = hashed.split(":", 1)
    check = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return hash_val == check


def get_user_role(tg_id: int) -> str:
    user = get_user_by_tg_id(tg_id)
    return user["role"] if user else "unauthorized"


def get_user_full_name(tg_id: int) -> str:
    user = get_user_by_tg_id(tg_id)
    return user["full_name"] if user else "Користувач"


def get_user_group(tg_id: int) -> Optional[str]:
    user = get_user_by_tg_id(tg_id)
    return user["group_name"] if user else None


def get_user_subgroup(tg_id: int) -> Optional[str]:
    user = get_user_by_tg_id(tg_id)
    return user["subgroup"] if user else None


def get_all_classes() -> List[str]:
    classes = []

    for letter in ["А", "Б", "В"]:
        classes.append(f"5-{letter}")

    for letter in ["А", "Б", "В", "Г"]:
        classes.append(f"6-{letter}")

    for letter in ["А", "Б", "В", "Г", "Д"]:
        classes.append(f"7-{letter}")

    for letter in ["А", "Б", "В", "Г", "Д", "М"]:
        classes.append(f"8-{letter}")

    for letter in ["А", "Б", "В", "Г"]:
        classes.append(f"9-{letter}")

    special_classes = ["ІТ-1", "ІТ-2", "Т-1", "Т-2", "П-1", "П-2", "Кі-2"]
    classes.extend(special_classes)

    return sorted(classes)


def get_all_teachers() -> List[Tuple[str, str]]:
    try:
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT full_name, subject 
                FROM users 
                WHERE role = 'teacher'
                ORDER BY full_name
            """)
            return [(row["full_name"], row["subject"] or "") for row in cur.fetchall()]
    except Exception as e:
        print(f"Помилка отримання списку вчителів: {e}")
        return []


def sanitize_table_name(class_name: str) -> str:
    translit_map = {
        'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'H', 'Ґ': 'G',
        'Д': 'D', 'Е': 'E', 'Є': 'Ye', 'Ж': 'Zh', 'З': 'Z',
        'И': 'Y', 'І': 'I', 'Ї': 'Yi', 'Й': 'Y', 'К': 'K',
        'Л': 'L', 'М': 'M', 'Н': 'N', 'О': 'O', 'П': 'P',
        'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U', 'Ф': 'F',
        'Х': 'Kh', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Shch',
        'Ю': 'Yu', 'Я': 'Ya'
    }

    result = class_name
    for cyr, lat in translit_map.items():
        result = result.replace(cyr, lat)

    result = result.replace('-', '_')
    return f"users_{result}"


def create_class_user_tables():
    conn = get_db_connection()
    try:
        cur = conn.cursor()

        classes = get_all_classes()

        for class_name in classes:
            table_name = sanitize_table_name(class_name)

            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER UNIQUE,
                    username TEXT,
                    login TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    subgroup TEXT,
                    created_at TEXT NOT NULL
                )
            """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS teachers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER UNIQUE,
                username TEXT,
                login TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                subject TEXT,
                class_teacher TEXT,
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER UNIQUE,
                username TEXT,
                login TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        conn.commit()
        print(f"✅ Створено {len(classes)} таблиць для учнів + таблиці для вчителів та адмінів")

    except Exception as e:
        print(f"Помилка при створенні таблиць класів: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def add_lesson_to_db(day: str, lesson_num: int, subject: str, group_name: str,
                     teacher: str, room: str, subgroup: str = None, is_extra: int = 0):
    """Додає урок до загальної таблиці schedule"""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO schedule (day, lesson_num, subject, group_name, subgroup, teacher, room, is_extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day, lesson_num, group_name, subgroup) 
            DO UPDATE SET subject = excluded.subject, teacher = excluded.teacher, room = excluded.room
        """, (day, lesson_num, subject, group_name, subgroup, teacher, room, is_extra))
        conn.commit()
    except Exception as e:
        print(f"Помилка додавання уроку: {e}")
    finally:
        conn.close()


def delete_lesson_from_db(day: str, lesson_num: int, group_name: str, subgroup: str = None) -> int:
    """Видаляє урок з загальної таблиці schedule"""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if subgroup:
            cur.execute("""
                DELETE FROM schedule
                WHERE day = ? AND lesson_num = ? AND group_name = ? AND subgroup = ?
            """, (day, lesson_num, group_name, subgroup))
        else:
            cur.execute("""
                DELETE FROM schedule
                WHERE day = ? AND lesson_num = ? AND group_name = ? AND subgroup IS NULL
            """, (day, lesson_num, group_name))

        deleted = cur.rowcount
        conn.commit()
        return deleted
    except Exception as e:
        print(f"Помилка видалення уроку: {e}")
        return 0
    finally:
        conn.close()


def add_substitution_to_db(day: str, lesson_num: int, group_name: str, subgroup: str,
                           old_subject: str, old_teacher: str, old_room: str,
                           new_subject: str, new_teacher: str, new_room: str):
    """Додає заміну до таблиці substitutions"""
    conn = get_db_connection()
    cur = conn.cursor()

    created_at = datetime.datetime.now().isoformat(timespec="seconds")

    try:
        cur.execute("""
            INSERT INTO substitutions (
                day, lesson_num, group_name, subgroup,
                old_subject, old_teacher, old_room,
                new_subject, new_teacher, new_room, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (day, lesson_num, group_name, subgroup,
              old_subject, old_teacher, old_room,
              new_subject, new_teacher, new_room, created_at))
        conn.commit()
    except Exception as e:
        print(f"Помилка додавання заміни: {e}")
    finally:
        conn.close()


def get_schedule_for_class(class_name: str, day: str) -> List[Tuple]:
    """Отримує розклад для конкретного класу на певний день"""
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT lesson_num, subject, teacher, room, subgroup
        FROM schedule
        WHERE group_name = ? AND day = ?
        ORDER BY lesson_num, subgroup
    """, (class_name, day))

    rows = cur.fetchall()
    conn.close()

    lessons = []
    for row in rows:
        group_display = class_name
        if row["subgroup"]:
            group_display = f"{class_name} (підгр.{row['subgroup']})"
        lessons.append((row["lesson_num"], row["subject"], group_display, row["teacher"], row["room"]))

    return lessons


async def notify_teacher(teacher_name: str, substitution_info: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT tg_id FROM users
        WHERE full_name = ? AND role = 'teacher'
    """, (teacher_name,))
    teacher = cur.fetchone()
    conn.close()

    if teacher and teacher["tg_id"]:
        try:
            await bot.send_message(
                teacher["tg_id"],
                substitution_info,
                parse_mode="HTML"
            )
        except Exception:
            pass


# ==================== КЛАВІАТУРИ ====================
def get_main_menu(role: str):
    if role == "admin":
        kb = [
            [KeyboardButton(text="👨‍🏫 Вчителі"), KeyboardButton(text="👨‍🎓 Учні")],
            [KeyboardButton(text="📚 Розклад"), KeyboardButton(text="🔄 Заміни")],
            [KeyboardButton(text="🚪 Вийти")]
        ]
    elif role == "teacher":
        kb = [
            [KeyboardButton(text="📅 Мій розклад"), KeyboardButton(text="🔄 Заміни")],
            [KeyboardButton(text="❓ FAQ")],
            [KeyboardButton(text="🚪 Вийти")]
        ]
    elif role == "student":
        kb = [
            [KeyboardButton(text="📅 Мій розклад"), KeyboardButton(text="👨‍🏫 Де вчитель?")],
            [KeyboardButton(text="📘 Додаткові"), KeyboardButton(text="🔄 Заміни")],
            [KeyboardButton(text="❓ FAQ")],
            [KeyboardButton(text="🚪 Вийти")]
        ]
    else:
        kb = [[KeyboardButton(text="🚪 Вийти")]]

    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def get_cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Скасувати")]],
        resize_keyboard=True
    )


def get_schedule_keyboard(prefix: str, device_type: str = 'desktop'):
    if device_type == 'mobile':
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="◀️", callback_data=f"{prefix}_prev"),
                InlineKeyboardButton(text="📅", callback_data=f"{prefix}_today"),
                InlineKeyboardButton(text="▶️", callback_data=f"{prefix}_next")
            ],
            [
                InlineKeyboardButton(text="❌", callback_data=f"{prefix}_close")
            ]
        ])
    elif device_type == 'tablet':
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="◀️ Попередній", callback_data=f"{prefix}_prev"),
                InlineKeyboardButton(text="📅 Сьогодні", callback_data=f"{prefix}_today"),
                InlineKeyboardButton(text="Наступний ▶️", callback_data=f"{prefix}_next")
            ],
            [
                InlineKeyboardButton(text="❌ Закрити", callback_data=f"{prefix}_close")
            ]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="◀️ Попередній день", callback_data=f"{prefix}_prev"),
                InlineKeyboardButton(text="Наступний день ▶️", callback_data=f"{prefix}_next")
            ],
            [
                InlineKeyboardButton(text="📅 Сьогодні", callback_data=f"{prefix}_today"),
                InlineKeyboardButton(text="❌ Закрити", callback_data=f"{prefix}_close")
            ]
        ])


# ==================== ФОРМАТУВАННЯ ====================
def format_schedule_for_day(day: str, lessons: List[Tuple], device_type: str = 'desktop') -> str:
    if not lessons:
        return f"📭 <b>{day}</b>\n\n✨ Немає уроків на цей день ✨"

    lessons_by_corps = {"1": [], "2": []}

    for lesson, subject, group, teacher, room in lessons:
        corps = get_corps_for_class(group, room)
        lessons_by_corps[corps].append((lesson, subject, group, teacher, room))

    text = f"📅 <b>{day}</b>\n"

    for corps in ["1", "2"]:
        if lessons_by_corps[corps]:
            text += f"🏫 <b>{corps} КОРПУС</b>\n"
            text += "━━━━━━━━━━━━━━━━━━━\n"
            lessons_sorted = sorted(lessons_by_corps[corps], key=lambda x: x[0])

            prev_lesson = None

            for lesson, subject, group, teacher, room in lessons_sorted:
                time_range = get_lesson_time(lesson, corps)

                if corps == "1" and prev_lesson == 4 and lesson == 5:
                    text += "\n🍽️ <b>ВЕЛИКА ПЕРЕРВА (12:10 - 13:00)</b> 🍽️\n\n"

                if corps == "2" and prev_lesson == 2 and lesson == 3:
                    text += "\n🍽️ <b>ВЕЛИКА ПЕРЕРВА (10:25 - 11:15)</b> 🍽️\n\n"

                if device_type == 'mobile':
                    text += (
                        f"<b>{lesson}</b> {time_range}\n"
                        f"📖 {subject}\n"
                        f"👥 {group}\n"
                        f"👨‍🏫 {teacher[:20]}{'…' if len(teacher) > 20 else ''}\n"
                        f"🚪 {room}\n"
                        f"────────\n"
                    )
                elif device_type == 'tablet':
                    text += (
                        f"<b>{lesson}-й урок</b> | {time_range}\n"
                        f"📖 {subject} | 👥 {group}\n"
                        f"👨‍🏫 {teacher} | 🚪 {room}\n\n"
                    )
                else:
                    text += (
                        f"<b>{lesson}-й урок</b> | <i>{time_range}</i>\n"
                        f"📖 {subject}\n"
                        f"👥 {group}\n"
                        f"👨‍🏫 {teacher}\n"
                        f"🚪 {room}\n\n"
                    )
                prev_lesson = lesson
    return text


def get_current_lesson_number(now: datetime.datetime) -> Optional[int]:
    current_minutes = now.hour * 60 + now.minute

    ranges = {
        1: (9 * 60, 9 * 60 + 40),
        2: (9 * 60 + 45, 10 * 60 + 25),
        3: (10 * 60 + 45, 11 * 60 + 25),
        4: (11 * 60 + 30, 12 * 60 + 10),
        5: (13 * 60, 13 * 60 + 40),
        6: (13 * 60 + 45, 14 * 60 + 25),
        7: (14 * 60 + 45, 15 * 60 + 25),
        8: (15 * 60 + 30, 16 * 60 + 10),
    }

    for lesson_num, (start, end) in ranges.items():
        if start <= current_minutes <= end:
            return lesson_num
    return None


def parse_group_and_subgroup(group_text: str):
    subgroup = None
    group_name = group_text

    if "(підгр.1)" in group_text:
        subgroup = "1"
        group_name = group_text.replace(" (підгр.1)", "")
    elif "(підгр.2)" in group_text:
        subgroup = "2"
        group_name = group_text.replace(" (підгр.2)", "")
    elif "(весь клас)" in group_text:
        group_name = group_text.replace(" (весь клас)", "")

    return group_name, subgroup


def detect_device_type(user_agent: str) -> str:
    user_agent = user_agent.lower()

    mobile_keywords = ['android', 'iphone', 'ipod', 'blackberry', 'windows phone', 'mobile']
    tablet_keywords = ['ipad', 'tablet', 'kindle', 'silk']

    if any(keyword in user_agent for keyword in tablet_keywords):
        return 'tablet'
    elif any(keyword in user_agent for keyword in mobile_keywords):
        return 'mobile'
    else:
        return 'desktop'


# ==================== АВТОРИЗАЦІЯ ====================
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    user = get_user_by_tg_id(message.from_user.id)
    if user and user.get("role") != "unauthorized":
        text = f"🌟 <b>Вітаємо, {user['full_name']}!</b>\n\n"
        text += f"📌 Роль: <b>{user['role'].upper()}</b>\n"
        if user.get("group_name"):
            text += f"🏫 Клас: <b>{user['group_name']}</b>\n"
        if user.get("subgroup"):
            text += f"🔹 Підгрупа: <b>{user['subgroup']}</b>\n"
        await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu(user["role"]))
        return

    await state.clear()
    await message.answer(
        "🔐 <b>Ласкаво просимо!</b>\n\nВведіть логін:",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AuthStates.waiting_for_login)


@dp.message(AuthStates.waiting_for_login)
async def process_login(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Авторизацію скасовано.", reply_markup=ReplyKeyboardRemove())
        return

    login = message.text.strip()
    await state.update_data(login=login)
    await state.set_state(AuthStates.waiting_for_password)
    await message.answer("🔒 Введіть пароль:", parse_mode="HTML")


@dp.message(AuthStates.waiting_for_password)
async def process_password(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Авторизацію скасовано.", reply_markup=ReplyKeyboardRemove())
        return

    password = message.text.strip()
    data = await state.get_data()
    login = data.get("login")

    user = get_user_by_login(login)
    if not user or not verify_password(password, user["password_hash"]):
        await message.answer("❌ Невірний логін або пароль. Спробуйте ще раз.")
        return

    # Реєструємо в users з tg_id
    register_user(
        tg_id=message.from_user.id,
        username=message.from_user.username,
        login=user["login"],
        password_hash=user["password_hash"],
        role=user["role"],
        full_name=user["full_name"],
        subject=user.get("subject"),
        group_name=user.get("group_name"),
        subgroup=user.get("subgroup")
    )

    await state.clear()
    await message.answer(
        f"✅ Вхід виконано.\n\n👤 <b>{user['full_name']}</b>",
        parse_mode="HTML",
        reply_markup=get_main_menu(user["role"])
    )


@dp.message(F.text == "🚪 Вийти")
async def logout(message: types.Message, state: FSMContext):
    schedule_view.reset(message.from_user.id)
    await state.clear()
    await message.answer(
        "👋 Ви вийшли з системи.\nДля повторного входу використайте /start",
        reply_markup=ReplyKeyboardRemove()
    )


# ==================== АДМІН: ВЧИТЕЛІ ====================
@dp.message(F.text == "👨‍🏫 Вчителі")
@require_auth(role="admin")
async def manage_teachers(message: types.Message):
    kb = [
        [KeyboardButton(text="➕ Додати вчителя")],
        [KeyboardButton(text="📋 Список вчителів")],
        [KeyboardButton(text="🔙 Назад")]
    ]
    await message.answer(
        "👨‍🏫 <b>Керування вчителями</b>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(F.text == "➕ Додати вчителя")
@require_auth(role="admin")
async def add_teacher_start(message: types.Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_teacher_login)
    await message.answer("Введіть логін вчителя:", reply_markup=get_cancel_keyboard())


@dp.message(AdminStates.waiting_for_teacher_login)
@require_auth(role="admin")
async def add_teacher_login(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    login = message.text.strip()
    if get_user_by_login(login):
        await message.answer("❌ Такий логін уже існує. Введіть інший.")
        return

    await state.update_data(login=login)
    await state.set_state(AdminStates.waiting_for_teacher_password)
    await message.answer("Введіть пароль:")


@dp.message(AdminStates.waiting_for_teacher_password)
@require_auth(role="admin")
async def add_teacher_password(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    await state.update_data(password=message.text.strip())
    await state.set_state(AdminStates.waiting_for_teacher_full_name)
    await message.answer("Введіть ПІБ вчителя:")


@dp.message(AdminStates.waiting_for_teacher_full_name)
@require_auth(role="admin")
async def add_teacher_full_name(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    await state.update_data(full_name=message.text.strip())
    await state.set_state(AdminStates.waiting_for_teacher_subject)
    await message.answer("Введіть предмет:")


@dp.message(AdminStates.waiting_for_teacher_subject)
@require_auth(role="admin")
async def add_teacher_subject(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    await state.update_data(subject=message.text.strip())
    await state.set_state(AdminStates.waiting_for_teacher_extra_days)
    await message.answer("Введіть дні додаткових (через кому, напр: Понеділок, Середа):")


@dp.message(AdminStates.waiting_for_teacher_extra_days)
@require_auth(role="admin")
async def add_teacher_extra(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    extras = [d.strip() for d in message.text.split(",") if d.strip()]
    await state.update_data(extra_days=extras)
    await state.set_state(AdminStates.waiting_for_teacher_group)
    await message.answer("Введіть класне керівництво або 'немає':")


@dp.message(AdminStates.waiting_for_teacher_group)
@require_auth(role="admin")
async def add_teacher_group(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    data = await state.get_data()
    group = None if message.text.strip().lower() == "немає" else message.text.strip()
    temp_tg_id = int(hashlib.md5(data["login"].encode()).hexdigest()[:8], 16)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO teachers (tg_id, username, login, password_hash, full_name, subject, class_teacher, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(login) DO UPDATE SET
            tg_id = excluded.tg_id,
            username = excluded.username,
            password_hash = excluded.password_hash,
            full_name = excluded.full_name,
            subject = excluded.subject,
            class_teacher = excluded.class_teacher
    """, (
        temp_tg_id,
        None,
        data["login"],
        hash_password(data["password"]),
        data["full_name"],
        data["subject"],
        group,
        datetime.datetime.now().isoformat(timespec="seconds")
    ))

    conn.commit()
    conn.close()

    register_user(
        tg_id=temp_tg_id,
        username=None,
        login=data["login"],
        password_hash=hash_password(data["password"]),
        role="teacher",
        full_name=data["full_name"],
        subject=data["subject"],
        group_name=group,
        subgroup=None
    )

    for day in data.get("extra_days", []):
        add_lesson_to_db(
            day=day,
            lesson_num=9,
            subject="Додаткові",
            group_name="ALL",
            teacher=data["full_name"],
            room="—",
            is_extra=1
        )

    await state.clear()
    await message.answer(
        f"✅ Вчителя додано!\n\n"
        f"👤 <b>{data['full_name']}</b>\n"
        f"📚 {data['subject']}\n"
        f"🔑 <code>{data['login']}</code>\n"
        f"🏫 Класне керівництво: {group or 'Немає'}\n"
        f"📘 Додаткові: {', '.join(data.get('extra_days', [])) or 'немає'}",
        parse_mode="HTML",
        reply_markup=get_main_menu("admin")
    )


@dp.message(F.text == "📋 Список вчителів")
@require_auth(role="admin")
async def list_teachers(message: types.Message):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT full_name, login, subject, group_name
        FROM users
        WHERE role = 'teacher'
        ORDER BY full_name
    """)

    teachers = cur.fetchall()
    conn.close()

    if not teachers:
        await message.answer("📭 Немає доданих вчителів.")
        return

    text = "👨‍🏫 <b>Список вчителів:</b>\n\n"
    for teacher in teachers:
        text += (
            f"• <b>{teacher['full_name']}</b>\n"
            f"  📚 {teacher['subject'] or 'Не вказано'}\n"
            f"  🔑 <code>{teacher['login']}</code>\n"
            f"  🏫 {teacher['group_name'] or 'Немає'}\n\n"
        )

    await message.answer(text, parse_mode="HTML")


# ==================== АДМІН: УЧНІ ====================
@dp.message(F.text == "👨‍🎓 Учні")
@require_auth(role="admin")
async def manage_students(message: types.Message):
    kb = [
        [KeyboardButton(text="➕ Додати учня")],
        [KeyboardButton(text="📋 Список учнів")],
        [KeyboardButton(text="🔙 Назад")]
    ]
    await message.answer(
        "👨‍🎓 <b>Керування учнями</b>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(F.text == "➕ Додати учня")
@require_auth(role="admin")
async def add_student_start(message: types.Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_student_full_name)
    await message.answer("Введіть ПІБ учня:", reply_markup=get_cancel_keyboard())


@dp.message(AdminStates.waiting_for_student_full_name)
@require_auth(role="admin")
async def add_student_name(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    await state.update_data(full_name=message.text.strip())
    await state.set_state(AdminStates.waiting_for_student_login)
    await message.answer("Введіть логін учня:")


@dp.message(AdminStates.waiting_for_student_login)
@require_auth(role="admin")
async def add_student_login(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    login = message.text.strip()
    if get_user_by_login(login):
        await message.answer("❌ Такий логін уже існує. Введіть інший.")
        return

    await state.update_data(login=login)
    await state.set_state(AdminStates.waiting_for_student_group)

    groups = get_all_classes()
    kb = [[KeyboardButton(text=group)] for group in groups]
    kb.append([KeyboardButton(text="❌ Скасувати")])

    await message.answer(
        "Оберіть клас:",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(AdminStates.waiting_for_student_group)
@require_auth(role="admin")
async def add_student_group(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    group_name = message.text.strip()
    await state.update_data(group=group_name)

    kb = [
        [KeyboardButton(text="весь клас")],
        [KeyboardButton(text="підгр.1")],
        [KeyboardButton(text="підгр.2")],
        [KeyboardButton(text="❌ Скасувати")]
    ]

    await state.set_state(AdminStates.waiting_for_student_subgroup)
    await message.answer(
        "Оберіть підгрупу:",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(AdminStates.waiting_for_student_subgroup)
@require_auth(role="admin")
async def add_student_subgroup(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    subgroup_map = {
        "весь клас": None,
        "підгр.1": "1",
        "підгр.2": "2"
    }

    if message.text not in subgroup_map:
        await message.answer("Оберіть підгрупу з клавіатури.")
        return

    await state.update_data(subgroup=subgroup_map[message.text])
    await state.set_state(AdminStates.waiting_for_student_password)
    await message.answer("Введіть пароль учня:")


@dp.message(AdminStates.waiting_for_student_password)
@require_auth(role="admin")
async def add_student_password(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    data = await state.get_data()
    login = data["login"]
    temp_tg_id = int(hashlib.md5(login.encode()).hexdigest()[:8], 16)

    table_name = sanitize_table_name(data["group"])
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(f"""
        INSERT INTO {table_name} (tg_id, username, login, password_hash, full_name, subgroup, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(login) DO UPDATE SET
            tg_id = excluded.tg_id,
            username = excluded.username,
            password_hash = excluded.password_hash,
            full_name = excluded.full_name,
            subgroup = excluded.subgroup
    """, (temp_tg_id, None, login, hash_password(message.text.strip()), data["full_name"], data.get("subgroup"),
          datetime.datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()

    register_user(
        tg_id=temp_tg_id,
        username=None,
        login=login,
        password_hash=hash_password(message.text.strip()),
        role="student",
        full_name=data["full_name"],
        group_name=data["group"],
        subgroup=data.get("subgroup")
    )

    subgroup_text = f" (підгрупа {data['subgroup']})" if data.get("subgroup") else ""
    await state.clear()
    await message.answer(
        f"✅ Учня додано!\n\n"
        f"👤 {data['full_name']}\n"
        f"🏫 {data['group']}{subgroup_text}\n"
        f"🔑 <code>{login}</code>",
        parse_mode="HTML",
        reply_markup=get_main_menu("admin")
    )


@dp.message(F.text == "📋 Список учнів")
@require_auth(role="admin")
async def list_students(message: types.Message):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT full_name, login, group_name, subgroup
        FROM users
        WHERE role = 'student'
        ORDER BY group_name, full_name
    """)
    students = cur.fetchall()
    conn.close()

    if not students:
        await message.answer("📭 Немає доданих учнів.")
        return

    text = "👨‍🎓 <b>Список учнів:</b>\n\n"
    for s in students:
        subgroup_text = f" (підгр.{s['subgroup']})" if s["subgroup"] else ""
        text += f"• {s['full_name']} — {s['group_name']}{subgroup_text} (<code>{s['login']}</code>)\n"

    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await message.answer(text[i:i + 4000], parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML")


# ==================== АДМІН: РОЗКЛАД ====================
@dp.message(F.text == "📚 Розклад")
@require_auth(role="admin")
async def manage_schedule(message: types.Message):
    kb = [
        [KeyboardButton(text="➕ Додати урок")],
        [KeyboardButton(text="🗑 Видалити урок")],
        [KeyboardButton(text="📋 Переглянути розклад")],
        [KeyboardButton(text="🔙 Назад")]
    ]
    await message.answer(
        "📚 <b>Керування розкладом</b>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(F.text == "➕ Додати урок")
@require_auth(role="admin")
async def add_lesson_start(message: types.Message, state: FSMContext):
    kb = [[KeyboardButton(text=day)] for day in DAYS_ORDER]
    kb.append([KeyboardButton(text="❌ Скасувати")])
    await state.set_state(AdminStates.waiting_for_schedule_day)
    await message.answer(
        "Оберіть день:",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(AdminStates.waiting_for_schedule_day)
@require_auth(role="admin")
async def add_lesson_day(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    if message.text not in DAYS_ORDER:
        await message.answer("Оберіть день з клавіатури.")
        return

    await state.update_data(day=message.text)
    await state.set_state(AdminStates.waiting_for_schedule_lesson)
    await message.answer("Введіть номер уроку (1-8):", reply_markup=get_cancel_keyboard())


@dp.message(AdminStates.waiting_for_schedule_lesson)
@require_auth(role="admin")
async def add_lesson_lesson(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    try:
        lesson_num = int(message.text)
        if lesson_num < 1 or lesson_num > 8:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введіть число від 1 до 8.")
        return

    await state.update_data(lesson_num=lesson_num)
    await state.set_state(AdminStates.waiting_for_schedule_subject)
    await message.answer("Введіть предмет:")


@dp.message(AdminStates.waiting_for_schedule_subject)
@require_auth(role="admin")
async def add_lesson_subject(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    await state.update_data(subject=message.text.strip())
    await state.set_state(AdminStates.waiting_for_schedule_group)

    groups = get_all_classes()
    kb = [[KeyboardButton(text=group)] for group in groups]
    kb.append([KeyboardButton(text="❌ Скасувати")])

    await message.answer(
        "Оберіть клас:",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(AdminStates.waiting_for_schedule_group)
@require_auth(role="admin")
async def add_lesson_group(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    group = message.text.strip()
    await state.update_data(group=group)

    kb = [
        [KeyboardButton(text="весь клас")],
        [KeyboardButton(text="підгр.1")],
        [KeyboardButton(text="підгр.2")],
        [KeyboardButton(text="❌ Скасувати")]
    ]

    await state.set_state(AdminStates.waiting_for_schedule_subgroup)
    await message.answer(
        "Оберіть підгрупу:",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(AdminStates.waiting_for_schedule_subgroup)
@require_auth(role="admin")
async def add_lesson_subgroup(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    subgroup_map = {
        "весь клас": None,
        "підгр.1": "1",
        "підгр.2": "2"
    }

    if message.text not in subgroup_map:
        await message.answer("Оберіть підгрупу з клавіатури.")
        return

    await state.update_data(subgroup=subgroup_map[message.text])
    await state.set_state(AdminStates.waiting_for_schedule_teacher)

    teachers = get_all_teachers()
    kb = [[KeyboardButton(text=t[0])] for t in teachers]
    kb.append([KeyboardButton(text="❌ Скасувати")])

    await message.answer(
        "Оберіть вчителя:",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(AdminStates.waiting_for_schedule_teacher)
@require_auth(role="admin")
async def add_lesson_teacher(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    await state.update_data(teacher=message.text.strip())
    await state.set_state(AdminStates.waiting_for_schedule_room)
    await message.answer("Введіть кабінет:")


@dp.message(AdminStates.waiting_for_schedule_room)
@require_auth(role="admin")
async def add_lesson_room(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    data = await state.get_data()
    add_lesson_to_db(
        day=data["day"],
        lesson_num=data["lesson_num"],
        subject=data["subject"],
        group_name=data["group"],
        teacher=data["teacher"],
        room=message.text.strip(),
        subgroup=data.get("subgroup")
    )
    await state.clear()
    await message.answer(
        "✅ Урок додано або оновлено.",
        reply_markup=get_main_menu("admin")
    )


@dp.message(F.text == "🗑 Видалити урок")
@require_auth(role="admin")
async def delete_lesson_start(message: types.Message, state: FSMContext):
    kb = [[KeyboardButton(text=day)] for day in DAYS_ORDER]
    kb.append([KeyboardButton(text="❌ Скасувати")])
    await state.set_state(AdminStates.waiting_for_schedule_delete_day)
    await message.answer(
        "Оберіть день уроку для видалення:",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(AdminStates.waiting_for_schedule_delete_day)
@require_auth(role="admin")
async def delete_lesson_day(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    if message.text not in DAYS_ORDER:
        await message.answer("Оберіть день з клавіатури.")
        return

    await state.update_data(day=message.text)
    await state.set_state(AdminStates.waiting_for_schedule_delete_lesson)
    await message.answer("Введіть номер уроку (1-8):", reply_markup=get_cancel_keyboard())


@dp.message(AdminStates.waiting_for_schedule_delete_lesson)
@require_auth(role="admin")
async def delete_lesson_lesson(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    try:
        lesson_num = int(message.text)
        if lesson_num < 1 or lesson_num > 8:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введіть число від 1 до 8.")
        return

    await state.update_data(lesson_num=lesson_num)
    await state.set_state(AdminStates.waiting_for_schedule_delete_group)

    groups = get_all_classes()
    kb = [[KeyboardButton(text=group)] for group in groups]
    kb.append([KeyboardButton(text="❌ Скасувати")])

    await message.answer(
        "Оберіть клас:",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(AdminStates.waiting_for_schedule_delete_group)
@require_auth(role="admin")
async def delete_lesson_group(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    data = await state.get_data()
    deleted = delete_lesson_from_db(
        day=data["day"],
        lesson_num=data["lesson_num"],
        group_name=message.text.strip()
    )
    await state.clear()

    if deleted:
        await message.answer("✅ Урок видалено.", reply_markup=get_main_menu("admin"))
    else:
        await message.answer("❌ Такий урок не знайдено.", reply_markup=get_main_menu("admin"))


@dp.message(F.text == "📋 Переглянути розклад")
@require_auth(role="admin")
async def view_schedule(message: types.Message):
    user_id = message.from_user.id
    current_day = schedule_view.get_current_day(user_id)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT lesson_num, subject, group_name, subgroup, teacher, room
        FROM schedule
        WHERE day = ?
        ORDER BY lesson_num, group_name, subgroup
    """, (current_day,))
    rows = cur.fetchall()
    conn.close()

    lessons = []
    for r in rows:
        group_display = r["group_name"]
        if r["subgroup"]:
            group_display = f"{r['group_name']} (підгр.{r['subgroup']})"
        else:
            group_display = f"{r['group_name']} (весь клас)"
        lessons.append((r["lesson_num"], r["subject"], group_display, r["teacher"], r["room"]))

    text = format_schedule_for_day(current_day, lessons)

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=get_schedule_keyboard("schedule")
    )


@dp.callback_query(F.data.startswith("schedule_"))
async def schedule_navigation(callback: types.CallbackQuery):
    action = callback.data.split("_")[1]
    user_id = callback.from_user.id

    if action == "prev":
        current_day = schedule_view.prev_day(user_id)
    elif action == "next":
        current_day = schedule_view.next_day(user_id)
    elif action == "today":
        current_day = schedule_view.set_today(user_id)
    elif action == "close":
        await callback.message.delete()
        await callback.answer()
        return
    else:
        await callback.answer()
        return

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT lesson_num, subject, group_name, subgroup, teacher, room
        FROM schedule
        WHERE day = ?
        ORDER BY lesson_num, group_name, subgroup
    """, (current_day,))
    rows = cur.fetchall()
    conn.close()

    lessons = []
    for r in rows:
        group_display = r["group_name"]
        if r["subgroup"]:
            group_display = f"{r['group_name']} (підгр.{r['subgroup']})"
        else:
            group_display = f"{r['group_name']} (весь клас)"
        lessons.append((r["lesson_num"], r["subject"], group_display, r["teacher"], r["room"]))

    text = format_schedule_for_day(current_day, lessons)

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=get_schedule_keyboard("schedule")
    )
    await callback.answer()


# ==================== ЗАМІНИ ====================
@dp.message(F.text == "🔄 Заміни")
async def substitutions_menu(message: types.Message):
    user = get_user_by_tg_id(message.from_user.id)
    if not user or user.get("role") == "unauthorized":
        await message.answer(
            "🔐 <b>Спочатку авторизуйтесь</b>\n\n"
            "Використайте команду /start для входу в систему.",
            parse_mode="HTML"
        )
        return

    if user["role"] == "admin":
        kb = [
            [KeyboardButton(text="➕ Додати заміну")],
            [KeyboardButton(text="📋 Список замін")],
            [KeyboardButton(text="🔙 Назад")]
        ]
        await message.answer(
            "🔄 <b>Керування замінами</b>",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
        )
        return

    today_day = DAYS_UA.get(datetime.datetime.now().strftime("%A"), "Понеділок")

    conn = get_db_connection()
    cur = conn.cursor()

    if user["role"] == "teacher":
        teacher_name = user["full_name"]
        cur.execute("""
            SELECT group_name, subgroup, lesson_num, old_subject, old_teacher, new_subject, new_teacher, new_room
            FROM substitutions
            WHERE day = ? AND new_teacher LIKE ?
            ORDER BY lesson_num
        """, (today_day, f"%{teacher_name}%"))
    else:
        group_name = user["group_name"]
        subgroup = user.get("subgroup")

        if subgroup:
            cur.execute("""
                SELECT group_name, subgroup, lesson_num, old_subject, old_teacher, new_subject, new_teacher, new_room
                FROM substitutions
                WHERE day = ? AND group_name = ? AND subgroup = ?
                ORDER BY lesson_num
            """, (today_day, group_name, subgroup))
        else:
            cur.execute("""
                SELECT group_name, subgroup, lesson_num, old_subject, old_teacher, new_subject, new_teacher, new_room
                FROM substitutions
                WHERE day = ? AND group_name = ? AND subgroup IS NULL
                ORDER BY lesson_num
            """, (today_day, group_name))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        await message.answer("✅ На сьогодні замін немає.")
        return

    text = f"🔄 <b>Заміни на {today_day}:</b>\n\n"
    for row in rows:
        group_display = row['group_name']
        if row['subgroup']:
            group_display = f"{row['group_name']} (підгр.{row['subgroup']})"
        else:
            group_display = f"{row['group_name']} (весь клас)"

        text += (
            f"📚 {group_display} — {row['lesson_num']}-й урок\n"
            f"❌ {row['old_subject']} ({row['old_teacher']})\n"
            f"✅ {row['new_subject']} ({row['new_teacher']}, каб. {row['new_room'] or 'той самий'})\n\n"
        )

    await message.answer(text, parse_mode="HTML")


@dp.message(F.text == "➕ Додати заміну")
@require_auth(role="admin")
async def add_substitution_start(message: types.Message, state: FSMContext):
    kb = [[KeyboardButton(text=day)] for day in DAYS_ORDER]
    kb.append([KeyboardButton(text="❌ Скасувати")])

    await state.set_state(AdminStates.waiting_for_substitution_day)
    await message.answer(
        "Оберіть день заміни:",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(AdminStates.waiting_for_substitution_day)
@require_auth(role="admin")
async def add_substitution_day(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    if message.text not in DAYS_ORDER:
        await message.answer("Оберіть день з клавіатури.")
        return

    await state.update_data(day=message.text)
    await state.set_state(AdminStates.waiting_for_substitution_lesson)
    await message.answer("Введіть номер уроку (1-8):", reply_markup=get_cancel_keyboard())


@dp.message(AdminStates.waiting_for_substitution_lesson)
@require_auth(role="admin")
async def add_substitution_lesson(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    try:
        lesson_num = int(message.text)
        if lesson_num < 1 or lesson_num > 8:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введіть число від 1 до 8.")
        return

    await state.update_data(lesson_num=lesson_num)
    await state.set_state(AdminStates.waiting_for_substitution_group)

    groups = get_all_classes()
    kb = [[KeyboardButton(text=group)] for group in groups]
    kb.append([KeyboardButton(text="❌ Скасувати")])

    await message.answer(
        "Оберіть клас для заміни:",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(AdminStates.waiting_for_substitution_group)
@require_auth(role="admin")
async def add_substitution_group(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    group_name, subgroup = parse_group_and_subgroup(message.text.strip())
    data = await state.get_data()

    conn = get_db_connection()
    cur = conn.cursor()

    if subgroup:
        cur.execute("""
            SELECT subject, teacher, room
            FROM schedule
            WHERE day = ? AND lesson_num = ? AND group_name = ? AND subgroup = ?
        """, (data["day"], data["lesson_num"], group_name, subgroup))
    else:
        cur.execute("""
            SELECT subject, teacher, room
            FROM schedule
            WHERE day = ? AND lesson_num = ? AND group_name = ? AND subgroup IS NULL
        """, (data["day"], data["lesson_num"], group_name))

    old_lesson = cur.fetchone()
    conn.close()

    if not old_lesson:
        await state.clear()
        await message.answer(
            "❌ Урок не знайдено в розкладі. Спочатку додайте урок у розклад.",
            reply_markup=get_main_menu("admin")
        )
        return

    await state.update_data(
        group=group_name,
        subgroup=subgroup,
        old_subject=old_lesson["subject"],
        old_teacher=old_lesson["teacher"],
        old_room=old_lesson["room"]
    )

    teachers = get_all_teachers()
    kb = [[KeyboardButton(text=t[0])] for t in teachers if t[0] != old_lesson["teacher"]]
    kb.append([KeyboardButton(text="❌ Скасувати")])

    await state.set_state(AdminStates.waiting_for_substitution_teacher)
    await message.answer(
        f"Оберіть вчителя на заміну.\n\n"
        f"📖 {old_lesson['subject']}\n"
        f"❌ Замість: {old_lesson['teacher']}",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(AdminStates.waiting_for_substitution_teacher)
@require_auth(role="admin")
async def add_substitution_teacher(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    await state.update_data(new_teacher=message.text.strip())
    await state.set_state(AdminStates.waiting_for_substitution_room)
    await message.answer("Введіть кабінет (або 'немає' для того ж самого):", reply_markup=get_cancel_keyboard())


@dp.message(AdminStates.waiting_for_substitution_room)
@require_auth(role="admin")
async def add_substitution_room(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return

    data = await state.get_data()
    new_room = None if message.text.strip().lower() == "немає" else message.text.strip()

    add_substitution_to_db(
        day=data["day"],
        lesson_num=data["lesson_num"],
        group_name=data["group"],
        subgroup=data.get("subgroup"),
        old_subject=data["old_subject"],
        old_teacher=data["old_teacher"],
        old_room=data["old_room"],
        new_subject=data["old_subject"],
        new_teacher=data["new_teacher"],
        new_room=new_room
    )

    await notify_teacher(
        data["new_teacher"],
        (
            f"🔔 <b>У вас заміна</b>\n\n"
            f"📅 {data['day']}\n"
            f"⏰ {data['lesson_num']}-й урок\n"
            f"🏫 {data['group']}"
            f"{' (підгр.' + data['subgroup'] + ')' if data.get('subgroup') else ' (весь клас)'}\n"
            f"📖 {data['old_subject']}\n"
            f"🚪 {new_room or 'той самий кабінет'}\n"
            f"❌ Замість: {data['old_teacher']}"
        )
    )

    await state.clear()
    await message.answer(
        f"✅ Заміну додано.\n\n"
        f"📅 {data['day']}\n"
        f"⏰ {data['lesson_num']}-й урок\n"
        f"🏫 {data['group']}"
        f"{' (підгр.' + data['subgroup'] + ')' if data.get('subgroup') else ' (весь клас)'}\n"
        f"❌ Було: {data['old_teacher']}\n"
        f"✅ Стало: {data['new_teacher']}",
        reply_markup=get_main_menu("admin")
    )


@dp.message(F.text == "📋 Список замін")
@require_auth(role="admin")
async def list_substitutions(message: types.Message):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT day, lesson_num, group_name, old_subject, old_teacher, new_teacher, new_room, created_at
        FROM substitutions
        ORDER BY id DESC
        LIMIT 30
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await message.answer("📭 Немає замін.")
        return

    text = "🔄 <b>Останні заміни:</b>\n\n"
    for row in rows:
        text += (
            f"📅 {row['day']} | {row['lesson_num']}-й урок\n"
            f"🏫 {row['group_name']}\n"
            f"📖 {row['old_subject']}\n"
            f"❌ {row['old_teacher']}\n"
            f"✅ {row['new_teacher']} (каб. {row['new_room'] or 'той самий'})\n"
            f"🕒 {row['created_at']}\n\n"
        )

    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await message.answer(text[i:i + 4000], parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML")


# ==================== МІЙ РОЗКЛАД ====================
@dp.message(F.text == "📅 Мій розклад")
async def show_my_schedule(message: types.Message):
    user = get_user_by_tg_id(message.from_user.id)
    if not user or user.get("role") == "unauthorized":
        await message.answer(
            "🔐 <b>Спочатку авторизуйтесь</b>\n\n"
            "Використайте команду /start для входу в систему.",
            parse_mode="HTML"
        )
        return

    user_id = message.from_user.id
    current_day = schedule_view.get_current_day(user_id)

    conn = get_db_connection()
    cur = conn.cursor()

    lessons = []

    if user["role"] == "teacher":
        cur.execute("""
            SELECT lesson_num, subject, group_name, subgroup, room
            FROM schedule
            WHERE teacher LIKE ? AND day = ?
            ORDER BY lesson_num
        """, (f"%{user['full_name']}%", current_day))

        rows = cur.fetchall()
        lessons = []
        for r in rows:
            group_display = r["group_name"]
            if r["subgroup"]:
                group_display = f"{r['group_name']} (підгр.{r['subgroup']})"
            else:
                group_display = f"{r['group_name']} (весь клас)"
            lessons.append((r["lesson_num"], r["subject"], group_display, user["full_name"], r["room"]))

    else:
        group_base = user["group_name"]

        if user["subgroup"]:
            cur.execute("""
                SELECT lesson_num, subject, teacher, room, group_name, subgroup
                FROM schedule
                WHERE day = ?
                AND group_name = ?
                AND (subgroup = ? OR subgroup IS NULL)
                ORDER BY lesson_num
            """, (current_day, group_base, user["subgroup"]))
        else:
            cur.execute("""
                SELECT lesson_num, subject, teacher, room, group_name, subgroup
                FROM schedule
                WHERE day = ?
                AND group_name = ?
                AND subgroup IS NULL
                ORDER BY lesson_num
            """, (current_day, group_base))

        rows = cur.fetchall()
        lessons = []
        for r in rows:
            group_display = r["group_name"]
            if r["subgroup"]:
                group_display = f"{r['group_name']} (підгр.{r['subgroup']})"
            else:
                group_display = f"{r['group_name']} (весь клас)"
            lessons.append((r["lesson_num"], r["subject"], group_display, r["teacher"], r["room"]))

    conn.close()

    text = f"👤 <b>{user['full_name']}</b>\n" + format_schedule_for_day(current_day, lessons)

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=get_schedule_keyboard("user_schedule")
    )


@dp.callback_query(F.data.startswith("user_schedule_"))
async def user_schedule_navigation(callback: types.CallbackQuery):
    user = get_user_by_tg_id(callback.from_user.id)
    if not user or user.get("role") == "unauthorized":
        await callback.answer("❌ Авторизуйтесь через /start")
        return

    action = callback.data.replace("user_schedule_", "")
    user_id = callback.from_user.id

    if action == "prev":
        current_day = schedule_view.prev_day(user_id)
    elif action == "next":
        current_day = schedule_view.next_day(user_id)
    elif action == "today":
        current_day = schedule_view.set_today(user_id)
    elif action == "close":
        await callback.message.delete()
        await callback.answer()
        return
    else:
        await callback.answer()
        return

    conn = get_db_connection()
    cur = conn.cursor()
    lessons = []

    if user["role"] == "teacher":
        cur.execute("""
            SELECT lesson_num, subject, group_name, subgroup, room
            FROM schedule
            WHERE teacher LIKE ? AND day = ?
            ORDER BY lesson_num
        """, (f"%{user['full_name']}%", current_day))
        rows = cur.fetchall()
        for r in rows:
            group_display = r["group_name"]
            if r["subgroup"]:
                group_display = f"{r['group_name']} (підгр.{r['subgroup']})"
            else:
                group_display = f"{r['group_name']} (весь клас)"
            lessons.append((r["lesson_num"], r["subject"], group_display, user["full_name"], r["room"]))
    else:
        if user["subgroup"]:
            cur.execute("""
                SELECT lesson_num, subject, teacher, room, group_name, subgroup
                FROM schedule
                WHERE group_name = ?
                AND day = ?
                AND (subgroup = ? OR subgroup IS NULL)
                ORDER BY lesson_num
            """, (user["group_name"], current_day, user["subgroup"]))
        else:
            cur.execute("""
                SELECT lesson_num, subject, teacher, room, group_name, subgroup
                FROM schedule
                WHERE group_name = ?
                AND day = ?
                AND subgroup IS NULL
                ORDER BY lesson_num
            """, (user["group_name"], current_day))

        rows = cur.fetchall()
        for r in rows:
            group_display = r["group_name"]
            if r["subgroup"]:
                group_display = f"{r['group_name']} (підгр.{r['subgroup']})"
            else:
                group_display = f"{r['group_name']} (весь клас)"
            lessons.append((r["lesson_num"], r["subject"], group_display, r["teacher"], r["room"]))

    conn.close()

    text = f"👤 <b>{user['full_name']}</b>\n" + format_schedule_for_day(current_day, lessons)

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=get_schedule_keyboard("user_schedule")
    )
    await callback.answer()


# ==================== ДОДАТКОВІ ====================
@dp.message(F.text == "📘 Додаткові")
async def extra_schedule(message: types.Message):
    user = get_user_by_tg_id(message.from_user.id)
    if not user or user.get("role") == "unauthorized":
        await message.answer(
            "🔐 <b>Спочатку авторизуйтесь</b>\n\n"
            "Використайте команду /start для входу в систему.",
            parse_mode="HTML"
        )
        return

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT day, teacher, subject
        FROM schedule
        WHERE is_extra = 1
        ORDER BY day
    """)

    rows = cur.fetchall()
    conn.close()

    if not rows:
        await message.answer("📭 Немає додаткових.")
        return

    text = "📘 <b>Розклад додаткових:</b>\n\n"

    for r in rows:
        text += f"📅 {r['day']}\n👨‍🏫 {r['teacher']}\n📖 {r['subject']}\n\n"

    await message.answer(text, parse_mode="HTML")


# ==================== ДЕ ВЧИТЕЛЬ ====================
@dp.message(F.text == "👨‍🏫 Де вчитель?")
async def ask_teacher_name(message: types.Message, state: FSMContext):
    user = get_user_by_tg_id(message.from_user.id)
    if not user or user.get("role") == "unauthorized":
        await message.answer(
            "🔐 <b>Спочатку авторизуйтесь</b>\n\n"
            "Використайте команду /start для входу в систему.",
            parse_mode="HTML"
        )
        return

    now = datetime.datetime.now()
    lesson = get_current_lesson_number(now)

    if lesson is None:
        await message.answer(
            "⛔ Нажаль уроки закінчились.\n\n📘 Дивіться у розклад додаткових.",
            reply_markup=get_main_menu(user["role"])
        )
        return

    await state.set_state(TeacherSearchStates.waiting_for_teacher_name)
    await message.answer(
        "Напишіть прізвище або ПІБ викладача:",
        reply_markup=get_cancel_keyboard()
    )


@dp.message(TeacherSearchStates.waiting_for_teacher_name)
async def find_teacher(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        user = get_user_by_tg_id(message.from_user.id)
        await message.answer(
            "❌ Пошук скасовано.",
            reply_markup=get_main_menu(user["role"] if user else "unauthorized")
        )
        return

    teacher_name = message.text.strip()
    now = datetime.datetime.now()
    current_day_ua = DAYS_UA.get(now.strftime("%A"), "Понеділок")
    current_lesson = get_current_lesson_number(now)

    if current_lesson is None or current_day_ua not in DAYS_ORDER:
        await state.clear()
        user = get_user_by_tg_id(message.from_user.id)
        await message.answer(
            "⛔ Нажаль уроки закінчились.\n\n📘 Дивіться у розклад додаткових.",
            reply_markup=get_main_menu(user["role"] if user else "unauthorized")
        )
        return

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT full_name
        FROM users
        WHERE role = 'teacher' AND full_name LIKE ?
        ORDER BY full_name
        LIMIT 1
    """, (f"%{teacher_name}%",))
    teacher = cur.fetchone()

    if not teacher:
        conn.close()
        await state.clear()
        user = get_user_by_tg_id(message.from_user.id)
        await message.answer(
            "❌ Вчителя не знайдено.",
            reply_markup=get_main_menu(user["role"] if user else "unauthorized")
        )
        return

    teacher_full = teacher["full_name"]
    response = f"👨‍🏫 <b>{teacher_full}</b>\n\n"

    cur.execute("""
        SELECT subject, room, group_name
        FROM schedule
        WHERE teacher LIKE ? AND day = ? AND lesson_num = ?
    """, (f"%{teacher_full}%", current_day_ua, current_lesson))
    lesson = cur.fetchone()
    conn.close()

    if lesson:
        response += (
            f"📖 Зараз: {lesson['subject']}\n"
            f"👥 {lesson['group_name']}\n"
            f"📍 Кабінет: {lesson['room']}\n"
            f"⏰ {current_lesson}-й урок"
        )
    else:
        response += "📭 Зараз у викладача вікно або немає уроку."

    await state.clear()
    user = get_user_by_tg_id(message.from_user.id)
    await message.answer(
        response,
        parse_mode="HTML",
        reply_markup=get_main_menu(user["role"] if user else "unauthorized")
    )


# ==================== FAQ ====================
@dp.message(F.text == "❓ FAQ")
async def show_faq(message: types.Message):
    text = """
<b>❓ Часті запитання:</b>

<b>🏫 Розклад за корпусами:</b>
• <b>5-6 класи</b> навчаються за розкладом <b>2 корпусу</b>
• <b>7-11 класи</b> навчаються за розкладом <b>1 корпусу</b>

<b>⏰ Розклад дзвінків 1 корпус (7-11 класи):</b>
<b>1 пара:</b>
1 урок: 9:00 – 9:40
2 урок: 9:45 – 10:25

<b>2 пара:</b>
3 урок: 10:45 – 11:25
4 урок: 11:30 – 12:10

🍽️ <b>ВЕЛИКА ПЕРЕРВА (12:10 - 13:00)</b> 🍽️

<b>3 пара:</b>
5 урок: 13:00 – 13:40
6 урок: 13:45 – 14:25

<b>4 пара:</b>
7 урок: 14:45 – 15:25
8 урок: 15:30 – 16:10

<b>⏰ Розклад дзвінків 2 корпус (5-6 класи):</b>
<b>1 пара:</b>
1 урок: 9:00 – 9:40
2 урок: 9:45 – 10:25

🍽️ <b>ВЕЛИКА ПЕРЕРВА (10:25 - 11:15)</b> 🍽️

<b>2 пара:</b>
3 урок: 11:15 – 11:55
4 урок: 12:00 – 12:40

<b>3 пара:</b>
5 урок: 13:00 – 13:40
6 урок: 13:45 – 14:25

<b>4 пара:</b>
7 урок: 14:45 – 15:25
8 урок: 15:30 – 16:10

<b>📚 Правила ліцею:</b>
• Приходити за 10 хвилин до початку
• Мати змінне взуття
• Дотримуватись дисципліни

<b>📞 Контакти:</b>
• Директор: +380 (99) 123-45-67
• Завуч: +380 (99) 234-56-78

<b>🏥 Медкабінет:</b>
• 1 поверх, каб. 107
"""
    await message.answer(text, parse_mode="HTML")


@dp.message(F.text == "🔙 Назад")
async def back_to_main(message: types.Message, state: FSMContext):
    await state.clear()
    user = get_user_by_tg_id(message.from_user.id)
    role = user["role"] if user else "unauthorized"
    await message.answer("🏠 <b>Головне меню</b>", parse_mode="HTML", reply_markup=get_main_menu(role))


# ==================== ЗАПУСК ====================
async def main():
    print("🚀 Бот запущено")
    print("✅ Адмін: admin / admin123")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        init_db()
        asyncio.run(main())
    except Exception as e:
        print(f"Помилка запуску: {e}")