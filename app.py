import os
import asyncio
import sqlite3
from urllib.parse import urlparse
import psycopg2
import datetime
import random
import re
import csv
import io
from flask import Flask, render_template, request, jsonify, Response
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Bot
from telegram.error import TelegramError

# --- 기본 설정 ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv('DATABASE_URL')
# Render의 영구 디스크 경로를 환경 변수에서 가져오고, 없으면 로컬 폴더를 사용
UPLOAD_FOLDER = os.getenv('RENDER_DISK_PATH', 'static/uploads')

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- 데이터베이스 연결 함수 (PostgreSQL & SQLite 호환) ---
def get_db_connection():
    if DATABASE_URL:
        # Render (PostgreSQL) 환경
        url = urlparse(DATABASE_URL)
        return psycopg2.connect(
            dbname=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port
        )
    else:
        # 로컬 (SQLite) 환경
        return sqlite3.connect('bot_config.db')

# --- 스핀택스 처리 함수 ---
def process_spintax(text):
    pattern = re.compile(r'{([^{}]*)}')
    while True:
        match = pattern.search(text)
        if not match:
            break
        options = match.group(1).split('|')
        choice = random.choice(options)
        text = text[:match.start()] + choice + text[match.end():]
    return text

# --- 데이터베이스 초기화 ---
def init_db():
    # Render에서는 PostgreSQL 문법, 로컬에서는 SQLite 문법 사용
    is_postgres = bool(DATABASE_URL)
    
    # 테이블 생성 SQL
    config_table_sql = '''
        CREATE TABLE IF NOT EXISTS config (
            id INTEGER PRIMARY KEY, message TEXT, photo TEXT, 
            interval_min INTEGER, interval_max INTEGER, scheduler_status TEXT,
            preview_id TEXT
        )'''
    promo_rooms_table_sql = f'''
        CREATE TABLE IF NOT EXISTS promo_rooms (
            id {'SERIAL' if is_postgres else 'INTEGER'} PRIMARY KEY {'AUTOINCREMENT' if not is_postgres else ''},
            chat_id TEXT NOT NULL UNIQUE,
            room_name TEXT,
            room_group TEXT DEFAULT '기본',
            is_active INTEGER DEFAULT 1,
            last_status TEXT DEFAULT '확인 안됨'
        )'''
    activity_log_table_sql = f'''
        CREATE TABLE IF NOT EXISTS activity_log (
            id {'SERIAL' if is_postgres else 'INTEGER'} PRIMARY KEY {'AUTOINCREMENT' if not is_postgres else ''},
            timestamp {'TIMESTAMPTZ' if is_postgres else 'DATETIME'} DEFAULT CURRENT_TIMESTAMP,
            details TEXT
        )'''

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(config_table_sql)
        cursor.execute(promo_rooms_table_sql)
        cursor.execute(activity_log_table_sql)
        
        cursor.execute("SELECT * FROM config WHERE id = 1")
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO config (id, message, photo, interval_min, interval_max, scheduler_status, preview_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (1, '', '', 30, 40, 'running', '')
            )
        conn.commit()

# --- 텔레그램 봇 공통 로직 ---
async def send_message_logic(chat_id, message_template, photo_filename):
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN이 설정되지 않았습니다.")
    
    bot = Bot(token=BOT_TOKEN)
    photo_path = os.path.join(app.config['UPLOAD_FOLDER'], photo_filename) if photo_filename else None
    final_message = process_spintax(message_template)

    if photo_path and os.path.exists(photo_path):
        with open(photo_path, 'rb') as photo_file:
            await bot.send_photo(chat_id=chat_id, photo=photo_file, caption=final_message)
    else:
        await bot.send_message(chat_id=chat_id, text=final_message)

# --- 스케줄러 실행 함수 ---
async def scheduled_send():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT scheduler_status FROM config WHERE id = 1")
        status_res = cursor.fetchone()
        status = status_res[0] if status_res else 'paused'
        
        if status != 'running':
            print("스케줄러가 '일시정지' 상태이므로 메시지를 발송하지 않습니다.")
            return

        cursor.execute("SELECT message, photo FROM config WHERE id = 1")
        config_res = cursor.fetchone()
        
        cursor.execute("SELECT chat_id FROM promo_rooms WHERE is_active = 1")
        active_rooms_res = cursor.fetchall()
        active_rooms = [row[0] for row in active_rooms_res]

    log_detail = ""
    try:
        if not config_res: raise ValueError("설정값이 DB에 없습니다.")
        message, photo = config_res[0], config_res[1]
        if not message or not active_rooms: raise ValueError("홍보 메시지 또는 대상 방이 설정되지 않았습니다.")

        for room_id in active_rooms:
            await send_message_logic(room_id, message, photo)
        
        log_detail = f"✅ {len(active_rooms)}개 활성 방에 메시지 발송 성공"
    except Exception as e:
        log_detail = f"❌ 스케줄러 오류: {e}"
    finally:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO activity_log (details) VALUES (%s)", (log_detail,))
            conn.commit()
        print(log_detail)

