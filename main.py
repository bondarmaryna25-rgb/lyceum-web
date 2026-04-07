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
from aiogram.types import Message

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
    waiting_for_substitution_lesson_choice = State()


class TeacherSearchStates(StatesGroup):
    waiting_for_teacher_name = State()


class SettingsStates(StatesGroup):
    waiting_for_display_mode = State()


class ImportStates(StatesGroup):
    waiting_for_csv = State()
    waiting_for_json = State()
    waiting_for_schedule_clone_source = State()
    waiting_for_schedule_clone_target = State()

class ExtraStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_description = State()
    waiting_for_day = State()
    waiting_for_time_start = State()
    waiting_for_time_end = State()
    waiting_for_group = State()
    waiting_for_teacher = State()
    waiting_for_room = State()
    waiting_for_max_participants = State()
    waiting_for_extra_delete_id = State()

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

        cur.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                course INTEGER,
                class_teacher TEXT,
                created_at TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS timeslots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                UNIQUE(start_time, end_time)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS extra_activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                teacher_id INTEGER NOT NULL,
                group_id INTEGER,
                timeslot_id INTEGER,
                room TEXT,
                date TEXT NOT NULL,
                max_participants INTEGER DEFAULT 0,
                current_participants INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS extra_activity_registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                status TEXT DEFAULT 'registered',
                registered_at TEXT,
                UNIQUE(activity_id, student_id)
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
            [KeyboardButton(text="📚 Розклад"), KeyboardButton(text="📘 Додаткові")],
            [KeyboardButton(text="🔄 Заміни"), KeyboardButton(text="🚪 Вийти")]
        ]
    elif role == "teacher":
        kb = [
            [KeyboardButton(text="📅 Мій розклад"), KeyboardButton(text="👨‍🏫 Де вчитель?")],
            [KeyboardButton(text="📘 Додаткові"), KeyboardButton(text="🔄 Заміни")],
            [KeyboardButton(text="❓ FAQ"), KeyboardButton(text="🚪 Вийти")]
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
    """Форматує розклад на день з покращеним відображенням"""
    if not lessons:
        return f"📭 <b>{day}</b>\n\n✨ Немає уроків на цей день ✨"

    lessons_by_corps = {"1": [], "2": []}

    for lesson, subject, group, teacher, room in lessons:
        corps = get_corps_for_class(group, room)
        lessons_by_corps[corps].append((lesson, subject, group, teacher, room))

    # Заголовок дня
    text = f"📅 <b>{day.upper()}</b>\n"
    text += "═" * 35 + "\n\n"

    for corps in ["1", "2"]:
        if lessons_by_corps[corps]:
            # Заголовок корпусу
            if corps == "1":
                text += "🏫 <b>КОРПУС №1</b> (7-11 класи)\n"
            else:
                text += "🏫 <b>КОРПУС №2</b> (5-6 класи)\n"
            text += "─" * 35 + "\n"
            
            lessons_sorted = sorted(lessons_by_corps[corps], key=lambda x: x[0])
            prev_lesson = None

            for idx, (lesson, subject, group, teacher, room) in enumerate(lessons_sorted):
                time_range = get_lesson_time(lesson, corps)

                # Додаємо велику перерву
                if corps == "1" and prev_lesson == 4 and lesson == 5:
                    text += "\n🍽️ <b>ВЕЛИКА ПЕРЕРВА</b> 🍽️\n"
                    text += "⏰ 12:10 – 13:00\n"
                    text += "─" * 35 + "\n\n"

                if corps == "2" and prev_lesson == 2 and lesson == 3:
                    text += "\n🍽️ <b>ВЕЛИКА ПЕРЕРВА</b> 🍽️\n"
                    text += "⏰ 10:25 – 11:15\n"
                    text += "─" * 35 + "\n\n"

                # Форматування залежно від типу пристрою
                if device_type == 'mobile':
                    # Компактний для мобільних
                    text += (
                        f"🕐 <b>{lesson}</b> {time_range}\n"
                        f"📖 {subject}\n"
                        f"👥 {group}\n"
                        f"👨‍🏫 {teacher[:18]}{'…' if len(teacher) > 18 else ''}\n"
                        f"🚪 каб. {room}\n"
                    )
                elif device_type == 'tablet':
                    # Середній для планшетів
                    text += (
                        f"🕐 <b>{lesson}-й урок</b> ({time_range})\n"
                        f"📖 {subject}  │  👥 {group}\n"
                        f"👨‍🏫 {teacher}  │  🚪 каб. {room}\n"
                    )
                else:
                    # Повний для десктопу
                    text += (
                        f"🕐 <b>{lesson}-й урок</b>\n"
                        f"   ⏰ {time_range}\n"
                        f"   📖 <b>{subject}</b>\n"
                        f"   👥 {group}\n"
                        f"   👨‍🏫 {teacher}\n"
                        f"   🚪 каб. {room}\n"
                    )
                
                # Додаємо роздільник між уроками
                if idx < len(lessons_sorted) - 1:
                    next_lesson = lessons_sorted[idx + 1][0]
                    if not (corps == "1" and lesson == 4 and next_lesson == 5) and \
                       not (corps == "2" and lesson == 2 and next_lesson == 3):
                        text += "   " + "·" * 30 + "\n"
                
                prev_lesson = lesson
            
            text += "\n"
    
    return text.strip()


def get_current_lesson_number(now: datetime.datetime) -> Optional[int]:
    """Визначає номер поточного уроку з урахуванням перерв"""
    current_minutes = now.hour * 60 + now.minute
    
    # Розклад для обох корпусів
    lesson_ranges = {
        1: (9 * 60, 9 * 60 + 40),      # 9:00-9:40
        2: (9 * 60 + 45, 10 * 60 + 25), # 9:45-10:25
        3: (10 * 60 + 45, 11 * 60 + 25), # 10:45-11:25
        4: (11 * 60 + 30, 12 * 60 + 10), # 11:30-12:10
        5: (13 * 60, 13 * 60 + 40),      # 13:00-13:40
        6: (13 * 60 + 45, 14 * 60 + 25), # 13:45-14:25
        7: (14 * 60 + 45, 15 * 60 + 25), # 14:45-15:25
        8: (15 * 60 + 30, 16 * 60 + 10), # 15:30-16:10
    }
    
    # Перевіряємо, чи зараз урок
    for lesson_num, (start, end) in lesson_ranges.items():
        if start <= current_minutes <= end:
            return lesson_num
    
    # Перевіряємо, чи зараз перерва (з урахуванням особливостей корпусів)
    breaks = [
        (9 * 60 + 40, 9 * 60 + 45, "мала перерва"),  # 9:40-9:45
        (10 * 60 + 25, 10 * 60 + 45, "велика/мала перерва"),  # 10:25-10:45
        (11 * 60 + 25, 11 * 60 + 30, "мала перерва"),  # 11:25-11:30
        (12 * 60 + 10, 13 * 60, "велика перерва"),  # 12:10-13:00
    ]
    
    for start, end, break_type in breaks:
        if start <= current_minutes < end:
            return None  # Зараз перерва
    
    return None


def get_current_lesson_status(now: datetime.datetime) -> dict:
    """Повертає детальний статус поточного часу"""
    current_minutes = now.hour * 60 + now.minute
    current_time = now.strftime("%H:%M")
    
    # Межі навчального дня
    if current_minutes < 9 * 60:
        return {
            "status": "before",
            "message": "Уроки ще не почалися",
            "next_lesson": 1,
            "time_until": f"{9 - now.hour - 1}:{60 - now.minute:02d}"
        }
    elif current_minutes > 16 * 60 + 10:
        return {
            "status": "after",
            "message": "Уроки вже закінчилися",
            "next_lesson": None,
            "time_until": None
        }
    
    lesson_num = get_current_lesson_number(now)
    
    if lesson_num:
        end_time = get_lesson_end_time(lesson_num)
        return {
            "status": "lesson",
            "lesson_num": lesson_num,
            "message": f"Зараз {lesson_num}-й урок",
            "time_until": f"до {end_time}"
        }
    else:
        next_lesson = get_next_lesson_number(now)
        if next_lesson:
            start_time = get_lesson_start_time(next_lesson)
            return {
                "status": "break",
                "message": "Зараз перерва",
                "next_lesson": next_lesson,
                "time_until": f"до {start_time}"
            }
    
    return {"status": "unknown", "message": "Поза навчальним часом"}


def get_lesson_end_time(lesson_num: int) -> str:
    """Повертає час закінчення уроку"""
    end_times = {
        1: "9:40", 2: "10:25", 3: "11:25", 4: "12:10",
        5: "13:40", 6: "14:25", 7: "15:25", 8: "16:10"
    }
    return end_times.get(lesson_num, "")


def get_lesson_start_time(lesson_num: int) -> str:
    """Повертає час початку уроку"""
    start_times = {
        1: "9:00", 2: "9:45", 3: "10:45", 4: "11:30",
        5: "13:00", 6: "13:45", 7: "14:45", 8: "15:30"
    }
    return start_times.get(lesson_num, "")


def get_next_lesson_number(now: datetime.datetime) -> Optional[int]:
    """Визначає номер наступного уроку"""
    current_minutes = now.hour * 60 + now.minute
    
    lesson_starts = {
        1: 9 * 60,
        2: 9 * 60 + 45,
        3: 10 * 60 + 45,
        4: 11 * 60 + 30,
        5: 13 * 60,
        6: 13 * 60 + 45,
        7: 14 * 60 + 45,
        8: 15 * 60 + 30,
    }
    
    for lesson_num, start_time in lesson_starts.items():
        if current_minutes < start_time:
            return lesson_num
    
    return None


def parse_group_and_subgroup(group_text: str):
    """Парсить назву групи та підгрупу"""
    subgroup = None
    group_name = group_text.strip()
    
    # Варіанти запису підгруп
    subgroup_patterns = [
        (r"\(підгр\.\s*1\)", "1"),
        (r"\(підгр\.\s*2\)", "2"),
        (r"\(весь\s*клас\)", None),
        (r"\(1\s*підгрупа\)", "1"),
        (r"\(2\s*підгрупа\)", "2"),
    ]
    
    import re
    for pattern, sub_value in subgroup_patterns:
        if re.search(pattern, group_text, re.IGNORECASE):
            subgroup = sub_value
            group_name = re.sub(pattern, "", group_text, flags=re.IGNORECASE).strip()
            break
    
    return group_name, subgroup


def detect_device_type(user_agent: str) -> str:
    """Визначає тип пристрою за User-Agent"""
    if not user_agent:
        return 'desktop'
        
    user_agent = user_agent.lower()
    
    # Ключові слова для різних типів пристроїв
    device_patterns = {
        'mobile': [
            'android', 'iphone', 'ipod', 'blackberry', 
            'windows phone', 'mobile', 'opera mini',
            'samsung', 'huawei', 'xiaomi'
        ],
        'tablet': [
            'ipad', 'tablet', 'kindle', 'silk',
            'playbook', 'nexus 7', 'nexus 10'
        ]
    }
    
    # Спочатку перевіряємо планшети (бо вони можуть містити mobile ключові слова)
    for keyword in device_patterns['tablet']:
        if keyword in user_agent:
            return 'tablet'
    
    # Потім перевіряємо мобільні
    for keyword in device_patterns['mobile']:
        if keyword in user_agent:
            # Додаткова перевірка для планшетів
            if 'tablet' not in user_agent and 'ipad' not in user_agent:
                return 'mobile'
    
    # За замовчуванням - десктоп
    return 'desktop'


def format_lesson_time(lesson_num: int, corps: str = "1") -> str:
    """Форматує час уроку з урахуванням корпусу"""
    lesson_times = {
        1: ("9:00", "9:40"),
        2: ("9:45", "10:25"),
        3: ("10:45", "11:25") if corps == "1" else ("11:15", "11:55"),
        4: ("11:30", "12:10") if corps == "1" else ("12:00", "12:40"),
        5: ("13:00", "13:40"),
        6: ("13:45", "14:25"),
        7: ("14:45", "15:25"),
        8: ("15:30", "16:10"),
    }
    
    times = lesson_times.get(lesson_num, ("", ""))
    return f"{times[0]} – {times[1]}"


def get_lesson_time(lesson_num: int, corps: str = "1") -> str:
    """Повертає час уроку у форматі 'HH:MM - HH:MM'"""
    return format_lesson_time(lesson_num, corps)


# ==================== АВТОРИЗАЦІЯ ====================
def update_user_tg_id(login: str, tg_id: int, username: str = None):
    """Оновлює tg_id для користувача в усіх таблицях"""
    try:
        # Отримуємо користувача
        user = get_user_by_login(login)
        if not user:
            return
        
        # Оновлюємо в таблиці users
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE users 
                SET tg_id = ?, username = COALESCE(?, username)
                WHERE login = ?
            """, (tg_id, username, login))
        
        # Оновлюємо у відповідній таблиці (адміни, вчителі, учні)
        if user["role"] == "admin":
            with db_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    UPDATE admins 
                    SET tg_id = ?, username = COALESCE(?, username)
                    WHERE login = ?
                """, (tg_id, username, login))
        
        elif user["role"] == "teacher":
            with db_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    UPDATE teachers 
                    SET tg_id = ?, username = COALESCE(?, username)
                    WHERE login = ?
                """, (tg_id, username, login))
        
        elif user["role"] == "student" and user.get("group_name"):
            table_name = sanitize_table_name(user["group_name"])
            try:
                with db_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(f"""
                        UPDATE {table_name} 
                        SET tg_id = ?, username = COALESCE(?, username)
                        WHERE login = ?
                    """, (tg_id, username, login))
            except Exception as e:
                print(f"Помилка оновлення учня: {e}")
                
    except Exception as e:
        print(f"Помилка в update_user_tg_id: {e}")


@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    user = get_user_by_tg_id(message.from_user.id)
    
    # Якщо користувач вже авторизований
    if user and user.get("role") != "unauthorized":
        # Оновлюємо username на випадок, якщо змінився
        if message.from_user.username != user.get("username"):
            update_user_tg_id(user["login"], message.from_user.id, message.from_user.username)
        
        text = f"🌟 <b>Вітаємо, {user['full_name']}!</b>\n\n"
        text += f"📌 Роль: <b>{user['role'].upper()}</b>\n"
        if user.get("group_name"):
            text += f"🏫 Клас: <b>{user['group_name']}</b>\n"
        if user.get("subgroup"):
            text += f"🔹 Підгрупа: <b>{user['subgroup']}</b>\n"
        await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu(user["role"]))
        return
    
    # Якщо не авторизований, але раніше входив з іншого пристрою
    # Перевіряємо, чи є користувач за логіном (з іншим tg_id)
    # Для цього потрібно зберегти останній логін у FSM або базі даних
    # Спрощений варіант - просто пропонуємо авторизуватись
    
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
    
    # Перевіряємо, чи існує користувач з таким логіном
    user = get_user_by_login(login)
    if not user:
        await message.answer("❌ Користувача з таким логіном не знайдено. Спробуйте ще раз або введіть /start для початку.")
        return
    
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
        await message.answer("❌ Невірний пароль. Спробуйте ще раз.")
        return
    
    # Оновлюємо tg_id для існуючого користувача
    update_user_tg_id(login, message.from_user.id, message.from_user.username)

    # Реєструємо/оновлюємо в users з tg_id
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
    # Очищаємо tg_id для користувача
    user = get_user_by_tg_id(message.from_user.id)
    if user:
        # Можна опціонально очистити tg_id, але краще залишити для зручності
        # update_user_tg_id(user["login"], None)
        pass
    
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

# ==================== АДМІН: ДОДАТКОВІ ЗАНЯТТЯ ====================
@dp.message(F.text == "📘 Додаткові")
async def extra_menu_handler(message: types.Message, state: FSMContext):
    """Обробник для кнопки Додаткові"""
    user = get_user_by_tg_id(message.from_user.id)
    
    if not user or user.get("role") == "unauthorized":
        await message.answer(
            "🔐 <b>Спочатку авторизуйтесь</b>\n\n"
            "Використайте команду /start для входу в систему.",
            parse_mode="HTML"
        )
        return
    
    # Якщо адмін - показуємо меню керування
    if user["role"] == "admin":
        kb = [
            [KeyboardButton(text="➕ Додати додаткове")],
            [KeyboardButton(text="📋 Список додаткових")],
            [KeyboardButton(text="🗑 Видалити додаткове")],
            [KeyboardButton(text="🔙 Назад")]
        ]
        await message.answer(
            "📘 <b>Керування додатковими заняттями</b>\n\n"
            "Тут ви можете додавати, переглядати та видаляти додаткові заняття.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
        )
    else:
        # Для учнів та вчителів - показуємо розклад
        await show_extra_schedule(message, user)


async def show_extra_schedule(message: types.Message, user: dict):
    """Показує розклад додаткових занять"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    if user["role"] == "teacher":
        cur.execute("""
            SELECT 
                ea.id,
                ea.title,
                ea.description,
                ea.room,
                ea.date,
                ea.max_participants,
                ea.current_participants,
                t.start_time,
                t.end_time,
                g.name as group_name,
                u.full_name as teacher_name
            FROM extra_activities ea
            LEFT JOIN groups g ON ea.group_id = g.id
            LEFT JOIN timeslots t ON ea.timeslot_id = t.id
            LEFT JOIN users u ON ea.teacher_id = u.tg_id
            WHERE u.tg_id = ?
            ORDER BY 
                CASE ea.date
                    WHEN 'Понеділок' THEN 1
                    WHEN 'Вівторок' THEN 2
                    WHEN 'Середа' THEN 3
                    WHEN 'Четвер' THEN 4
                    WHEN 'П''ятниця' THEN 5
                    ELSE 6
                END,
                t.start_time
        """, (user["tg_id"],))
    else:
        cur.execute("""
            SELECT 
                ea.id,
                ea.title,
                ea.description,
                ea.room,
                ea.date,
                ea.max_participants,
                ea.current_participants,
                t.start_time,
                t.end_time,
                g.name as group_name,
                u.full_name as teacher_name
            FROM extra_activities ea
            LEFT JOIN groups g ON ea.group_id = g.id
            LEFT JOIN timeslots t ON ea.timeslot_id = t.id
            LEFT JOIN users u ON ea.teacher_id = u.tg_id
            WHERE g.name = ?
            ORDER BY 
                CASE ea.date
                    WHEN 'Понеділок' THEN 1
                    WHEN 'Вівторок' THEN 2
                    WHEN 'Середа' THEN 3
                    WHEN 'Четвер' THEN 4
                    WHEN 'П''ятниця' THEN 5
                    ELSE 6
                END,
                t.start_time
        """, (user["group_name"],))
    
    activities = cur.fetchall()
    conn.close()
    
    if not activities:
        await message.answer(
            "📭 <b>Немає запланованих додаткових занять</b>\n\n"
            "Перевірте пізніше або зверніться до адміністратора.",
            parse_mode="HTML",
            reply_markup=get_main_menu(user["role"])
        )
        return
    
    text = "📘 <b>РОЗКЛАД ДОДАТКОВИХ ЗАНЯТЬ</b>\n\n"
    
    for act in activities:
        text += f"📅 <b>{act['date']}</b>\n"
        text += f"🕐 {act['start_time']} - {act['end_time']}\n"
        text += f"📖 <b>{act['title']}</b>\n"
        text += f"👨‍🏫 {act['teacher_name']}\n"
        text += f"🚪 каб. {act['room'] or 'не вказано'}\n"
        
        if act['max_participants'] > 0:
            text += f"👥 Місць: {act['current_participants']}/{act['max_participants']}\n"
        
        if act['description']:
            desc = act['description'][:100]
            text += f"📝 {desc}{'...' if len(act['description']) > 100 else ''}\n"
        
        text += "━━━━━━━━━━━━━━━━━━━\n\n"
    
    if len(text) > 4000:
        parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for part in parts:
            await message.answer(part, parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu(user["role"]))


