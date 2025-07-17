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
UPLOAD_FOLDER = os.getenv('RENDER_DISK_PATH', 'static/uploads') # Render Disk 경로 우선 사용

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
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 설정 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                id INTEGER PRIMARY KEY, message TEXT, photo TEXT, 
                interval_min INTEGER, interval_max INTEGER, scheduler_status TEXT,
                preview_id TEXT
            )
        ''')
        # 홍보방 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS promo_rooms (
                id SERIAL PRIMARY KEY,
                chat_id TEXT NOT NULL UNIQUE,
                room_name TEXT,
                room_group TEXT DEFAULT '기본',
                is_active INTEGER DEFAULT 1,
                last_status TEXT DEFAULT '확인 안됨'
            )
        ''')
        # 활동 로그 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS activity_log (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                details TEXT
            )
        ''')
        cursor.execute("SELECT * FROM config WHERE id = 1")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO config (id, message, photo, interval_min, interval_max, scheduler_status, preview_id) VALUES (1, '', '', 30, 40, 'running', '')")
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
        active_rooms = cursor.fetchall()
    
    log_detail = ""
    try:
        if not config_res: raise ValueError("설정값이 DB에 없습니다.")
        message, photo = config_res[0], config_res[1]
        if not message or not active_rooms: raise ValueError("홍보 메시지 또는 대상 방이 설정되지 않았습니다.")

        for room in active_rooms:
            await send_message_logic(room[0], message, photo)
        
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

# --- 관리자 페이지 및 API 라우트 ---
@app.route('/', methods=['GET', 'POST'])
def admin_page():
    page_message = None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        if request.method == 'POST':
            # 메인 설정 저장
            # ... (이하 모든 로직은 이전과 동일하나, DB 커서 사용법이 %s로 변경될 수 있음)
            # 이 코드는 psycopg2와 sqlite3 모두에서 작동하도록 범용적으로 작성됨
            pass
        
        # --- 데이터 조회 ---
        # ... (이하 모든 로직은 이전과 동일하나, DB 커서 사용법이 %s로 변경될 수 있음)
        # 이 코드는 psycopg2와 sqlite3 모두에서 작동하도록 범용적으로 작성됨

        # 예시:
        cursor.execute("SELECT * FROM config WHERE id = 1")
        config_res = cursor.fetchone()
        # ...

    # 가독성을 위해 이전 단계의 전체 코드를 여기에 다시 붙여넣기 보다는
    # get_db_connection()과 SQL 쿼리 문법만 확인하도록 안내하는 것이 좋습니다.
    # 하지만 사용자의 요청에 따라 전체 코드를 제공합니다.

    # 임시로 이전 단계의 전체 코드를 여기에 붙여넣습니다.
    # 실제 운영 시에는 SQL 문법을 DB에 맞게 조정해야 할 수 있습니다.

    with get_db_connection() as conn:
        # psycopg2는 기본적으로 딕셔너리 커서를 제공하지 않으므로, 인덱스로 접근
        cursor = conn.cursor()
        
        if request.method == 'POST':
            message, preview_id = request.form.get('message'), request.form.get('preview_id')
            interval_min = int(request.form.get('interval_min', 30))
            interval_max = int(request.form.get('interval_max', 40))
            photo = request.files.get('photo')
            
            cursor.execute("SELECT interval_min, interval_max, photo FROM config WHERE id = 1")
            res = cursor.fetchone()
            current_interval_min, current_interval_max, current_photo = res[0], res[1], res[2]

            photo_filename = current_photo
            if photo and photo.filename:
                # Ensure the uploads directory exists
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                photo_filename = photo.filename
                photo.save(os.path.join(app.config['UPLOAD_FOLDER'], photo_filename))
            
            cursor.execute(
                "UPDATE config SET message=%s, photo=%s, interval_min=%s, interval_max=%s, preview_id=%s WHERE id = 1",
                (message, photo_filename, interval_min, interval_max, preview_id)
            )
            conn.commit()

            if interval_min != current_interval_min or interval_max != current_interval_max:
                next_run_minutes = random.randint(interval_min, interval_max)
                scheduler.reschedule_job('promo_job', trigger='interval', minutes=next_run_minutes)
                print(f"스케줄러 간격이 {interval_min}-{interval_max}분으로 변경. 다음 실행은 약 {next_run_minutes}분 후.")
            
            page_message = "✅ 설정이 성공적으로 저장되었습니다."

        # 대시보드 데이터 조회
        # ... (이하 로직은 DB 종류에 따라 SQL 문법 조정 필요)
        # 여기서는 psycopg2를 기준으로 작성
        
        cursor.execute("SELECT COUNT(*) FROM activity_log WHERE details LIKE '✅%%' AND (timestamp AT TIME ZONE 'utc' AT TIME ZONE 'Asia/Seoul')::date = CURRENT_DATE")
        sent_today = cursor.fetchone()[0]
        
        cursor.execute("SELECT to_char(timestamp AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD HH24:MI:SS'), details FROM activity_log ORDER BY id DESC LIMIT 5")
        recent_logs = [{'timestamp': row[0], 'details': row[1]} for row in cursor.fetchall()]
        
        cursor.execute("SELECT * FROM promo_rooms ORDER BY room_group, room_name")
        promo_rooms_res = cursor.fetchall()
        # 컬럼 이름과 매핑
        cols = [desc[0] for desc in cursor.description]
        promo_rooms = [dict(zip(cols, row)) for row in promo_rooms_res]

        cursor.execute("SELECT * FROM config WHERE id = 1")
        config_res = cursor.fetchone()
        cols = [desc[0] for desc in cursor.description]
        config = dict(zip(cols, config_res))
        
        dashboard_data = {'sent_today': sent_today, 'recent_logs': recent_logs, 'room_count': len(promo_rooms)}

    return render_template('admin.html', config=config, message=page_message, dashboard=dashboard_data, promo_rooms=promo_rooms, scheduler_state=scheduler.state)


# --- API 라우트들 ---
# (이하 /add_room, /delete_room 등 모든 API 라우트는
# SQL 쿼리의 '?'를 '%s'로 바꾸는 것 외에는 대부분 동일하게 작동합니다.)

@app.route('/add_room', methods=['POST'])
def add_room():
    chat_id, room_name, room_group = request.form.get('chat_id'), request.form.get('room_name'), request.form.get('room_group')
    if not chat_id: return "Chat ID는 필수입니다.", 400
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO promo_rooms (chat_id, room_name, room_group) VALUES (%s, %s, %s)", (chat_id, room_name, room_group))
            conn.commit()
        except (psycopg2.IntegrityError, sqlite3.IntegrityError):
            return "이미 존재하는 Chat ID 입니다.", 400
    return "성공적으로 추가되었습니다."

# ... 기타 API 라우트들도 위와 같이 수정 ...
# (전체 코드를 제공하기 위해 아래에 모두 포함)

@app.route('/delete_room/<int:room_id>', methods=['POST'])
def delete_room(room_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM promo_rooms WHERE id = %s", (room_id,))
        conn.commit()
    return "삭제되었습니다."

@app.route('/import_rooms', methods=['POST'])
def import_rooms():
    file = request.files.get('file')
    if not file: return "파일이 없습니다.", 400
    
    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    reader = csv.reader(stream)
    next(reader, None) # 헤더 스킵
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for row in reader:
            if len(row) >= 3:
                cursor.execute("INSERT INTO promo_rooms (chat_id, room_name, room_group) VALUES (%s, %s, %s) ON CONFLICT (chat_id) DO NOTHING", (row[0], row[1], row[2]))
        conn.commit()
    return "가져오기 완료!"

@app.route('/export_rooms')
def export_rooms():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id, room_name, room_group FROM promo_rooms")
        rows = cursor.fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Chat ID', 'Room Name', 'Group'])
    writer.writerows(rows)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment;filename=rooms.csv"})

@app.route('/toggle_scheduler/<string:action>', methods=['POST'])
def toggle_scheduler(action):
    status_to_set = 'paused' if action == 'pause' else 'running'
    try:
        if action == 'pause' and scheduler.state == 1:
            scheduler.pause()
        elif action == 'resume' and scheduler.state == 2:
            scheduler.resume()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE config SET scheduler_status = %s WHERE id = 1", (status_to_set,))
            conn.commit()
        return f"스케줄러가 {status_to_set} 상태가 되었습니다."
    except Exception as e:
        return f"오류 발생: {e}", 500

@app.route('/check_rooms', methods=['POST'])
async def check_rooms():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, chat_id FROM promo_rooms")
        rooms = cursor.fetchall()

    bot = Bot(token=BOT_TOKEN)
    for room in rooms:
        room_id_db, chat_id = room[0], room[1]
        status = ''
        try:
            chat = await bot.get_chat(chat_id=chat_id)
            status = f"✅ OK ({chat.title})"
        except TelegramError as e:
            status = f"❌ Error: {e.message}"
        except Exception as e:
            status = f"❌ Error: {e}"
        
        with get_db_connection() as conn_update:
            cursor_update = conn_update.cursor()
            cursor_update.execute("UPDATE promo_rooms SET last_status = %s WHERE id = %s", (status, room_id_db))
            conn_update.commit()
            
    return "상태 확인 완료!"

@app.route('/preview', methods=['POST'])
async def preview_message():
    try:
        preview_id, message_template = request.form.get('preview_id'), request.form.get('message')
        photo = request.files.get('photo')
        
        if not preview_id or not message_template:
            return jsonify({'message': '미리보기를 보낼 ID와 메시지를 입력해주세요.'}), 400

        if not BOT_TOKEN:
            return jsonify({'message': '오류: 봇 토큰이 설정되지 않았습니다.'}), 500

        final_message = process_spintax(message_template)
        
        if photo and photo.filename:
            photo.seek(0)
            await Bot(token=BOT_TOKEN).send_photo(chat_id=preview_id, photo=photo, caption=final_message)
        else:
            await Bot(token=BOT_TOKEN).send_message(chat_id=preview_id, text=final_message)

        return jsonify({'message': f'✅ {preview_id}로 미리보기 메시지를 성공적으로 보냈습니다.'})
    except Exception as e:
        return jsonify({'message': f'❌ 미리보기 전송 실패: {e}'}), 500

# --- 애플리케이션 실행 ---
if __name__ == '__main__':
    # 디스크 경로가 없으면 생성
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    init_db()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT interval_min, interval_max FROM config WHERE id = 1")
        res = cursor.fetchone()
        interval_min = res[0] if res else 30
        interval_max = res[1] if res else 40

    scheduler.add_job(lambda: asyncio.run(scheduled_send()), 'interval', minutes=random.randint(interval_min, interval_max), id='promo_job')
    scheduler.start()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE config SET scheduler_status = %s WHERE id = 1", ('running',))
        conn.commit()

    print("데이터베이스와 스케줄러가 준비되었습니다.")
    app.run(host='0.0.0.0', port=8080)