# --- 스케줄러 설정 ---
scheduler = BackgroundScheduler(daemon=True, timezone='Asia/Seoul')

# --- DB 헬퍼 함수 ---
def query_db(query, args=(), one=False):
    with get_db_connection() as conn:
        # psycopg2와 sqlite3의 파라미터 스타일이 다르므로 변환
        if DATABASE_URL:
            query = query.replace('?', '%s')
        
        cursor = conn.cursor()
        cursor.execute(query, args)
        
        if query.lower().strip().startswith(('insert', 'update', 'delete')):
            conn.commit()
            return

        rv = [dict((cursor.description[idx][0], value) for idx, value in enumerate(row)) for row in cursor.fetchall()]
        return (rv[0] if rv else None) if one else rv

def execute_db(query, args=()):
    with get_db_connection() as conn:
        if DATABASE_URL:
            query = query.replace('?', '%s')
        cursor = conn.cursor()
        cursor.execute(query, args)
        conn.commit()

# --- 관리자 페이지 및 API 라우트 ---
@app.route('/', methods=['GET', 'POST'])
def admin_page():
    page_message = None
    if request.method == 'POST':
        message, preview_id = request.form.get('message'), request.form.get('preview_id')
        interval_min = int(request.form.get('interval_min', 30))
        interval_max = int(request.form.get('interval_max', 40))
        photo = request.files.get('photo')
        
        current_config = query_db("SELECT interval_min, interval_max, photo FROM config WHERE id = 1", one=True)
        photo_filename = current_config['photo']

        if photo and photo.filename:
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            photo_filename = photo.filename
            photo.save(os.path.join(app.config['UPLOAD_FOLDER'], photo_filename))
        
        execute_db(
            "UPDATE config SET message=?, photo=?, interval_min=?, interval_max=?, preview_id=? WHERE id = 1",
            (message, photo_filename, interval_min, interval_max, preview_id)
        )

        if interval_min != current_config['interval_min'] or interval_max != current_config['interval_max']:
            next_run_minutes = random.randint(interval_min, interval_max)
            scheduler.reschedule_job('promo_job', trigger='interval', minutes=next_run_minutes)
            print(f"스케줄러 간격 변경. 다음 실행은 약 {next_run_minutes}분 후.")
        
        page_message = "✅ 설정이 성공적으로 저장되었습니다."

    # 대시보드 데이터 조회
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")
    sent_today_query = "SELECT COUNT(*) as count FROM activity_log WHERE details LIKE '✅%%' AND DATE(timestamp, '+9 hours') = ?" if not DATABASE_URL else "SELECT COUNT(*) as count FROM activity_log WHERE details LIKE '✅%%' AND (timestamp AT TIME ZONE 'utc' AT TIME ZONE 'Asia/Seoul')::date = CURRENT_DATE"
    sent_today = query_db(sent_today_query, (today,) if not DATABASE_URL else (), one=True)['count']
    
    log_query = "SELECT strftime('%Y-%m-%d %H:%M:%S', timestamp, '+9 hours') as ts, details FROM activity_log ORDER BY id DESC LIMIT 5" if not DATABASE_URL else "SELECT to_char(timestamp AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD HH24:MI:SS') as ts, details FROM activity_log ORDER BY id DESC LIMIT 5"
    recent_logs = query_db(log_query)
    
    promo_rooms = query_db("SELECT * FROM promo_rooms ORDER BY room_group, room_name")
    config = query_db("SELECT * FROM config WHERE id = 1", one=True)
    dashboard_data = {'sent_today': sent_today, 'recent_logs': recent_logs, 'room_count': len(promo_rooms)}
        
    return render_template('admin.html', config=config, message=page_message, dashboard=dashboard_data, promo_rooms=promo_rooms, scheduler_state=scheduler.state)