@dp.message(F.text == "➕ Додати додаткове")
@require_auth(role="admin")
async def add_extra_start(message: types.Message, state: FSMContext):
    """Початок додавання додаткового заняття"""
    await state.set_state(ExtraStates.waiting_for_title)
    await message.answer(
        "📘 <b>Додавання додаткового заняття</b>\n\n"
        "Введіть <b>назву</b> заняття (наприклад: 'Підготовка до ЗНО з математики'):",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )


@dp.message(ExtraStates.waiting_for_title)
@require_auth(role="admin")
async def add_extra_title(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return
    
    await state.update_data(title=message.text.strip())
    await state.set_state(ExtraStates.waiting_for_description)
    await message.answer(
        "Введіть <b>опис</b> заняття (або введіть 'ні' для пропуску):",
        parse_mode="HTML"
    )


@dp.message(ExtraStates.waiting_for_description)
@require_auth(role="admin")
async def add_extra_description(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return
    
    description = None if message.text.strip().lower() == "ні" else message.text.strip()
    await state.update_data(description=description)
    await state.set_state(ExtraStates.waiting_for_day)
    
    kb = [[KeyboardButton(text=day)] for day in DAYS_ORDER]
    kb.append([KeyboardButton(text="❌ Скасувати")])
    
    await message.answer(
        "📅 Оберіть <b>день тижня</b> для регулярного заняття:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(ExtraStates.waiting_for_day)
@require_auth(role="admin")
async def add_extra_day(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return
        
    day = message.text.strip()
    if day not in DAYS_ORDER:
        await message.answer("❌ Будь ласка, оберіть день з клавіатури.")
        return

    await state.update_data(day_of_week=day)
    await state.set_state(ExtraStates.waiting_for_time_start)
    await message.answer(
        "Введіть <b>час початку</b> (наприклад: 15:00):",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )


@dp.message(ExtraStates.waiting_for_time_start)
@require_auth(role="admin")
async def add_extra_time_start(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return
    
    try:
        datetime.datetime.strptime(message.text.strip(), "%H:%M")
        await state.update_data(time_start=message.text.strip())
    except ValueError:
        await message.answer("❌ Невірний формат часу. Використовуйте <code>ГГ:ХХ</code>", parse_mode="HTML")
        return
    
    await state.set_state(ExtraStates.waiting_for_time_end)
    await message.answer(
        "Введіть <b>час закінчення</b> заняття:",
        parse_mode="HTML"
    )


@dp.message(ExtraStates.waiting_for_time_end)
@require_auth(role="admin")
async def add_extra_time_end(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return
    
    try:
        datetime.datetime.strptime(message.text.strip(), "%H:%M")
        await state.update_data(time_end=message.text.strip())
    except ValueError:
        await message.answer("❌ Невірний формат часу. Використовуйте <code>ГГ:ХХ</code>", parse_mode="HTML")
        return
    
    await state.set_state(ExtraStates.waiting_for_group)
    
    groups = get_all_classes()
    kb = [[KeyboardButton(text=group)] for group in groups[:15]]
    kb.append([KeyboardButton(text="❌ Скасувати")])
    
    await message.answer(
        "Оберіть <b>клас/групу</b> для заняття:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(ExtraStates.waiting_for_group)
@require_auth(role="admin")
async def add_extra_group(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return
    
    group_name = message.text.strip()
    await state.update_data(group_name=group_name)
    await state.set_state(ExtraStates.waiting_for_teacher)
    
    teachers = get_all_teachers()
    kb = [[KeyboardButton(text=t[0])] for t in teachers[:20]]
    kb.append([KeyboardButton(text="❌ Скасувати")])
    
    await message.answer(
        "Оберіть <b>вчителя</b>, який проводить заняття:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )


@dp.message(ExtraStates.waiting_for_teacher)
@require_auth(role="admin")
async def add_extra_teacher(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return
    
    teacher_name = message.text.strip()
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT tg_id FROM users WHERE role = 'teacher' AND full_name = ?", (teacher_name,))
    teacher = cur.fetchone()
    conn.close()
    
    if not teacher:
        await message.answer("❌ Вчителя з таким ім'ям не знайдено. Введіть коректне ПІБ.")
        return
    
    await state.update_data(teacher_name=teacher_name, teacher_id=teacher["tg_id"])
    await state.set_state(ExtraStates.waiting_for_room)
    await message.answer(
        "Введіть <b>кабінет</b> (або введіть 'ні' для пропуску):",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )


@dp.message(ExtraStates.waiting_for_room)
@require_auth(role="admin")
async def add_extra_room(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return
    
    room = None if message.text.strip().lower() == "ні" else message.text.strip()
    await state.update_data(room=room)
    await state.set_state(ExtraStates.waiting_for_max_participants)
    await message.answer(
        "Введіть <b>максимальну кількість учасників</b> (або 0, якщо без обмежень):",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )


@dp.message(ExtraStates.waiting_for_max_participants)
@require_auth(role="admin")
async def add_extra_max_participants(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return
    
    try:
        max_participants = int(message.text.strip())
        if max_participants < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введіть коректне число (0 або більше).")
        return
    
    data = await state.get_data()
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Отримуємо або створюємо таймслот
        cur.execute("INSERT OR IGNORE INTO timeslots (start_time, end_time) VALUES (?, ?)", 
                   (data["time_start"], data["time_end"]))
        cur.execute("SELECT id FROM timeslots WHERE start_time=? AND end_time=?", 
                   (data["time_start"], data["time_end"]))
        ts_row = cur.fetchone()
        ts_id = ts_row[0] if ts_row else None
        
        # Отримуємо ID групи
        cur.execute("SELECT id FROM groups WHERE name = ?", (data["group_name"],))
        group = cur.fetchone()
        group_id = group[0] if group else None
        
        # Якщо групи немає в таблиці groups, додаємо її
        if not group_id:
            cur.execute("INSERT INTO groups (name, created_at) VALUES (?, ?)", 
                       (data["group_name"], datetime.datetime.now().isoformat(timespec="seconds")))
            group_id = cur.lastrowid
        
        # Додаємо заняття
        cur.execute("""
            INSERT INTO extra_activities (
                title, description, teacher_id, group_id, timeslot_id,
                room, date, max_participants, current_participants,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """, (
            data["title"], 
            data.get("description"), 
            data["teacher_id"], 
            group_id, 
            ts_id, 
            data.get("room"), 
            data["day_of_week"], 
            max_participants,
            datetime.datetime.now().isoformat(timespec="seconds"),
            datetime.datetime.now().isoformat(timespec="seconds")
        ))
        
        conn.commit()
        
        await state.clear()
        await message.answer(
            f"✅ <b>Додаткове заняття додано!</b>\n\n"
            f"📖 {data['title']}\n"
            f"📅 {data['day_of_week']}, {data['time_start']}-{data['time_end']}\n"
            f"👥 {data['group_name']}\n"
            f"👨‍🏫 {data['teacher_name']}",
            parse_mode="HTML",
            reply_markup=get_main_menu("admin")
        )
        
    except Exception as e:
        conn.rollback()
        await message.answer(f"❌ Помилка: {e}")
    finally:
        conn.close()


@dp.message(F.text == "📋 Список додаткових")
@require_auth(role="admin")
async def list_extra_activities_admin(message: types.Message):
    """Показує список додаткових занять для адміна"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT 
            ea.id,
            ea.title,
            ea.description,
            ea.room,
            ea.date,
            ea.max_participants,
            ea.current_participants,
            t.start_time,
            t.end_time,
            g.name as group_name,
            u.full_name as teacher_name
        FROM extra_activities ea
        LEFT JOIN groups g ON ea.group_id = g.id
        LEFT JOIN timeslots t ON ea.timeslot_id = t.id
        LEFT JOIN users u ON ea.teacher_id = u.tg_id
        ORDER BY 
            CASE ea.date
                WHEN 'Понеділок' THEN 1
                WHEN 'Вівторок' THEN 2
                WHEN 'Середа' THEN 3
                WHEN 'Четвер' THEN 4
                WHEN 'П''ятниця' THEN 5
                ELSE 6
            END,
            t.start_time
    """)
    
    activities = cur.fetchall()
    conn.close()
    
    if not activities:
        await message.answer(
            "📭 <b>Немає додаткових занять</b>\n\n"
            "Щоб додати заняття, натисніть ➕ Додати додаткове",
            parse_mode="HTML",
            reply_markup=get_main_menu("admin")
        )
        return
    
    text = "📘 <b>СПИСОК ДОДАТКОВИХ ЗАНЯТЬ</b>\n\n"
    
    for act in activities:
        text += f"🆔 <b>ID: {act['id']}</b>\n"
        text += f"📖 <b>{act['title']}</b>\n"
        text += f"📅 {act['date']}\n"
        text += f"🕐 {act['start_time']} - {act['end_time']}\n"
        text += f"👥 {act['group_name'] or 'всі класи'}\n"
        text += f"👨‍🏫 {act['teacher_name']}\n"
        text += f"🚪 каб. {act['room'] or 'не вказано'}\n"
        
        if act['max_participants'] > 0:
            text += f"👥 Учасники: {act['current_participants']}/{act['max_participants']}\n"
        
        if act['description']:
            desc = act['description'][:100]
            text += f"📝 {desc}{'...' if len(act['description']) > 100 else ''}\n"
        
        text += "━━━━━━━━━━━━━━━━━━━\n\n"
    
    if len(text) > 4000:
        parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for part in parts:
            await message.answer(part, parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu("admin"))


@dp.message(F.text == "🗑 Видалити додаткове")
@require_auth(role="admin")
async def delete_extra_start(message: types.Message, state: FSMContext):
    """Початок видалення додаткового заняття"""
    await state.set_state(ExtraStates.waiting_for_extra_delete_id)
    await message.answer(
        "🗑 <b>Видалення додаткового заняття</b>\n\n"
        "Введіть <b>ID</b> заняття, яке потрібно видалити.\n\n"
        "💡 Щоб побачити ID, скористайтесь <b>📋 Список додаткових</b>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )


@dp.message(ExtraStates.waiting_for_extra_delete_id)
@require_auth(role="admin")
async def delete_extra_confirm(message: types.Message, state: FSMContext):
    if message.text == "❌ Скасувати":
        await state.clear()
        await message.answer("❌ Скасовано.", reply_markup=get_main_menu("admin"))
        return
    
    try:
        activity_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введіть коректний ID (число).")
        return
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title, date FROM extra_activities WHERE id = ?", (activity_id,))
    activity = cur.fetchone()
    
    if not activity:
        conn.close()
        await message.answer(f"❌ Заняття з ID {activity_id} не знайдено.")
        return
    
    cur.execute("DELETE FROM extra_activity_registrations WHERE activity_id = ?", (activity_id,))
    cur.execute("DELETE FROM extra_activities WHERE id = ?", (activity_id,))
    conn.commit()
    conn.close()
    
    await state.clear()
    await message.answer(
        f"✅ <b>Заняття видалено!</b>\n\n"
        f"🆔 ID: {activity_id}\n"
        f"📖 {activity['title']}\n"
        f"📅 {activity['date']}",
        parse_mode="HTML",
        reply_markup=get_main_menu("admin")
    )


@dp.message(F.text == "🔙 Назад")
@require_auth(role="admin")
async def back_to_admin_main(message: types.Message):
    """Повернення до головного меню адміна"""
    await message.answer(
        "🏠 <b>Головне меню адміністратора</b>",
        parse_mode="HTML",
        reply_markup=get_main_menu("admin")
    )

# ==================== ЗАМІНИ ====================

@dp.message(F.text == "🔄 Заміни")
async def substitutions_menu(message: types.Message):
    """Показує заміни для учнів та вчителів"""
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

    # Отримуємо поточний день
    import datetime
    now = datetime.datetime.now()
    today_day = DAYS_UA.get(now.strftime("%A"), "Понеділок")

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if user["role"] == "teacher":
            teacher_name = user["full_name"]
            print(f"DEBUG: Teacher {teacher_name} checking substitutions for {today_day}")
            
            # Використовуємо правильний синтаксис SQLite з ?
            cur.execute("""
                SELECT group_name, subgroup, lesson_num, old_subject, old_teacher, 
                       new_subject, new_teacher, new_room
                FROM substitutions
                WHERE day = ? AND new_teacher LIKE ?
                ORDER BY lesson_num
            """, (today_day, f"%{teacher_name}%"))
        else:
            group_name = user["group_name"]
            subgroup = user.get("subgroup")
            print(f"DEBUG: Student {group_name} (subgroup {subgroup}) checking substitutions for {today_day}")

            if subgroup:
                # Шукаємо заміни для конкретної підгрупи АБО для всього класу
                cur.execute("""
                    SELECT group_name, subgroup, lesson_num, old_subject, old_teacher, 
                           new_subject, new_teacher, new_room
                    FROM substitutions
                    WHERE day = ? AND group_name = ? AND (subgroup = ? OR subgroup IS NULL)
                    ORDER BY lesson_num
                """, (today_day, group_name, subgroup))
            else:
                # Шукаємо заміни тільки для всього класу
                cur.execute("""
                    SELECT group_name, subgroup, lesson_num, old_subject, old_teacher, 
                           new_subject, new_teacher, new_room
                    FROM substitutions
                    WHERE day = ? AND group_name = ? AND subgroup IS NULL
                    ORDER BY lesson_num
                """, (today_day, group_name))

        rows = cur.fetchall()
        print(f"DEBUG: Found {len(rows)} substitutions")
        
    except Exception as e:
        print(f"ERROR in substitutions query: {e}")
        rows = []
    finally:
        conn.close()

    if not rows:
        await message.answer(
            f"✅ <b>На сьогодні ({today_day}) замін немає</b>",
            parse_mode="HTML",
            reply_markup=get_main_menu(user["role"])
        )
        return

    # Формуємо текст з замінами
    text = f"🔄 <b>Заміни на {today_day}:</b>\n\n"
    
    for row in rows:
        # Форматуємо відображення групи
        if row['subgroup']:
            group_display = f"{row['group_name']} (підгр.{row['subgroup']})"
        else:
            group_display = f"{row['group_name']} (весь клас)"

        # Додаємо інформацію про заміну
        text += (
            f"📚 <b>{row['lesson_num']}-й урок</b> | {group_display}\n"
            f"❌ Було: {row['old_subject']} ({row['old_teacher']})\n"
            f"✅ Стало: {row['new_subject']} ({row['new_teacher']}"
        )
        
        # Додаємо кабінет
        if row['new_room']:
            text += f", каб. {row['new_room']}"
        else:
            text += f", каб. той самий"
        
        text += ")\n━━━━━━━━━━━━━━━━━━━\n"

    try:
        await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu(user["role"]))
    except Exception as e:
        print(f"ERROR sending message: {e}")
        # Якщо виникає помилка з HTML, пробуємо без форматування
        await message.answer(
            text.replace("<b>", "").replace("</b>", ""),
            reply_markup=get_main_menu(user["role"])
        )

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
# Оновлений обробник для кнопки "📘 Додаткові" для звичайних користувачів
# Він вже є у вашому файлі, але переконайтеся, що він використовує нову таблицю extra_activities

@dp.message(F.text == "📘 Додаткові")
async def extra_schedule(message: types.Message):
    """Показує розклад додаткових занять для всіх користувачів"""
    user = get_user_by_tg_id(message.from_user.id)
    if not user or user.get("role") == "unauthorized":
        await message.answer(
            "🔐 <b>Спочатку авторизуйтесь</b>\n\n"
            "Використайте команду /start для входу в систему.",
            parse_mode="HTML"
        )
        return

    # Якщо користувач - адмін, показуємо меню керування
    if user["role"] == "admin":
        await manage_extra_admin(message)
        return

    conn = get_db_connection()
    cur = conn.cursor()
    
    now = datetime.datetime.now()
    current_date = now.strftime("%Y-%m-%d")
    
    # Отримуємо додаткові заняття для користувача
    if user["role"] == "teacher":
        # Для вчителя - його заняття
        cur.execute("""
            SELECT 
                ea.id,
                ea.title,
                ea.description,
                ea.room,
                ea.date,
                ea.max_participants,
                ea.current_participants,
                t.start_time,
                t.end_time,
                g.name as group_name,
                u.full_name as teacher_name
            FROM extra_activities ea
            LEFT JOIN groups g ON ea.group_id = g.id
            LEFT JOIN timeslots t ON ea.timeslot_id = t.id
            LEFT JOIN users u ON ea.teacher_id = u.tg_id
            WHERE u.tg_id = ? AND ea.date >= ?
            ORDER BY ea.date, t.start_time
        """, (user["tg_id"], current_date))
    else:
        # Для учня - заняття його класу
        cur.execute("""
            SELECT 
                ea.id,
                ea.title,
                ea.description,
                ea.room,
                ea.date,
                ea.max_participants,
                ea.current_participants,
                t.start_time,
                t.end_time,
                g.name as group_name,
                u.full_name as teacher_name
            FROM extra_activities ea
            LEFT JOIN groups g ON ea.group_id = g.id
            LEFT JOIN timeslots t ON ea.timeslot_id = t.id
            LEFT JOIN users u ON ea.teacher_id = u.tg_id
            WHERE g.name = ? AND ea.date >= ?
            ORDER BY ea.date, t.start_time
        """, (user["group_name"], current_date))
    
    activities = cur.fetchall()
    conn.close()
    
    if not activities:
        await message.answer(
            "📭 <b>Немає запланованих додаткових занять</b>\n\n"
            "Перевірте пізніше або зверніться до адміністратора.",
            parse_mode="HTML",
            reply_markup=get_main_menu(user["role"])
        )
        return
    
    text = "📘 <b>РОЗКЛАД ДОДАТКОВИХ ЗАНЯТЬ</b>\n\n"
    
    for act in activities:
        date_obj = datetime.datetime.strptime(act["date"], "%Y-%m-%d")
        day_ua = DAYS_UA.get(date_obj.strftime("%A"), "")
        
        text += f"📅 <b>{day_ua}, {act['date']}</b>\n"
        text += f"🕐 {act['start_time']} - {act['end_time']}\n"
        text += f"📖 <b>{act['title']}</b>\n"
        text += f"👨‍🏫 {act['teacher_name']}\n"
        text += f"🚪 каб. {act['room'] or 'не вказано'}\n"
        
        if act['max_participants'] > 0:
            text += f"👥 Місць: {act['current_participants']}/{act['max_participants']}\n"
        
        if act['description']:
            text += f"📝 {act['description'][:100]}...\n"
        
        text += "━━━━━━━━━━━━━━━━━━━\n\n"
    
    # Додаємо кнопку запису, якщо це учень
    if user["role"] == "student":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Записатись на заняття", callback_data="extra_register")]
        ])
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        if len(text) > 4000:
            parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for part in parts:
                await message.answer(part, parse_mode="HTML")
        else:
            await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu(user["role"]))

# ==================== ДЕ ВЧИТЕЛЬ? ====================

@dp.message(F.text == "👨‍🏫 Де вчитель?")
async def ask_teacher_name(message: types.Message, state: FSMContext):
    """Запит на пошук вчителя"""
    user = get_user_by_tg_id(message.from_user.id)
    if not user or user.get("role") == "unauthorized":
        await message.answer(
            "🔐 <b>Спочатку авторизуйтесь</b>\n\n"
            "Використайте команду /start для входу в систему.",
            parse_mode="HTML"
        )
        return

    now = datetime.datetime.now()
    current_hour = now.hour
    current_lesson = get_current_lesson_number(now)
    
    # Перевіряємо, чи зараз уроки
    if current_lesson is None:
        # Перевіряємо, чи ще не почалися уроки
        if current_hour < 9:
            await message.answer(
                "⏰ <b>Уроки ще не почалися</b>\n\n"
                "📚 Перший урок починається о 9:00.\n"
                "💡 Ви можете переглянути розклад додаткових або FAQ.",
                parse_mode="HTML",
                reply_markup=get_main_menu(user["role"])
            )
            return
        else:
            await message.answer(
                "⛔ <b>Уроки закінчились</b>\n\n"
                "📘 Дивіться у розклад додаткових або зверніться до завуча.\n"
                "🏫 Навчальний день: 9:00 - 16:10",
                parse_mode="HTML",
                reply_markup=get_main_menu(user["role"])
            )
            return

    await state.set_state(TeacherSearchStates.waiting_for_teacher_name)
    
    # Показуємо підказку з популярними вчителями
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT full_name 
        FROM users 
        WHERE role = 'teacher' 
        ORDER BY full_name 
        LIMIT 5
    """)
    popular_teachers = cur.fetchall()
    conn.close()
    
    hint_text = "Напишіть прізвище або ПІБ викладача:\n\n"
    if popular_teachers:
        hint_text += "📌 <i>Наприклад:</i>\n"
        for teacher in popular_teachers:
            hint_text += f"• {teacher['full_name']}\n"
    
    await message.answer(
        hint_text,
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )


@dp.message(TeacherSearchStates.waiting_for_teacher_name)
async def find_teacher(message: types.Message, state: FSMContext):
    """Пошук вчителя та визначення його місцезнаходження"""
    if message.text == "❌ Скасувати":
        await state.clear()
        user = get_user_by_tg_id(message.from_user.id)
        await message.answer(
            "❌ Пошук скасовано.",
            reply_markup=get_main_menu(user["role"] if user else "unauthorized")
        )
        return

    teacher_query = message.text.strip()
    now = datetime.datetime.now()
    current_day_ua = DAYS_UA.get(now.strftime("%A"), "Понеділок")
    current_lesson = get_current_lesson_number(now)
    current_time = now.strftime("%H:%M")
    
    # Форматуємо поточну дату для додаткових занять
    current_date_str = now.strftime("%Y-%m-%d")

    conn = get_db_connection()
    cur = conn.cursor()

    # Шукаємо вчителя за різними варіантами
    cur.execute("""
        SELECT full_name, subject, group_name, user_id
        FROM users
        WHERE role = 'teacher' 
        AND (full_name LIKE ? OR full_name LIKE ? OR full_name LIKE ?)
        ORDER BY 
            CASE 
                WHEN full_name = ? THEN 1
                WHEN full_name LIKE ? THEN 2
                ELSE 3
            END,
            full_name
        LIMIT 3
    """, (
        f"%{teacher_query}%",  # будь-яке входження
        f"{teacher_query}%",   # початок рядка
        f"% {teacher_query}%", # після пробілу
        teacher_query,         # точний збіг
        f"{teacher_query}%"    # початок рядка для сортування
    ))
    
    teachers = cur.fetchall()
    
    if not teachers:
        conn.close()
        await state.clear()
        user = get_user_by_tg_id(message.from_user.id)
        
        # Пропонуємо схожих вчителів
        cur = conn.cursor()
        cur.execute("""
            SELECT full_name 
            FROM users 
            WHERE role = 'teacher'
            ORDER BY full_name
            LIMIT 10
        """)
        all_teachers = cur.fetchall()
        conn.close()
        
        suggestion = "\n".join([f"• {t['full_name']}" for t in all_teachers[:5]])
        
        await message.answer(
            f"❌ <b>Вчителя не знайдено</b>\n\n"
            f"🔍 Ви шукали: <i>{teacher_query}</i>\n\n"
            f"💡 <b>Можливо ви мали на увазі:</b>\n{suggestion}\n\n"
            f"📝 Спробуйте ввести прізвище без ініціалів або повне ПІБ.",
            parse_mode="HTML",
            reply_markup=get_main_menu(user["role"] if user else "unauthorized")
        )
        return

    # Якщо знайдено кілька вчителів, показуємо вибір
    if len(teachers) > 1:
        kb = []
        for teacher in teachers:
            kb.append([KeyboardButton(text=teacher["full_name"])])
        kb.append([KeyboardButton(text="❌ Скасувати")])
        
        await state.update_data(teacher_query=teacher_query)
        await state.set_state(TeacherSearchStates.waiting_for_teacher_name)
        
        await message.answer(
            f"🔍 <b>Знайдено кілька вчителів:</b>\n\n"
            f"Оберіть потрібного:",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
        )
        conn.close()
        return

    # Якщо знайдено одного вчителя
    teacher = teachers[0]
    teacher_full = teacher["full_name"]
    teacher_id = teacher["user_id"]
    
    # Шукаємо поточний урок вчителя в основному розкладі
    cur.execute("""
        SELECT subject, room, group_name, subgroup
        FROM schedule
        WHERE teacher LIKE ? AND day = ? AND lesson_num = ?
        ORDER BY group_name
    """, (f"%{teacher_full}%", current_day_ua, current_lesson))
    
    main_lessons = cur.fetchall()
    
    # Шукаємо поточне додаткове заняття вчителя
    cur.execute("""
        SELECT 
            ea.id,
            ea.title,
            ea.room,
            ea.teacher_id,
            ea.date,
            t.start_time as time_start,
            t.end_time as time_end,
            g.name as group_name
        FROM extra_activities ea
        LEFT JOIN groups g ON ea.group_id = g.id
        LEFT JOIN timeslots t ON ea.timeslot_id = t.id
        WHERE ea.teacher_id = ? 
        AND ea.date = ?
        AND t.start_time <= ? 
        AND t.end_time >= ?
        ORDER BY t.start_time
    """, (teacher_id, current_date_str, current_time, current_time))
    
    extra_activities = cur.fetchall()
    
    # Шукаємо наступний урок вчителя (основний розклад)
    next_main_lesson = None
    if current_lesson:
        next_lesson_num = current_lesson + 1
        if next_lesson_num <= 8:
            cur.execute("""
                SELECT lesson_num, subject, room, group_name
                FROM schedule
                WHERE teacher LIKE ? AND day = ? AND lesson_num = ?
                LIMIT 1
            """, (f"%{teacher_full}%", current_day_ua, next_lesson_num))
            next_main_lesson = cur.fetchone()
    
    # Шукаємо наступне додаткове заняття
    cur.execute("""
        SELECT 
            ea.title,
            ea.room,
            ea.date,
            t.start_time as time_start,
            t.end_time as time_end,
            g.name as group_name
        FROM extra_activities ea
        LEFT JOIN groups g ON ea.group_id = g.id
        LEFT JOIN timeslots t ON ea.timeslot_id = t.id
        WHERE ea.teacher_id = ? 
        AND ea.date = ?
        AND t.start_time > ?
        ORDER BY t.start_time
        LIMIT 1
    """, (teacher_id, current_date_str, current_time))
    
    next_extra = cur.fetchone()
    conn.close()
    
    # Формуємо відповідь
    response = f"👨‍🏫 <b>{teacher_full}</b>\n"
    if teacher["subject"]:
        response += f"📚 Предмет: <i>{teacher['subject']}</i>\n"
    if teacher["group_name"]:
        response += f"🏫 Класне керівництво: <i>{teacher['group_name']}</i>\n"
    response += "\n"
    
    # Інформація про поточне місцезнаходження
    response += f"🕒 <b>Поточний момент ({current_time})</b>\n"
    
    if main_lessons:
        # Вчитель на уроці за основним розкладом
        response += f"⏰ {current_lesson}-й урок\n\n"
        
        for lesson in main_lessons:
            group_display = lesson["group_name"]
            if lesson["subgroup"]:
                group_display = f"{lesson['group_name']} (підгр.{lesson['subgroup']})"
            
            response += (
                f"📖 <b>{lesson['subject']}</b>\n"
                f"👥 {group_display}\n"
                f"📍 <b>Кабінет: {lesson['room'] or 'не вказано'}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
            )
            
    elif extra_activities:
        # Вчитель на додатковому занятті
        response += f"📚 <b>Додаткове заняття</b>\n\n"
        
        for activity in extra_activities:
            group_info = f"\n👥 {activity['group_name']}" if activity['group_name'] else ""
            response += (
                f"📖 <b>{activity['title']}</b>{group_info}\n"
                f"⏰ {activity['time_start']} - {activity['time_end']}\n"
                f"📍 <b>Кабінет: {activity['room'] or 'не вказано'}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
            )
    else:
        # Вчитель зараз вільний
        response += f"📭 <b>У {teacher_full} зараз вікно або немає заняття</b>\n\n"
    
    # Інформація про наступне заняття
    response += f"\n⏭️ <b>Найближче заняття:</b>\n"
    
    # Визначаємо, що буде раніше: наступний урок чи додаткове заняття
    next_events = []
    
    if next_main_lesson:
        next_time = get_lesson_time(next_main_lesson["lesson_num"], "1")
        next_events.append({
            'type': 'main',
            'time': next_time,
            'lesson_num': next_main_lesson["lesson_num"],
            'subject': next_main_lesson["subject"],
            'room': next_main_lesson["room"],
            'group': next_main_lesson["group_name"]
        })
    
    if next_extra:
        next_events.append({
            'type': 'extra',
            'time': next_extra["time_start"],
            'subject': next_extra["title"],
            'room': next_extra["room"],
            'group': next_extra["group_name"],
            'time_end': next_extra["time_end"]
        })
    
    if next_events:
        # Сортуємо за часом
        next_events.sort(key=lambda x: x['time'])
        next_event = next_events[0]
        
        if next_event['type'] == 'main':
            response += (
                f"📚 Основний розклад\n"
                f"⏰ {next_event['lesson_num']}-й урок ({next_event['time']})\n"
                f"📖 {next_event['subject']}\n"
                f"👥 {next_event['group']}\n"
                f"📍 <b>Кабінет: {next_event['room'] or 'не вказано'}</b>\n"
            )
        else:
            group_info = f"\n👥 {next_event['group']}" if next_event['group'] else ""
            response += (
                f"📘 Додаткове заняття\n"
                f"⏰ {next_event['time']} - {next_event['time_end']}\n"
                f"📖 {next_event['subject']}{group_info}\n"
                f"📍 <b>Кабінет: {next_event['room'] or 'не вказано'}</b>\n"
            )
    else:
        response += "✨ <b>На сьогодні занять більше немає</b> ✨"
    
    # Додаємо інформацію про заміни (якщо є)
    if main_lessons and current_lesson:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT group_name, lesson_num, new_room
            FROM substitutions
            WHERE day = ? AND new_teacher LIKE ? AND lesson_num = ?
        """, (current_day_ua, f"%{teacher_full}%", current_lesson))
        substitution = cur.fetchone()
        conn.close()
        
        if substitution:
            response += f"\n\n⚠️ <b>Увага! Заміна:</b>\n"
            response += f"🏫 {substitution['group_name']}\n"
            if substitution['new_room']:
                response += f"🚪 Кабінет: {substitution['new_room']}\n"
    
    await state.clear()
    user = get_user_by_tg_id(message.from_user.id)
    await message.answer(
        response,
        parse_mode="HTML",
        reply_markup=get_main_menu(user["role"] if user else "unauthorized")
    )

# ==================== FAQ ====================

class FAQStates(StatesGroup):
    waiting_for_category = State()


@dp.message(F.text == "❓ FAQ")
async def show_faq_categories(message: types.Message, state: FSMContext):
    """Показує категорії FAQ"""
    user = get_user_by_tg_id(message.from_user.id)
    role = user["role"] if user else "unauthorized"
    
    # Очищаємо стан
    await state.clear()
    
    # Створюємо інтерактивну клавіатуру з категоріями
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🏫 Розклад та корпуси", callback_data="faq_schedule"),
            InlineKeyboardButton(text="⏰ Розклад дзвінків", callback_data="faq_bell")
        ],
        [
            InlineKeyboardButton(text="📚 Правила ліцею", callback_data="faq_rules"),
            InlineKeyboardButton(text="👨‍🏫 Вчителі та кабінети", callback_data="faq_teachers")
        ],
        [
            InlineKeyboardButton(text="🔄 Заміни та додаткові", callback_data="faq_substitutions"),
            InlineKeyboardButton(text="📞 Контакти", callback_data="faq_contacts")
        ],
        [
            InlineKeyboardButton(text="❓ Інше", callback_data="faq_other"),
            InlineKeyboardButton(text="🏠 Головне меню", callback_data="faq_back")
        ]
    ])
    
    await message.answer(
        "❓ <b>Часті запитання</b>\n\nОберіть категорію, яка вас цікавить:",
        parse_mode="HTML",
        reply_markup=kb
    )


async def edit_or_send_new(callback_query, text, reply_markup):
    """Редагує поточне повідомлення або створює нове, якщо не вдалося"""
    try:
        await callback_query.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return True
    except Exception as e:
        if "message can't be edited" in str(e) or "message to edit not found" in str(e):
            await callback_query.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return False
        else:
            raise e


@dp.callback_query(F.data.startswith("faq_"))
async def faq_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    """Обробляє вибір категорії FAQ"""
    action = callback.data.split("_", 1)[1]  # Зміна: беремо все після першого _
    
    # Спочатку перевіряємо спеціальні дії
    if action == "back":
        user = get_user_by_tg_id(callback.from_user.id)
        role = user["role"] if user else "unauthorized"
        await callback.message.delete()
        await callback.message.answer(
            "🏠 <b>Головне меню</b>", 
            parse_mode="HTML", 
            reply_markup=get_main_menu(role)
        )
        await callback.answer()
        return
    
    if action == "categories":
        # Показуємо категорії
        await show_categories(callback)
        return
    
    # Текст для кожної категорії
    if action == "schedule":
        text = """
🏫 <b>РОЗКЛАД ЗА КОРПУСАМИ</b>

📌 <b>Важливо:</b>
• 5-6 класи навчаються за розкладом <b>2 корпусу</b>
• 7-11 класи навчаються за розкладом <b>1 корпусу</b>

📍 <b>Як визначити свій корпус:</b>
1. Подивіться номер вашого класу
2. Якщо 5-6 клас → ви в 2 корпусі
3. Якщо 7-11 клас → ви в 1 корпусі

🏢 <b>Корпуси знаходяться за адресами:</b>
• 1 корпус: вул. Тампере, 9
• 2 корпус: вул. Тампере, 12

💡 <b>Порада:</b> Розклад залежить від вашого корпусу, тому звертайте увагу на номери уроків та перерви.
"""
    elif action == "bell":
        text = """
⏰ <b>РОЗКЛАД ДЗВІНКІВ</b>

<b>🏫 1 КОРПУС</b> (7-11 кл.)
1️⃣ 9:00-9:40 | 2️⃣ 9:45-10:25
3️⃣ 10:45-11:25 | 4️⃣ 11:30-12:10
🍽️ 12:10-13:00 — ВЕЛИКА ПЕРЕРВА
5️⃣ 13:00-13:40 | 6️⃣ 13:45-14:25
7️⃣ 14:45-15:25 | 8️⃣ 15:30-16:10

<b>🏫 2 КОРПУС</b> (5-6 кл.)
1️⃣ 9:00-9:40 | 2️⃣ 9:45-10:25
🍽️ 10:25-11:15 — ВЕЛИКА ПЕРЕРВА
3️⃣ 11:15-11:55 | 4️⃣ 12:00-12:40
5️⃣ 13:00-13:40 | 6️⃣ 13:45-14:25
7️⃣ 14:45-15:25 | 8️⃣ 15:30-16:10
"""
    elif action == "rules":
        text = """
📚 <b>ПРАВИЛА ЛІЦЕЮ</b>

✅ <b>Загальні правила:</b>
• Приходити за <b>10 хвилин</b> до початку уроків
• Мати <b>змінне взуття</b> обов'язково
• Дотримуватись <b>дисципліни</b> та тиші на уроках
• Не спізнюватись на уроки без поважної причини

📱 <b>Мобільні телефони:</b>
• Телефони мають бути <b>вимкнені</b> під час уроків
• Користуватися телефонами можна <b>тільки на перервах</b>

🍽️ <b>Їжа та напої:</b>
• Їсти можна <b>тільки в їдальні</b>
• Не можна жувати гумку на уроках
• Сміття викидати <b>тільки в урни</b>

👕 <b>Зовнішній вигляд:</b>
• Шкільна форма <b>обов'язкова</b>
• Охайний вигляд, зачіска
• Без яскравого макіяжу та прикрас

📖 <b>Навчальний процес:</b>
• Виконувати <b>домашні завдання</b>
• Мати <b>всі підручники та зошити</b>
• Активно працювати на уроках
"""
    elif action == "teachers":
        text = """
👨‍🏫 <b>ВЧИТЕЛІ ТА КАБІНЕТИ</b>

🔍 <b>Як знайти вчителя:</b>
• Використайте функцію <b>"👨‍🏫 Де вчитель?"</b> в головному меню
• Введіть прізвище або ПІБ викладача
• Бот покаже поточне місцезнаходження

🏢 <b>Кабінети:</b>
• Кабінети 1-40 знаходяться в <b>1 корпусі</b>
• Кабінети 1-30 знаходяться в <b>2 корпусі</b>

🏥 <b>Медичні кабінети:</b>
• 1 корпус: 1 поверх
• 2 корпус: 1 поверх

📋 <b>Класні керівники:</b>
• Список класних керівників доступний у завучів
• Інформацію можна отримати в канцелярії

👥 <b>Класний колектив:</b>
• Актив класу обирається на початку року
• Староста класу - помічник вчителя
"""
    elif action == "substitutions":
        text = """
🔄 <b>ЗАМІНИ ТА ДОДАТКОВІ ЗАНЯТТЯ</b>

📅 <b>Як дізнатись про заміни:</b>
• Натисніть <b>"🔄 Заміни"</b> в головному меню
• Бот покаже всі заміни на сьогодні
• <b>Учні</b> бачать заміни для свого класу
• <b>Вчителі</b> бачать заміни, де вони задіяні

📘 <b>Додаткові заняття:</b>
• Натисніть <b>"📘 Додаткові"</b> в меню
• Показується розклад додаткових занять
• Можна переглянути за днями тижня

🔔 <b>Сповіщення:</b>
• Вчителі отримують <b>автоматичне сповіщення</b> про заміни
• Учні бачать заміни в реальному часі

➕ <b>Як додати заміну (тільки для адмінів):</b>
1. Натисніть <b>"➕ Додати заміну"</b>
2. Оберіть день та урок
3. Вкажіть клас та предмет
4. Оберіть вчителя, який проводитиме заміну
"""
    elif action == "contacts":
        text = """
📞 <b>КОНТАКТИ ЛІЦЕЮ</b>

👨‍💼 <b>Адміністрація:</b>
• <b>Директор:</b> Андрієнко Анатолій Михайлович
  📱 +380 (67) 234-41-85
  📧 director@lyceum.edu.ua

• <b>Заступник директора:</b> Ярема Тетяна Юріївна
  📱 +380 (50) 254-35-48

• <b>Заступник директора:</b> Снігова Руслана Вікторівна
  📱 +380 (97) 491-10-41

• <b>Заступник директора:</b> Сіренко Анна Сергіївна
  📱 +380 (63) 104-57-73

• <b>Заступник директора:</b> Ковальчук Світлана Сергіївна
  📱 +380 (98) 970-31-50

🏥 <b>Медичні кабінети:</b>
• 1 корпус: Басик Ірина Сергіївна
• 2 корпус: Стась Альона
• Екстрена допомога: <b>103</b>

📚 <b>Бібліотека:</b>
• 📍 2 поверх, каб. 25
• ⏰ 9:00 - 17:00

🍽️ <b>Їдальня:</b>
• 📍 1 корпус, 1 поверх
• ⏰ 9:00 - 16:00

💻 <b>Технічна підтримка бота:</b>
• 📧 support@lyceum-bot.edu.ua

🌐 <b>Офіційний сайт:</b>
• https://tl.kyiv.ua/
"""
    else:  # other
        text = """
❓ <b>ІНШІ ПИТАННЯ</b>

🔐 <b>Як авторизуватись?</b>
• Введіть команду <b>/start</b>
• Введіть свій <b>логін</b> та <b>пароль</b>
• Після входу відкриється головне меню

🔄 <b>Що робити, якщо забув пароль?</b>
• Зверніться до <b>адміністратора бота</b>
• Або до <b>свого класного керівника</b>

📱 <b>Чи можна користуватись ботом з телефону?</b>
• Так, бот <b>адаптований</b> для мобільних пристроїв
• Інтерфейс автоматично підлаштовується під екран

🐛 <b>Знайшли помилку?</b>
• Напишіть у <b>технічну підтримку</b>
• Вкажіть <b>скріншот</b> та <b>опишіть проблему</b>

💡 <b>Пропозиції щодо покращення:</b>
• Надішліть свої ідеї на email: suggestions@lyceum-bot.edu.ua
• Або через форму зворотного зв'язку на сайті
"""
    
    # Клавіатура для повернення
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад до категорій", callback_data="faq_categories")],
        [InlineKeyboardButton(text="🏠 Головне меню", callback_data="faq_back")]
    ])
    
    await edit_or_send_new(callback, text, kb)


async def show_categories(callback: types.CallbackQuery):
    """Показує категорії FAQ"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🏫 Розклад та корпуси", callback_data="faq_schedule"),
            InlineKeyboardButton(text="⏰ Розклад дзвінків", callback_data="faq_bell")
        ],
        [
            InlineKeyboardButton(text="📚 Правила ліцею", callback_data="faq_rules"),
            InlineKeyboardButton(text="👨‍🏫 Вчителі та кабінети", callback_data="faq_teachers")
        ],
        [
            InlineKeyboardButton(text="🔄 Заміни та додаткові", callback_data="faq_substitutions"),
            InlineKeyboardButton(text="📞 Контакти", callback_data="faq_contacts")
        ],
        [
            InlineKeyboardButton(text="❓ Інше", callback_data="faq_other"),
            InlineKeyboardButton(text="🏠 Головне меню", callback_data="faq_back")
        ]
    ])
    
    text = "❓ <b>Часті запитання</b>\n\nОберіть категорію, яка вас цікавить:"
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=kb
        )
    except Exception as e:
        if "message can't be edited" in str(e) or "message to edit not found" in str(e):
            await callback.message.delete()
            await callback.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=kb
            )
        else:
            raise e
    
    await callback.answer()


@dp.callback_query(F.data == "faq_back")
async def back_to_main_from_faq(callback: types.CallbackQuery):
    """Повернення в головне меню з FAQ"""
    user = get_user_by_tg_id(callback.from_user.id)
    role = user["role"] if user else "unauthorized"
    
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    await callback.message.answer(
        "🏠 <b>Головне меню</b>", 
        parse_mode="HTML", 
        reply_markup=get_main_menu(role)
    )
    await callback.answer()


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