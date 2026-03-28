from flask import Flask, render_template, request, redirect, session, jsonify, send_from_directory
import sqlite3
import hashlib
import secrets
import datetime
import re
import json
import os
from functools import wraps
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "super_secret_key_lyceum"

DB_PATH = "lyceum_v3.db"
UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {'csv', 'json'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Дні тижня
DAYS_ORDER = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця"]

# Часи уроків для різних корпусів
LESSON_TIMES = {
    "1": {
        1: ("9:00", "9:40"),
        2: ("9:45", "10:25"),
        3: ("10:45", "11:25"),
        4: ("11:30", "12:10"),
        5: ("13:00", "13:40"),
        6: ("13:45", "14:25"),
        7: ("14:45", "15:25"),
        8: ("15:30", "16:10")
    },
    "2": {
        1: ("9:00", "9:40"),
        2: ("9:45", "10:25"),
        3: ("11:15", "11:55"),
        4: ("12:00", "12:40"),
        5: ("13:00", "13:40"),
        6: ("13:45", "14:25"),
        7: ("14:45", "15:25"),
        8: ("15:30", "16:10")
    }
}


def login_required(f):
    """Декоратор для перевірки авторизації"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({"success": False, "error": "Не авторизовано"}), 401
        return f(*args, **kwargs)
    return decorated_function


def get_db():
    return sqlite3.connect(DB_PATH)


def get_db_row_factory():
    """Підключається з row_factory для отримання словників"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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


def table_exists(table_name):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    exists = cur.fetchone() is not None
    conn.close()
    return exists


def sanitize_table_name(class_name: str) -> str:
    """Перетворює назву класу в безпечне ім'я таблиці"""
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


def get_corps_for_class(group_name: str) -> str:
    """Визначає корпус для класу"""
    match = re.match(r'(\d+)', group_name)
    class_num = int(match.group(1)) if match else 0
    
    if 5 <= class_num <= 6:
        return "2"
    return "1"


def get_lesson_time(lesson_num: int, group_name: str = None) -> str:
    """Повертає час уроку"""
    corps = get_corps_for_class(group_name) if group_name else "1"
    times = LESSON_TIMES.get(corps, LESSON_TIMES["1"])
    
    if lesson_num in times:
        return f"{times[lesson_num][0]} – {times[lesson_num][1]}"
    return ""


def get_all_classes() -> list:
    """Повертає список всіх класів"""
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


def get_all_teachers() -> list:
    """Повертає список всіх вчителів з таблиці users"""
    if not table_exists('users'):
        return []
    
    conn = get_db_row_factory()
    cur = conn.cursor()
    rows = cur.execute("SELECT full_name, subject FROM users WHERE role = 'teacher' ORDER BY full_name").fetchall()
    conn.close()
    return [(r['full_name'], r['subject'] or '') for r in rows]


def register_user_in_users(tg_id, username, login, password_hash, role, full_name, subject=None, group_name=None, subgroup=None):
    """Реєструє користувача в таблиці users"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Перевіряємо чи існує таблиця users
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not cur.fetchone():
            # Створюємо таблицю users якщо вона не існує
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
        
        # Перевіряємо чи існує вже користувач
        cur.execute("SELECT tg_id FROM users WHERE login=?", (login,))
        existing = cur.fetchone()
        
        if existing:
            # Оновлюємо існуючого
            cur.execute("""
                UPDATE users 
                SET tg_id = COALESCE(?, users.tg_id),
                    username = COALESCE(?, users.username),
                    password_hash = COALESCE(?, users.password_hash),
                    role = COALESCE(?, users.role),
                    full_name = COALESCE(?, users.full_name),
                    subject = COALESCE(?, users.subject),
                    group_name = COALESCE(?, users.group_name),
                    subgroup = COALESCE(?, users.subgroup)
                WHERE login = ?
            """, (tg_id, username, password_hash, role, full_name, subject, group_name, subgroup, login))
            print(f"✅ Оновлено користувача в users: {login}")
        else:
            # Додаємо нового
            cur.execute("""
                INSERT INTO users (tg_id, username, login, password_hash, role, full_name, subject, group_name, subgroup)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (tg_id, username, login, password_hash, role, full_name, subject, group_name, subgroup))
            print(f"✅ Додано користувача в users: {login}")
        
        conn.commit()
        
    except Exception as e:
        print(f"Помилка реєстрації в users: {e}")
        conn.rollback()
    finally:
        conn.close()


