import sqlite3
import os
import json
from datetime import datetime
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)
DB_PATH = "lyceum_v3.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== ГОЛОВНА СТОРІНКА ====================
@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/logout')
def logout():
    return "Logged out", 200

# ==================== СТАТИСТИКА ====================
@app.route('/api/stats')
def get_stats():
    conn = get_db()
    cursor = conn.cursor()
    
    # Вчителі
    cursor.execute("SELECT COUNT(*) FROM teachers")
    teachers_count = cursor.fetchone()[0]
    
    # Учні (з таблиць users_*)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'users_%'")
    student_tables = [row['name'] for row in cursor.fetchall()]
    total_students = 0
    for table in student_tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        total_students += cursor.fetchone()[0]
    
    # Уроки
    cursor.execute("SELECT COUNT(*) FROM schedule")
    lessons_count = cursor.fetchone()[0]
    
    # Заміни
    cursor.execute("SELECT COUNT(*) FROM substitutions")
    subs_count = cursor.fetchone()[0]
    
    # Додаткові заняття
    cursor.execute("SELECT COUNT(*) FROM extra_lessons")
    extra_count = cursor.fetchone()[0]
    
    # Класи
    cursor.execute("SELECT COUNT(DISTINCT group_name) FROM schedule WHERE group_name IS NOT NULL AND group_name != ''")
    classes_count = cursor.fetchone()[0]
    
    conn.close()
    return jsonify({
        "teachers": teachers_count,
        "students": total_students,
        "lessons": lessons_count,
        "substitutions": subs_count,
        "extra_lessons": extra_count,
        "classes": classes_count
    })

# ==================== ВЧИТЕЛІ ====================
@app.route('/api/teachers')
def get_teachers():
    conn = get_db()
    teachers = conn.execute("SELECT id, full_name, login, subject, class_teacher FROM teachers ORDER BY full_name").fetchall()
    conn.close()
    return jsonify([dict(t) for t in teachers])

@app.route('/api/add_teacher', methods=['POST'])
def add_teacher():
    data = request.json
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO teachers (full_name, login, password, subject, class_teacher)
            VALUES (?, ?, ?, ?, ?)
        """, (data['full_name'], data['login'], data['password'], data.get('subject', ''), data.get('group_name', '')))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/delete_teacher/<int:id>', methods=['DELETE'])
def delete_teacher(id):
    try:
        conn = get_db()
        conn.execute("DELETE FROM teachers WHERE id = ?", (id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ==================== УЧНІ ====================
@app.route('/api/students')
def get_students():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'users_%'")
    tables = [row['name'] for row in cursor.fetchall()]
    
    students = []
    for table in tables:
        group_name = table.replace('users_', '')
        rows = conn.execute(f"SELECT id, full_name, login, subgroup FROM {table} ORDER BY full_name").fetchall()
        for row in rows:
            students.append({
                "id": row['id'],
                "full_name": row['full_name'],
                "login": row['login'],
                "group_name": group_name,
                "subgroup": row['subgroup']
            })
    conn.close()
    return jsonify(students)

@app.route('/api/add_student', methods=['POST'])
def add_student():
    data = request.json
    group = data['group_name']
    table_name = f"users_{group}"
    try:
        conn = get_db()
        # Перевіряємо чи існує таблиця
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                login TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                subgroup TEXT
            )
        """)
        conn.execute(f"""
            INSERT INTO {table_name} (full_name, login, password, subgroup)
            VALUES (?, ?, ?, ?)
        """, (data['full_name'], data['login'], data['password'], data.get('subgroup')))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/delete_student/<int:id>', methods=['DELETE'])
def delete_student(id):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'users_%'")
        tables = [row['name'] for row in cursor.fetchall()]
        deleted = False
        for table in tables:
            cursor.execute(f"DELETE FROM {table} WHERE id = ?", (id,))
            if cursor.rowcount > 0:
                deleted = True
                break
        conn.commit()
        conn.close()
        return jsonify({"success": deleted})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ==================== КЛАСИ ====================
@app.route('/api/classes')
def get_classes():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT group_name FROM schedule WHERE group_name IS NOT NULL AND group_name != '' UNION SELECT DISTINCT REPLACE(name, 'users_', '') FROM sqlite_master WHERE type='table' AND name LIKE 'users_%'")
    classes = [row['group_name'] for row in cursor.fetchall() if row['group_name']]
    classes.sort()
    conn.close()
    return jsonify(classes)