@app.route('/add_room', methods=['POST'])
def add_room():
    chat_id, room_name, room_group = request.form.get('chat_id'), request.form.get('room_name'), request.form.get('room_group')
    if not chat_id: return "Chat ID는 필수입니다.", 400
    try:
        execute_db("INSERT INTO promo_rooms (chat_id, room_name, room_group) VALUES (?, ?, ?)", (chat_id, room_name, room_group))
    except (psycopg2.IntegrityError, sqlite3.IntegrityError):
        return "이미 존재하는 Chat ID 입니다.", 400
    return "성공적으로 추가되었습니다."

# (이하 다른 API 라우트들도 execute_db, query_db 헬퍼 함수 사용)

@app.route('/delete_room/<int:room_id>', methods=['POST'])
def delete_room(room_id):
    execute_db("DELETE FROM promo_rooms WHERE id = ?", (room_id,))
    return "삭제되었습니다."

@app.route('/import_rooms', methods=['POST'])
def import_rooms():
    file = request.files.get('file')
    if not file: return "파일이 없습니다.", 400
    
    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    reader = csv.reader(stream)
    next(reader, None) # 헤더 스킵
    for row in reader:
        if len(row) >= 3:
            # ON CONFLICT는 DB마다 문법이 약간 다를 수 있으므로, 여기서는 IGNORE 방식을 사용
            try:
                execute_db("INSERT INTO promo_rooms (chat_id, room_name, room_group) VALUES (?, ?, ?)", (row[0], row[1], row[2]))
            except (psycopg2.IntegrityError, sqlite3.IntegrityError):
                continue # 중복이면 무시
    return "가져오기 완료!"

@app.route('/export_rooms')
def export_rooms():
    rows = query_db("SELECT chat_id, room_name, room_group FROM promo_rooms")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Chat ID', 'Room Name', 'Group'])
    for row in rows:
        writer.writerow([row['chat_id'], row['room_name'], row['room_group']])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment;filename=rooms.csv"})

@app.route('/toggle_scheduler/<string:action>', methods=['POST'])
def toggle_scheduler(action):
    status_to_set = 'paused' if action == 'pause' else 'running'
    try:
        if action == 'pause' and scheduler.state == 1:
            scheduler.pause()
        elif action == 'resume' and scheduler.state == 2:
            scheduler.resume()
        execute_db("UPDATE config SET scheduler_status = ? WHERE id = 1", (status_to_set,))
        return f"스케줄러가 {status_to_set} 상태가 되었습니다."
    except Exception as e:
        return f"오류 발생: {e}", 500

@app.route('/check_rooms', methods=['POST'])
async def check_rooms():
    rooms = query_db("SELECT id, chat_id FROM promo_rooms")
    bot = Bot(token=BOT_TOKEN)
    for room in rooms:
        status = ''
        try:
            chat = await bot.get_chat(chat_id=room['chat_id'])
            status = f"✅ OK ({chat.title})"
        except TelegramError as e:
            status = f"❌ Error: {e.message}"
        except Exception as e:
            status = f"❌ Error: {e}"
        
        execute_db("UPDATE promo_rooms SET last_status = ? WHERE id = ?", (status, room['id']))
            
    return "상태 확인 완료!"

@app.route('/preview', methods=['POST'])
async def preview_message():
    try:
        preview_id, message_template = request.form.get('preview_id'), request.form.get('message')
        photo = request.files.get('photo')
        if not preview_id or not message_template: return jsonify({'message': 'ID와 메시지를 입력해주세요.'}), 400

        final_message = process_spintax(message_template)
        if photo and photo.filename:
            photo.seek(0)
            await Bot(token=BOT_TOKEN).send_photo(chat_id=preview_id, photo=photo, caption=final_message)
        else:
            await Bot(token=BOT_TOKEN).send_message(chat_id=preview_id, text=final_message)
        return jsonify({'message': f'✅ {preview_id}로 미리보기 발송 성공.'})
    except Exception as e:
        return jsonify({'message': f'❌ 미리보기 전송 실패: {e}'}), 500

# --- 애플리케이션 실행 ---
init_db()

if __name__ == '__main__':
    # 로컬 실행을 위한 스케줄러 및 서버 시작
    config = query_db("SELECT interval_min, interval_max FROM config WHERE id = 1", one=True)
    interval_min = config['interval_min'] if config else 30
    interval_max = config['interval_max'] if config else 40

    scheduler.add_job(lambda: asyncio.run(scheduled_send()), 'interval', minutes=random.randint(interval_min, interval_max), id='promo_job')
    scheduler.start()
    
    execute_db("UPDATE config SET scheduler_status = ? WHERE id = 1", ('running',))

    print("데이터베이스와 스케줄러가 준비되었습니다.")
    app.run(host='0.0.0.0', port=8080, debug=True)