# ================= AUTH =================

@app.route('/')
def index():
    return redirect('/login')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login = request.form['username']
        password = request.form['password']
        
        if table_exists('users'):
            conn = get_db_row_factory()
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE login=? AND role='admin'", (login,))
            admin = cur.fetchone()
            conn.close()
            
            if admin and verify_password(password, admin['password_hash']):
                session['logged_in'] = True
                session['admin'] = login
                session['admin_id'] = admin['tg_id']
                session['admin_name'] = admin['full_name']
                return redirect('/dashboard')
        
        return render_template("login.html", error="Невірний логін або пароль")
    
    return render_template("login.html")


@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        return redirect('/login')
    return render_template("dashboard.html", 
                         admin_name=session.get('admin_name', session['admin']))


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# ================= API =================

@app.route('/api/stats')
@login_required
def stats():
    data = {
        "teachers": 0,
        "students": 0,
        "lessons": 0,
        "substitutions": 0,
        "extra_lessons": 0,
        "classes": len(get_all_classes())
    }
    
    if table_exists('users'):
        conn = get_db()
        cur = conn.cursor()
        data["teachers"] = cur.execute("SELECT COUNT(*) FROM users WHERE role='teacher'").fetchone()[0]
        data["students"] = cur.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0]
        conn.close()
    
    if table_exists('schedule'):
        conn = get_db()
        cur = conn.cursor()
        data["lessons"] = cur.execute("SELECT COUNT(*) FROM schedule").fetchone()[0]
        data["extra_lessons"] = cur.execute("SELECT COUNT(*) FROM schedule WHERE is_extra=1").fetchone()[0]
        conn.close()
    
    if table_exists('substitutions'):
        conn = get_db()
        cur = conn.cursor()
        data["substitutions"] = cur.execute("SELECT COUNT(*) FROM substitutions").fetchone()[0]
        conn.close()
    
    return jsonify(data)


# ---------- TEACHERS ----------
@app.route('/api/teachers')
@login_required
def get_teachers():
    if not table_exists('users'):
        return jsonify([])
    
    conn = get_db_row_factory()
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT tg_id as id, full_name, login, subject, group_name as class_teacher
        FROM users 
        WHERE role = 'teacher'
        ORDER BY full_name
    """).fetchall()
    conn.close()
    
    return jsonify([{
        "id": r['id'],
        "full_name": r['full_name'],
        "login": r['login'],
        "subject": r['subject'] or "-",
        "class_teacher": r['class_teacher'] or "-"
    } for r in rows])


@app.route('/api/add_teacher', methods=['POST'])
@login_required
def add_teacher():
    data = request.json
    
    temp_tg_id = int(hashlib.md5(data['login'].encode()).hexdigest()[:8], 16)
    password_hash = hash_password(data['password'])
    created_at = datetime.datetime.now().isoformat(timespec="seconds")
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Перевіряємо чи існує таблиця teachers
        if not table_exists('teachers'):
            # Створюємо таблицю teachers якщо вона не існує
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
        
        # Додаємо в teachers
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
        """, (temp_tg_id, None, data['login'], password_hash, data['full_name'], 
              data.get('subject'), data.get('group_name'), created_at))
        
        # Додаємо в users
        try:
            # Перевіряємо чи існує таблиця users
            if not table_exists('users'):
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
            
            # Додаємо або оновлюємо в users
            cur.execute("""
                INSERT INTO users (tg_id, username, login, password_hash, role, full_name, subject, group_name, subgroup)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(login) DO UPDATE SET
                    tg_id = excluded.tg_id,
                    username = excluded.username,
                    password_hash = excluded.password_hash,
                    role = excluded.role,
                    full_name = excluded.full_name,
                    subject = excluded.subject,
                    group_name = excluded.group_name,
                    subgroup = excluded.subgroup
            """, (temp_tg_id, None, data['login'], password_hash, 'teacher', 
                  data['full_name'], data.get('subject'), data.get('group_name'), None))
            
            print(f"✅ Вчителя додано в users: {data['login']}")
            
        except Exception as e:
            print(f"Помилка при додаванні в users: {e}")
            # Якщо помилка в users, відкочуємо транзакцію
            conn.rollback()
            raise
        
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Вчителя додано"})
        
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"Помилка при додаванні вчителя: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/edit_teacher/<int:id>', methods=['PUT'])
@login_required
def edit_teacher(id):
    data = request.json
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT login FROM users WHERE tg_id=? AND role='teacher'", (id,))
    teacher = cur.fetchone()
    
    if teacher:
        cur.execute("""
            UPDATE users 
            SET full_name=?, subject=?, group_name=?
            WHERE tg_id=? AND role='teacher'
        """, (data['full_name'], data.get('subject'), data.get('group_name'), id))
        
        if table_exists('teachers'):
            cur.execute("""
                UPDATE teachers 
                SET full_name=?, subject=?, class_teacher=?
                WHERE login=?
            """, (data['full_name'], data.get('subject'), data.get('group_name'), teacher[0]))
    
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/api/delete_teacher/<int:id>', methods=['DELETE'])
@login_required
def delete_teacher(id):
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT login FROM users WHERE tg_id=? AND role='teacher'", (id,))
    teacher = cur.fetchone()
    
    if teacher:
        cur.execute("DELETE FROM users WHERE login=? AND role='teacher'", (teacher[0],))
        
        if table_exists('teachers'):
            cur.execute("DELETE FROM teachers WHERE login=?", (teacher[0],))
    
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ---------- STUDENTS ----------
@app.route('/api/students')
@login_required
def get_students():
    if table_exists('users'):
        conn = get_db_row_factory()
        cur = conn.cursor()
        rows = cur.execute("""
            SELECT tg_id as id, full_name, login, group_name, subgroup
            FROM users 
            WHERE role = 'student'
            ORDER BY group_name, full_name
        """).fetchall()
        conn.close()
        
        return jsonify([{
            "id": r['id'],
            "full_name": r['full_name'],
            "login": r['login'],
            "group_name": r['group_name'],
            "subgroup": r['subgroup'] or ""
        } for r in rows])
    
    return jsonify([])


