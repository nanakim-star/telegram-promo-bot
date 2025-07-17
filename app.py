import os
import asyncio
import sqlite3
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
UPLOAD_FOLDER = 'static/uploads'
DATABASE = 'bot_config.db'

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

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
    with sqlite3.connect(DATABASE) as conn:
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
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
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT scheduler_status FROM config WHERE id = 1")
        status = cursor.fetchone()[0]
        if status != 'running':
            print("스케줄러가 '일시정지' 상태이므로 메시지를 발송하지 않습니다.")
            return

        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT message, photo FROM config WHERE id = 1")
        config = cursor.fetchone()
        cursor.execute("SELECT chat_id FROM promo_rooms WHERE is_active = 1")
        active_rooms = cursor.fetchall()

    log_detail = ""
    try:
        if not config: raise ValueError("설정값이 DB에 없습니다.")
        message, photo = config['message'], config['photo']
        if not message or not active_rooms: raise ValueError("홍보 메시지 또는 대상 방이 설정되지 않았습니다.")

        for room in active_rooms:
            await send_message_logic(room['chat_id'], message, photo)
        
        log_detail = f"✅ {len(active_rooms)}개 활성 방에 메시지 발송 성공"
    except Exception as e:
        log_detail = f"❌ 스케줄러 오류: {e}"
    finally:
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO activity_log (details) VALUES (?)", (log_detail,))
            conn.commit()
        print(log_detail)

# --- 스케줄러 설정 ---
scheduler = BackgroundScheduler(daemon=True, timezone='Asia/Seoul')

# --- 관리자 페이지 및 API 라우트 ---
@app.route('/', methods=['GET', 'POST'])
def admin_page():
    page_message = None
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if request.method == 'POST':
            message, preview_id = request.form.get('message'), request.form.get('preview_id')
            interval_min = int(request.form.get('interval_min', 30))
            interval_max = int(request.form.get('interval_max', 40))
            photo = request.files.get('photo')
            
            cursor.execute("SELECT interval_min, interval_max, photo FROM config WHERE id = 1")
            current_config = cursor.fetchone()

            photo_filename = current_config['photo']
            if photo and photo.filename:
                photo_filename = photo.filename
                photo.save(os.path.join(app.config['UPLOAD_FOLDER'], photo_filename))
            
            cursor.execute(
                "UPDATE config SET message=?, photo=?, interval_min=?, interval_max=?, preview_id=? WHERE id = 1",
                (message, photo_filename, interval_min, interval_max, preview_id)
            )
            conn.commit()
            
            if interval_min != current_config['interval_min'] or interval_max != current_config['interval_max']:
                # Jitter를 사용하여 다음 실행 시간만 랜덤화
                next_run_minutes = random.randint(interval_min, interval_max)
                scheduler.reschedule_job('promo_job', trigger='interval', minutes=next_run_minutes)
                print(f"스케줄러 간격이 {interval_min}-{interval_max}분으로 변경되었습니다. 다음 실행은 약 {next_run_minutes}분 후 입니다.")

            page_message = "✅ 설정이 성공적으로 저장되었습니다."
        
        # --- 데이터 조회 ---
        today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")
        cursor.execute("SELECT COUNT(*) FROM activity_log WHERE details LIKE '✅%' AND DATE(timestamp, '+9 hours') = ?", (today,))
        sent_today = cursor.fetchone()[0]
        cursor.execute("SELECT strftime('%Y-%m-%d %H:%M:%S', timestamp, '+9 hours'), details FROM activity_log ORDER BY id DESC LIMIT 5")
        recent_logs = [{'timestamp': row[0], 'details': row[1]} for row in cursor.fetchall()]
        
        cursor.execute("SELECT * FROM promo_rooms ORDER BY room_group, room_name")
        promo_rooms = cursor.fetchall()
        
        cursor.execute("SELECT * FROM config WHERE id = 1")
        config = cursor.fetchone()
        
        dashboard_data = {'sent_today': sent_today, 'recent_logs': recent_logs, 'room_count': len(promo_rooms)}
        
    return render_template('admin.html', config=config, message=page_message, dashboard=dashboard_data, promo_rooms=promo_rooms, scheduler_state=scheduler.state)