# ==================== РОЗКЛАД ====================
@app.route('/api/schedule')
def get_schedule():
    day = request.args.get('day')
    group = request.args.get('group')
    conn = get_db()
    if group:
        lessons = conn.execute("""
            SELECT s.*, 
                   CASE s.lesson_num 
                       WHEN 1 THEN '08:30-09:15'
                       WHEN 2 THEN '09:25-10:10'
                       WHEN 3 THEN '10:25-11:10'
                       WHEN 4 THEN '11:20-12:05'
                       WHEN 5 THEN '12:15-13:00'
                       WHEN 6 THEN '13:10-13:55'
                       WHEN 7 THEN '14:05-14:50'
                       WHEN 8 THEN '15:00-15:45'
                   END as time
            FROM schedule s
            WHERE s.day = ? AND s.group_name = ?
            ORDER BY s.lesson_num
        """, (day, group)).fetchall()
    else:
        lessons = conn.execute("""
            SELECT s.*,
                   CASE s.lesson_num 
                       WHEN 1 THEN '08:30-09:15'
                       WHEN 2 THEN '09:25-10:10'
                       WHEN 3 THEN '10:25-11:10'
                       WHEN 4 THEN '11:20-12:05'
                       WHEN 5 THEN '12:15-13:00'
                       WHEN 6 THEN '13:10-13:55'
                       WHEN 7 THEN '14:05-14:50'
                       WHEN 8 THEN '15:00-15:45'
                   END as time
            FROM schedule s
            WHERE s.day = ?
            ORDER BY s.group_name, s.lesson_num
        """, (day,)).fetchall()
    conn.close()
    return jsonify([dict(l) for l in lessons])

@app.route('/api/all_schedule')
def get_all_schedule():
    day = request.args.get('day')
    conn = get_db()
    lessons = conn.execute("""
        SELECT s.*,
               CASE s.lesson_num 
                   WHEN 1 THEN '08:30-09:15'
                   WHEN 2 THEN '09:25-10:10'
                   WHEN 3 THEN '10:25-11:10'
                   WHEN 4 THEN '11:20-12:05'
                   WHEN 5 THEN '12:15-13:00'
                   WHEN 6 THEN '13:10-13:55'
                   WHEN 7 THEN '14:05-14:50'
                   WHEN 8 THEN '15:00-15:45'
               END as time
        FROM schedule s
        WHERE s.day = ?
        ORDER BY s.group_name, s.lesson_num
    """, (day,)).fetchall()
    conn.close()
    return jsonify([dict(l) for l in lessons])

@app.route('/api/add_lesson', methods=['POST'])
def add_lesson():
    data = request.json
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO schedule (day, lesson_num, subject, group_name, subgroup, teacher, room, is_extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (data['day'], data['lesson_num'], data['subject'], data['group_name'], 
              data.get('subgroup'), data['teacher'], data.get('room', ''), 
              1 if data.get('is_extra') else 0))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/delete_lesson/<int:id>', methods=['DELETE'])