@app.route('/api/add_student', methods=['POST'])
@login_required
def add_student():
    data = request.json
    
    group_name = data['group_name']
    table_name = sanitize_table_name(group_name)
    
    if not table_exists(table_name):
        conn = get_db()
        cur = conn.cursor()
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
        conn.commit()
        conn.close()
    
    conn = get_db()
    cur = conn.cursor()
    
    password_hash = hash_password(data['password'])
    created_at = datetime.datetime.now().isoformat(timespec="seconds")
    subgroup = data.get('subgroup') or None
    temp_tg_id = int(hashlib.md5(data['login'].encode()).hexdigest()[:8], 16)
    
    try:
        cur.execute(f"""
            INSERT INTO {table_name} (tg_id, username, login, password_hash, full_name, subgroup, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(login) DO UPDATE SET
                tg_id = excluded.tg_id,
                username = excluded.username,
                password_hash = excluded.password_hash,
                full_name = excluded.full_name,
                subgroup = excluded.subgroup
        """, (temp_tg_id, None, data['login'], password_hash, data['full_name'], subgroup, created_at))
        
        register_user_in_users(
            tg_id=temp_tg_id,
            username=None,
            login=data['login'],
            password_hash=password_hash,
            role='student',
            full_name=data['full_name'],
            subject=None,
            group_name=group_name,
            subgroup=subgroup
        )
        
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/edit_student/<int:id>', methods=['PUT'])
@login_required
def edit_student(id):
    data = request.json
    group_name = data.get('group_name')
    
    if not group_name:
        for class_name in get_all_classes():
            table_name = sanitize_table_name(class_name)
            if table_exists(table_name):
                conn = get_db()
                cur = conn.cursor()
                cur.execute(f"SELECT id, login FROM {table_name} WHERE id=?", (id,))
                student = cur.fetchone()
                if student:
                    cur.execute(f"""
                        UPDATE {table_name} 
                        SET full_name=?, subgroup=?
                        WHERE id=?
                    """, (data['full_name'], data.get('subgroup') or None, id))
                    
                    if table_exists('users'):
                        register_user_in_users(
                            tg_id=None,
                            username=None,
                            login=student[1],
                            password_hash=None,
                            role='student',
                            full_name=data['full_name'],
                            subject=None,
                            group_name=class_name,
                            subgroup=data.get('subgroup') or None
                        )
                    
                    conn.commit()
                    conn.close()
                    return jsonify({"success": True})
                conn.close()
        return jsonify({"success": False, "error": "Учня не знайдено"})
    
    table_name = sanitize_table_name(group_name)
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute(f"SELECT login FROM {table_name} WHERE id=?", (id,))
    student = cur.fetchone()
    
    cur.execute(f"""
        UPDATE {table_name} 
        SET full_name=?, subgroup=?
        WHERE id=?
    """, (data['full_name'], data.get('subgroup') or None, id))
    
    if student and table_exists('users'):
        register_user_in_users(
            tg_id=None,
            username=None,
            login=student[0],
            password_hash=None,
            role='student',
            full_name=data['full_name'],
            subject=None,
            group_name=group_name,
            subgroup=data.get('subgroup') or None
        )
    
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/api/delete_student/<int:id>', methods=['DELETE'])
@login_required
def delete_student(id):
    for class_name in get_all_classes():
        table_name = sanitize_table_name(class_name)
        if table_exists(table_name):
            conn = get_db()
            cur = conn.cursor()
            cur.execute(f"SELECT id, login FROM {table_name} WHERE id=?", (id,))
            student = cur.fetchone()
            if student:
                if table_exists('users'):
                    cur.execute("DELETE FROM users WHERE login=? AND role='student'", (student[1],))
                cur.execute(f"DELETE FROM {table_name} WHERE id=?", (id,))
                conn.commit()
                conn.close()
                return jsonify({"success": True})
            conn.close()
    return jsonify({"success": False, "error": "Учня не знайдено"})