@app.route('/add_room', methods=['POST'])
def add_room():
    chat_id, room_name, room_group = request.form.get('chat_id'), request.form.get('room_name'), request.form.get('room_group')
    if not chat_id: return "Chat ID는 필수입니다.", 400
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO promo_rooms (chat_id, room_name, room_group) VALUES (?, ?, ?)", (chat_id, room_name, room_group))
            conn.commit()
        except sqlite3.IntegrityError:
            return "이미 존재하는 Chat ID 입니다.", 400
    return "성공적으로 추가되었습니다."

@app.route('/delete_room/<int:room_id>', methods=['POST'])
def delete_room(room_id):
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM promo_rooms WHERE id = ?", (room_id,))
        conn.commit()
    return "삭제되었습니다."

@app.route('/import_rooms', methods=['POST'])
def import_rooms():
    file = request.files.get('file')
    if not file: return "파일이 없습니다.", 400
    
    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    reader = csv.reader(stream)
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        for row in reader:
            if len(row) >= 3:
                cursor.execute("INSERT OR IGNORE INTO promo_rooms (chat_id, room_name, room_group) VALUES (?, ?, ?)", (row[0], row[1], row[2]))
        conn.commit()
    return "가져오기 완료!"

@app.route('/export_rooms')
def export_rooms():
    with sqlite3.connect(DATABASE) as conn:
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
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE config SET scheduler_status = ? WHERE id = 1", (status_to_set,))
            conn.commit()
        return f"스케줄러가 {status_to_set} 상태가 되었습니다."
    except Exception as e:
        return f"오류 발생: {e}", 500

@app.route('/check_rooms', methods=['POST'])
async def check_rooms():
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, chat_id FROM promo_rooms")
        rooms = cursor.fetchall()

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
        
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE promo_rooms SET last_status = ? WHERE id = ?", (status, room['id']))
            conn.commit()
            
    return "상태 확인 완료!"


@app.route('/preview', methods=['POST'])
async def preview_message():
    try:
        preview_id = request.form.get('preview_id')
        message_template = request.form.get('message')
        photo = request.files.get('photo')
        
        if not preview_id or not message_template:
            return jsonify({'message': '미리보기를 보낼 ID와 메시지를 입력해주세요.'}), 400

        if not BOT_TOKEN:
            return jsonify({'message': '오류: 봇 토큰이 설정되지 않았습니다.'}), 500

        bot = Bot(token=BOT_TOKEN)
        final_message = process_spintax(message_template)

        if photo and photo.filename:
            photo.seek(0)
            await bot.send_photo(chat_id=preview_id, photo=photo, caption=final_message)
        else:
            await bot.send_message(chat_id=preview_id, text=final_message)

        return jsonify({'message': f'✅ {preview_id}로 미리보기 메시지를 성공적으로 보냈습니다.'})
    except Exception as e:
        return jsonify({'message': f'❌ 미리보기 전송 실패: {e}'}), 500

# --- 애플리케이션 실행 ---
if __name__ == '__main__':
    init_db()
    
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT interval_min, interval_max FROM config WHERE id = 1")
        res = cursor.fetchone()
        interval_min = res[0] if res else 30
        interval_max = res[1] if res else 40

    scheduler.add_job(lambda: asyncio.run(scheduled_send()), 'interval', minutes=random.randint(interval_min, interval_max), id='promo_job')
    scheduler.start()
    
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE config SET scheduler_status = ? WHERE id = 1", ('running',))
        conn.commit()

    print("데이터베이스와 스케줄러가 준비되었습니다.")
    app.run(host='0.0.0.0', port=8080)