def delete_lesson(id):
    try:
        conn = get_db()
        conn.execute("DELETE FROM schedule WHERE id = ?", (id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ==================== ЗАМІНИ ====================
@app.route('/api/substitutions')
def get_substitutions():
    conn = get_db()
    subs = conn.execute("""
        SELECT s.*, 
               sch.teacher as original_teacher,
               sch.room as original_room
        FROM substitutions s
        LEFT JOIN schedule sch ON sch.day = s.day AND sch.lesson_num = s.lesson_num AND sch.group_name = s.group_name
        ORDER BY s.id DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(sub) for sub in subs])

@app.route('/api/add_substitution', methods=['POST'])
def add_substitution():
    data = request.json
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO substitutions (day, lesson_num, group_name, subgroup, old_subject, old_teacher, old_room, new_subject, new_teacher, new_room, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (data['day'], data['lesson_num'], data['group_name'], data.get('subgroup'),
              data['old_subject'], data['old_teacher'], data.get('old_room', ''),
              data['new_subject'], data['new_teacher'], data.get('new_room', ''),
              datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/delete_substitution/<int:id>', methods=['DELETE'])
def delete_substitution(id):
    try:
        conn = get_db()
        conn.execute("DELETE FROM substitutions WHERE id = ?", (id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ==================== ДОДАТКОВІ ЗАНЯТТЯ ====================
@app.route('/api/extra_lessons')
def get_extra_lessons():
    conn = get_db()
    lessons = conn.execute("SELECT * FROM extra_lessons ORDER BY day, subject").fetchall()
    conn.close()
    return jsonify([dict(l) for l in lessons])

@app.route('/api/add_extra_lesson', methods=['POST'])
def add_extra_lesson():
    data = request.json
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO extra_lessons (day, subject, teacher, room)
            VALUES (?, ?, ?, ?)
        """, (data['day'], data['subject'], data['teacher'], data.get('room', '')))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/delete_extra_lesson/<int:id>', methods=['DELETE'])
def delete_extra_lesson(id):
    try:
        conn = get_db()
        conn.execute("DELETE FROM extra_lessons WHERE id = ?", (id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ==================== СИНХРОНІЗАЦІЯ КОРИСТУВАЧІВ ====================
@app.route('/api/sync_users', methods=['POST'])
def sync_users():
    try:
        conn = get_db()
        # Створюємо основну таблицю users якщо її немає
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                login TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                group_name TEXT,
                subgroup TEXT
            )
        """)
        
        # Очищаємо users
        conn.execute("DELETE FROM users")
        
        # Додаємо вчителів
        teachers = conn.execute("SELECT full_name, login, password FROM teachers").fetchall()
        for t in teachers:
            conn.execute("""
                INSERT INTO users (full_name, login, password, role)
                VALUES (?, ?, ?, 'teacher')
            """, (t['full_name'], t['login'], t['password']))
        
        # Додаємо учнів з усіх класів
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'users_%'")
        tables = [row['name'] for row in cursor.fetchall()]
        for table in tables:
            group_name = table.replace('users_', '')
            students = conn.execute(f"SELECT full_name, login, password, subgroup FROM {table}").fetchall()
            for s in students:
                conn.execute("""
                    INSERT INTO users (full_name, login, password, role, group_name, subgroup)
                    VALUES (?, ?, ?, 'student', ?, ?)
                """, (s['full_name'], s['login'], s['password'], group_name, s['subgroup']))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ==================== ЗМІНА ПАРОЛЮ ====================
@app.route('/api/change_password', methods=['POST'])
def change_password():
    data = request.json
    # Тут має бути перевірка старого паролю з сесії
    # Для демонстрації просто повертаємо успіх
    return jsonify({"success": True, "message": "Пароль змінено"})

# ==================== РЕЗЕРВНЕ КОПІЮВАННЯ ====================
@app.route('/api/backups')
def get_backups():
    backup_dir = "backups"
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
        return jsonify([])
    
    backups = []
    for file in os.listdir(backup_dir):
        if file.endswith('.db'):
            path = os.path.join(backup_dir, file)
            stat = os.stat(path)
            backups.append({
                "name": file,
                "size": stat.st_size,
                "modified": stat.st_mtime
            })
    backups.sort(key=lambda x: x['modified'], reverse=True)
    return jsonify(backups)

@app.route('/api/backup', methods=['POST'])
def create_backup():
    try:
        if not os.path.exists('backups'):
            os.makedirs('backups')
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"lyceum_backup_{timestamp}.db"
        backup_path = os.path.join('backups', backup_name)
        
        # Копіюємо базу даних
        import shutil
        shutil.copy2(DB_PATH, backup_path)
        
        return jsonify({"success": True, "backup": backup_name})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/restore_backup/<backup_name>', methods=['POST'])
def restore_backup(backup_name):
    try:
        backup_path = os.path.join('backups', backup_name)
        if not os.path.exists(backup_path):
            return jsonify({"success": False, "error": "Файл не знайдено"})
        
        # Відновлюємо з бекапу
        import shutil
        shutil.copy2(backup_path, DB_PATH)
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ==================== ЗАПУСК ====================
if __name__ == '__main__':
    # Створюємо необхідні директорії
    if not os.path.exists('templates'):
        os.makedirs('templates')
    if not os.path.exists('backups'):
        os.makedirs('backups')
    
    # Функція для створення таблиць, якщо їх немає
    def init_db():
        conn = get_db()
        cursor = conn.cursor()
        
        # Таблиця вчителів
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS teachers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                login TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                subject TEXT,
                class_teacher TEXT
            )
        """)
        
        # Таблиця розкладу
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                lesson_num INTEGER NOT NULL,
                subject TEXT NOT NULL,
                group_name TEXT NOT NULL,
                subgroup TEXT,
                teacher TEXT NOT NULL,
                room TEXT,
                is_extra INTEGER DEFAULT 0
            )
        """)
        
        # Таблиця замін
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS substitutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                lesson_num INTEGER NOT NULL,
                group_name TEXT NOT NULL,
                subgroup TEXT,
                old_subject TEXT NOT NULL,
                old_teacher TEXT NOT NULL,
                old_room TEXT,
                new_subject TEXT NOT NULL,
                new_teacher TEXT NOT NULL,
                new_room TEXT,
                date TEXT NOT NULL
            )
        """)
        
        # Таблиця додаткових занять
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS extra_lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                subject TEXT NOT NULL,
                teacher TEXT NOT NULL,
                room TEXT
            )
        """)
        
        conn.commit()
        conn.close()
    
    init_db()
    print("🚀 Адмін-панель запускається на http://127.0.0.1:5000")
    print("📁 База даних:", DB_PATH)
    app.run(debug=True, port=5000)