# ---------- SCHEDULE ----------
@app.route('/api/schedule')
@login_required
def get_schedule():
    if not table_exists('schedule'):
        return jsonify([])
    
    day = request.args.get('day')
    group = request.args.get('group')
    
    conn = get_db_row_factory()
    cur = conn.cursor()
    
    if group:
        rows = cur.execute("""
            SELECT * FROM schedule 
            WHERE day=? AND group_name=?
            ORDER BY lesson_num, subgroup
        """, (day, group)).fetchall()
    else:
        rows = cur.execute("""
            SELECT * FROM schedule WHERE day=?
            ORDER BY lesson_num, group_name, subgroup
        """, (day,)).fetchall()
    conn.close()
    
    result = []
    for r in rows:
        lesson_info = {
            "id": r['id'],
            "lesson_num": r['lesson_num'],
            "subject": r['subject'],
            "group_name": r['group_name'],
            "subgroup": r['subgroup'] or "",
            "teacher": r['teacher'],
            "room": r['room'] or "-",
            "is_extra": r['is_extra'] or 0,
            "time": get_lesson_time(r['lesson_num'], r['group_name'])
        }
        result.append(lesson_info)
    
    return jsonify(result)


@app.route('/api/all_schedule')
@login_required
def get_all_schedule():
    if not table_exists('schedule'):
        return jsonify([])
    
    day = request.args.get('day')
    
    conn = get_db_row_factory()
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT * FROM schedule 
        WHERE day=?
        ORDER BY lesson_num, group_name, subgroup
    """, (day,)).fetchall()
    conn.close()
    
    result = []
    for r in rows:
        lesson_info = {
            "id": r['id'],
            "lesson_num": r['lesson_num'],
            "subject": r['subject'],
            "group_name": r['group_name'],
            "subgroup": r['subgroup'] or "",
            "teacher": r['teacher'],
            "room": r['room'] or "-",
            "is_extra": r['is_extra'] or 0,
            "time": get_lesson_time(r['lesson_num'], r['group_name'])
        }
        result.append(lesson_info)
    
    return jsonify(result)


@app.route('/api/schedule_by_class/<class_name>')
@login_required
def get_schedule_by_class(class_name):
    if not table_exists('schedule'):
        return jsonify([])
    
    conn = get_db_row_factory()
    cur = conn.cursor()
    
    rows = cur.execute("""
        SELECT day, lesson_num, subject, subgroup, teacher, room
        FROM schedule 
        WHERE group_name=?
        ORDER BY day, lesson_num, subgroup
    """, (class_name,)).fetchall()
    conn.close()
    
    result = {}
    for r in rows:
        day = r['day']
        if day not in result:
            result[day] = []
        lesson_info = {
            "lesson_num": r['lesson_num'],
            "subject": r['subject'],
            "subgroup": r['subgroup'] or "",
            "teacher": r['teacher'],
            "room": r['room'] or "-",
            "time": get_lesson_time(r['lesson_num'], class_name)
        }
        result[day].append(lesson_info)
    
    return jsonify(result)


@app.route('/api/add_lesson', methods=['POST'])
@login_required
def add_lesson():
    data = request.json
    
    if not table_exists('schedule'):
        return jsonify({"success": False, "error": "Таблиця schedule не існує"})
    
    conn = get_db()
    cur = conn.cursor()
    
    subgroup = data['subgroup'] if data.get('subgroup') else None
    is_extra = 1 if data.get('is_extra') else 0
    
    try:
        cur.execute("""
            INSERT INTO schedule (day, lesson_num, subject, group_name, subgroup, teacher, room, is_extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day, lesson_num, group_name, subgroup) 
            DO UPDATE SET 
                subject = excluded.subject, 
                teacher = excluded.teacher, 
                room = excluded.room,
                is_extra = excluded.is_extra
        """, (data['day'], data['lesson_num'], data['subject'],
              data['group_name'], subgroup, data['teacher'], data['room'], is_extra))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/edit_lesson/<int:id>', methods=['PUT'])
@login_required
def edit_lesson(id):
    data = request.json
    conn = get_db()
    cur = conn.cursor()
    
    subgroup = data['subgroup'] if data.get('subgroup') else None
    is_extra = 1 if data.get('is_extra') else 0
    
    cur.execute("""
        UPDATE schedule 
        SET lesson_num=?, subject=?, group_name=?, subgroup=?, teacher=?, room=?, is_extra=?
        WHERE id=?
    """, (data['lesson_num'], data['subject'], data['group_name'], 
          subgroup, data['teacher'], data['room'], is_extra, id))
    
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/api/delete_lesson/<int:id>', methods=['DELETE'])
@login_required
def delete_lesson(id):
    conn = get_db()
    conn.execute("DELETE FROM schedule WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/api/delete_lessons_by_group', methods=['POST'])
@login_required
def delete_lessons_by_group():
    data = request.json
    
    conn = get_db()
    cur = conn.cursor()
    
    if data.get('subgroup'):
        cur.execute("""
            DELETE FROM schedule 
            WHERE day=? AND lesson_num=? AND group_name=? AND subgroup=?
        """, (data['day'], data['lesson_num'], data['group_name'], data['subgroup']))
    else:
        cur.execute("""
            DELETE FROM schedule 
            WHERE day=? AND lesson_num=? AND group_name=? AND subgroup IS NULL
        """, (data['day'], data['lesson_num'], data['group_name']))
    
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    
    return jsonify({"success": True, "deleted": deleted})


# ---------- SUBSTITUTIONS ----------
@app.route('/api/substitutions')
@login_required
def get_substitutions():
    if not table_exists('substitutions'):
        return jsonify([])
    
    day = request.args.get('day')
    
    conn = get_db_row_factory()
    cur = conn.cursor()
    
    if day:
        rows = cur.execute("""
            SELECT id, day, lesson_num, group_name, subgroup,
                   old_subject, old_teacher, old_room,
                   new_subject, new_teacher, new_room, created_at
            FROM substitutions
            WHERE day = ?
            ORDER BY lesson_num, group_name
        """, (day,)).fetchall()
    else:
        rows = cur.execute("""
            SELECT id, day, lesson_num, group_name, subgroup,
                   old_subject, old_teacher, old_room,
                   new_subject, new_teacher, new_room, created_at
            FROM substitutions
            ORDER BY id DESC
            LIMIT 100
        """).fetchall()
    conn.close()
    
    return jsonify([{
        "id": r['id'],
        "day": r['day'],
        "lesson_num": r['lesson_num'],
        "group_name": r['group_name'],
        "subgroup": r['subgroup'] or "",
        "old_subject": r['old_subject'],
        "old_teacher": r['old_teacher'],
        "old_room": r['old_room'] or "-",
        "new_subject": r['new_subject'],
        "new_teacher": r['new_teacher'],
        "new_room": r['new_room'] or "-",
        "created_at": r['created_at']
    } for r in rows])


@app.route('/api/add_substitution', methods=['POST'])
@login_required
def add_substitution():
    data = request.json
    
    if not table_exists('substitutions'):
        return jsonify({"success": False, "error": "Таблиця substitutions не існує"})
    
    conn = get_db()
    cur = conn.cursor()
    
    created_at = datetime.datetime.now().isoformat(timespec="seconds")
    subgroup = data.get('subgroup') or None
    old_room = data.get('old_room') or None
    new_room = data.get('new_room') or None
    
    try:
        cur.execute("""
            INSERT INTO substitutions (
                day, lesson_num, group_name, subgroup,
                old_subject, old_teacher, old_room,
                new_subject, new_teacher, new_room, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['day'], data['lesson_num'], data['group_name'], subgroup,
            data['old_subject'], data['old_teacher'], old_room,
            data['new_subject'], data['new_teacher'], new_room, created_at
        ))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/delete_substitution/<int:id>', methods=['DELETE'])
@login_required
def delete_substitution(id):
    conn = get_db()
    conn.execute("DELETE FROM substitutions WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ---------- EXTRA LESSONS ----------
@app.route('/api/extra_lessons')
@login_required
def get_extra_lessons():
    if not table_exists('schedule'):
        return jsonify([])
    
    conn = get_db_row_factory()
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT id, day, lesson_num, subject, teacher, room
        FROM schedule
        WHERE is_extra = 1
        ORDER BY day, lesson_num
    """).fetchall()
    conn.close()
    
    return jsonify([{
        "id": r['id'],
        "day": r['day'],
        "lesson_num": r['lesson_num'],
        "subject": r['subject'],
        "teacher": r['teacher'],
        "room": r['room'] or "-"
    } for r in rows])


@app.route('/api/add_extra_lesson', methods=['POST'])
@login_required
def add_extra_lesson():
    data = request.json
    
    if not table_exists('schedule'):
        return jsonify({"success": False, "error": "Таблиця schedule не існує"})
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            INSERT INTO schedule (day, lesson_num, subject, group_name, teacher, room, is_extra)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            data['day'], data['lesson_num'] or 9, data['subject'],
            'ALL', data['teacher'], data['room'], 1
        ))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/delete_extra_lesson/<int:id>', methods=['DELETE'])
@login_required
def delete_extra_lesson(id):
    conn = get_db()
    conn.execute("DELETE FROM schedule WHERE id=? AND is_extra=1", (id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ---------- CHANGE PASSWORD ----------
@app.route('/api/change_password', methods=['POST'])
@login_required
def change_password():
    data = request.json
    
    if table_exists('users'):
        conn = get_db_row_factory()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE login=? AND role='admin'", (session.get('admin'),))
        admin = cur.fetchone()
        
        if admin and verify_password(data['old_password'], admin['password_hash']):
            new_login = data.get('new_login')
            new_password = data.get('new_password')
            
            if new_login:
                cur.execute("UPDATE users SET login=? WHERE login=? AND role='admin'", 
                           (new_login, session.get('admin')))
                session['admin'] = new_login
            
            if new_password:
                new_hash = hash_password(new_password)
                cur.execute("UPDATE users SET password_hash=? WHERE login=? AND role='admin'", 
                           (new_hash, new_login or session.get('admin')))
            
            if table_exists('admins'):
                if new_login:
                    cur.execute("UPDATE admins SET login=? WHERE login=?", 
                               (new_login, session.get('admin')))
                if new_password:
                    new_hash = hash_password(new_password)
                    cur.execute("UPDATE admins SET password_hash=? WHERE login=?", 
                               (new_hash, new_login or session.get('admin')))
            
            conn.commit()
            conn.close()
            return jsonify({"success": True})
        conn.close()
    
    return jsonify({"success": False, "error": "Невірний старий пароль"})


# ---------- CLASSES ----------
@app.route('/api/classes')
@login_required
def get_classes():
    return jsonify(get_all_classes())


@app.route('/api/class_info/<class_name>')
@login_required
def get_class_info(class_name):
    table_name = sanitize_table_name(class_name)
    
    if not table_exists(table_name):
        return jsonify({"students": []})
    
    conn = get_db_row_factory()
    cur = conn.cursor()
    students = cur.execute(f"""
        SELECT id, full_name, login, subgroup
        FROM {table_name}
        ORDER BY full_name
    """).fetchall()
    conn.close()
    
    return jsonify({
        "class_name": class_name,
        "students": [{
            "id": s['id'],
            "full_name": s['full_name'],
            "login": s['login'],
            "subgroup": s['subgroup'] or ""
        } for s in students]
    })


# ---------- SYNC USERS ----------
@app.route('/api/sync_users', methods=['POST'])
@login_required
def sync_users():
    try:
        if table_exists('teachers'):
            conn = get_db_row_factory()
            cur = conn.cursor()
            teachers = cur.execute("SELECT tg_id, username, login, password_hash, full_name, subject, class_teacher FROM teachers").fetchall()
            conn.close()
            
            for teacher in teachers:
                register_user_in_users(
                    tg_id=teacher['tg_id'],
                    username=teacher['username'],
                    login=teacher['login'],
                    password_hash=teacher['password_hash'],
                    role='teacher',
                    full_name=teacher['full_name'],
                    subject=teacher['subject'],
                    group_name=teacher['class_teacher'],
                    subgroup=None
                )
        
        for class_name in get_all_classes():
            table_name = sanitize_table_name(class_name)
            if table_exists(table_name):
                conn = get_db_row_factory()
                cur = conn.cursor()
                students = cur.execute(f"SELECT tg_id, username, login, password_hash, full_name, subgroup FROM {table_name}").fetchall()
                conn.close()
                
                for student in students:
                    register_user_in_users(
                        tg_id=student['tg_id'],
                        username=student['username'],
                        login=student['login'],
                        password_hash=student['password_hash'],
                        role='student',
                        full_name=student['full_name'],
                        subject=None,
                        group_name=class_name,
                        subgroup=student['subgroup']
                    )
        
        if table_exists('admins'):
            conn = get_db_row_factory()
            cur = conn.cursor()
            admins = cur.execute("SELECT tg_id, username, login, password_hash, full_name FROM admins").fetchall()
            conn.close()
            
            for admin in admins:
                register_user_in_users(
                    tg_id=admin['tg_id'],
                    username=admin['username'],
                    login=admin['login'],
                    password_hash=admin['password_hash'],
                    role='admin',
                    full_name=admin['full_name'],
                    subject=None,
                    group_name=None,
                    subgroup=None
                )
        
        return jsonify({"success": True, "message": "Синхронізацію завершено"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ---------- BACKUP ----------
@app.route('/api/backup', methods=['POST'])
@login_required
def create_backup():
    backup_name = f"backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    backup_path = os.path.join(UPLOAD_FOLDER, backup_name)
    
    try:
        import shutil
        shutil.copy2(DB_PATH, backup_path)
        return jsonify({"success": True, "backup": backup_name})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/backups', methods=['GET'])
@login_required
def list_backups():
    backups = []
    for f in os.listdir(UPLOAD_FOLDER):
        if f.startswith('backup_') and f.endswith('.db'):
            stat = os.stat(os.path.join(UPLOAD_FOLDER, f))
            backups.append({
                "name": f,
                "size": stat.st_size,
                "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
    backups.sort(key=lambda x: x['modified'], reverse=True)
    return jsonify(backups)


@app.route('/api/restore_backup/<backup_name>', methods=['POST'])
@login_required
def restore_backup(backup_name):
    backup_path = os.path.join(UPLOAD_FOLDER, backup_name)
    
    if not os.path.exists(backup_path):
        return jsonify({"success": False, "error": "Резервна копія не знайдена"})
    
    try:
        import shutil
        shutil.copy2(backup_path, DB_PATH)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ---------- STATIC FILES ----------
@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)


# ================= RUN =================

if __name__ == "__main__":
    print("=" * 50)
    print("🚀 Веб-адмін панель запущена!")
    print("🔗 Відкрийте: http://localhost:5000")
    print("🔑 Логін: admin")
    print("🔑 Пароль: admin123")
    print("=" * 50)
    app.run(debug=True, host='127.0.0.1', port=5000)