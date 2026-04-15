"""
Bad Manners 3000 — Declarer Play Trainer
Flask app with SQLite, endplay DDS, and username/password accounts
"""

from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, json, os, functools, math, random, secrets
import urllib.request
from itertools import combinations
from endplay.types import Deal, Player, Denom, Card, Rank
from endplay.dds import solve_board

app = Flask(__name__, static_folder='.')
CORS(app, supports_credentials=True)

app.secret_key = os.environ.get('SECRET_KEY', 'bm3k-dev-secret-change-in-prod')

DATA_DIR = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH  = os.path.join(DATA_DIR, 'bridge.db')

# ── Card / Player mappings ────────────────────────────────────────────────────

SUIT_MAP   = {'S': Denom.spades,  'H': Denom.hearts,
              'D': Denom.diamonds,'C': Denom.clubs, 'N': Denom.nt}
PLAYER_MAP = {'N': Player.north,  'E': Player.east,
              'S': Player.south,  'W': Player.west}
RANK_MAP   = {'A': Rank.RA, 'K': Rank.RK, 'Q': Rank.RQ, 'J': Rank.RJ,
              'T': Rank.RT, '9': Rank.R9, '8': Rank.R8, '7': Rank.R7,
              '6': Rank.R6, '5': Rank.R5, '4': Rank.R4, '3': Rank.R3, '2': Rank.R2}
SUIT_BACK  = {Denom.spades:'S', Denom.hearts:'H',
              Denom.diamonds:'D', Denom.clubs:'C', Denom.nt:'N'}
RANK_BACK  = {v: k for k, v in RANK_MAP.items()}
RANK_ORD   = 'AKQJT98765432'   # index 0 = A (highest), 12 = 2 (lowest)

# ── BEN (Bridge Engine by Neural Networks) config ────────────────────────────
# Models downloaded at Docker build time into /app/ben_models/
BEN_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ben_models')
_BEN_MODELS:    dict = {}   # populated by _init_ben() at startup
_BEN_RANK_IDX = {'A': 0, 'K': 1, 'Q': 2, 'J': 3, 'T': 4, '9': 5, '8': 6, '7': 7}
_BEN_SUIT_IDX = {'S': 0, 'H': 1, 'D': 2, 'C': 3}
_BEN_PLAYER_IDX = {'N': 0, 'E': 1, 'S': 2, 'W': 3}
# Opening leader is the player to the LEFT of declarer (clockwise)
_BEN_LEFT_OF  = {'N': 'W', 'E': 'N', 'S': 'E', 'W': 'S'}

def card_to_str(card):
    return SUIT_BACK[card.suit] + RANK_BACK[card.rank]

def str_to_card(s):
    return Card(rank=RANK_MAP[s[1]], suit=SUIT_MAP[s[0]])

# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS groups (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS user_groups (
            user_id  INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, group_id),
            FOREIGN KEY (user_id)  REFERENCES users(id)  ON DELETE CASCADE,
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS topic_groups (
            topic_name TEXT NOT NULL,
            group_id   INTEGER NOT NULL,
            PRIMARY KEY (topic_name, group_id),
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'student',
            created_at    TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS topics (
            name          TEXT PRIMARY KEY,
            restricted    INTEGER NOT NULL DEFAULT 0,
            homework      INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS quip_unlocks (
            user_id     INTEGER NOT NULL,
            quip_type   TEXT    NOT NULL,
            quip_idx    INTEGER NOT NULL,
            unlocked_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, quip_type, quip_idx),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS lessons (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
            topic        TEXT DEFAULT '',
            technique    TEXT DEFAULT '',
            explanation  TEXT DEFAULT '',
            pbn          TEXT NOT NULL,
            contract     TEXT NOT NULL,
            declarer     TEXT NOT NULL,
            lead         TEXT NOT NULL,
            par_tricks   INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS attempts (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            lesson_id      INTEGER NOT NULL,
            student_name   TEXT NOT NULL,
            user_id        INTEGER,
            tricks_made    INTEGER NOT NULL,
            contract_level INTEGER NOT NULL,
            result         TEXT NOT NULL,
            score          INTEGER NOT NULL,
            play_sequence  TEXT DEFAULT '[]',
            lin_data       TEXT DEFAULT '',
            played_at      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (lesson_id) REFERENCES lessons(id),
            FOREIGN KEY (user_id)   REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS game_progress (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            lesson_id   INTEGER NOT NULL,
            state_json  TEXT NOT NULL,
            saved_at    TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, lesson_id),
            FOREIGN KEY (user_id)   REFERENCES users(id)   ON DELETE CASCADE,
            FOREIGN KEY (lesson_id) REFERENCES lessons(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS telegram_link_tokens (
            token      TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    ''')
    conn.commit()
    # Migrations for existing databases
    for stmt in [
        'ALTER TABLE lessons  ADD COLUMN topic            TEXT DEFAULT ""',
        'ALTER TABLE attempts ADD COLUMN user_id          INTEGER',
        'ALTER TABLE attempts ADD COLUMN lin_data         TEXT DEFAULT ""',
        'ALTER TABLE topics   ADD COLUMN homework         INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE lessons  ADD COLUMN auction          TEXT DEFAULT ""',
        'ALTER TABLE users    ADD COLUMN telegram_chat_id TEXT DEFAULT NULL',
    ]:
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception:
            pass
    conn.close()

# ── Auth helpers ──────────────────────────────────────────────────────────────

def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    conn = get_db()
    row = conn.execute('SELECT id, username, role FROM users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return dict(row) if row else None

def teacher_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if not user or user['role'] != 'teacher':
            return jsonify({'error': 'Teacher access required'}), 403
        return f(*args, **kwargs)
    return decorated

# ── LIN generator ─────────────────────────────────────────────────────────────

def generate_lin(lesson, play_sequence, student_name):
    """Generate BBO LIN string from lesson data and ordered play sequence."""
    declarer = lesson['declarer']
    pbn      = lesson['pbn']
    contract = lesson['contract']

    # Parse PBN: "N:N_hand E_hand S_hand W_hand"
    parts  = pbn.split(':')[1].split(' ')
    n_hand, e_hand, s_hand, w_hand = parts[0], parts[1], parts[2], parts[3]

    def pbn_to_lin(hand):
        result = ''
        for suit_char, cards in zip(['S','H','D','C'], hand.split('.')):
            if cards and cards != '-':
                result += suit_char + cards
        return result

    # Player names in LIN order: South, West, North, East
    def name(pos):
        return student_name if pos == declarer else f'Robot-{pos}'

    pn = f"{name('S')},{name('W')},{name('N')},{name('E')}"

    # Auction for LIN: use stored auction if present, else synthesise
    lin_dealer_code = '1'  # default: South deals
    if lesson.get('auction'):
        try:
            aut = json.loads(lesson['auction'])
            dealer_codes = {'S': '1', 'W': '2', 'N': '3', 'E': '4'}
            lin_dealer_code = dealer_codes.get(aut.get('dealer', 'S'), '1')
            mb_str = ''.join(f'mb|{b}|' for b in aut.get('bids', [])) + 'pg||'
        except Exception:
            pos_of = {'S': 0, 'W': 1, 'N': 2, 'E': 3}
            level, suit = contract[0], contract[1]
            synth = ['p'] * pos_of[declarer] + [f"{level}{suit}", 'p', 'p', 'p']
            mb_str = ''.join(f'mb|{c}|' for c in synth) + 'pg||'
    else:
        pos_of = {'S': 0, 'W': 1, 'N': 2, 'E': 3}
        level, suit = contract[0], contract[1]
        synth = ['p'] * pos_of[declarer] + [f"{level}{suit}", 'p', 'p', 'p']
        mb_str = ''.join(f'mb|{c}|' for c in synth) + 'pg||'

    # md: hands in S/W/N order (E inferred), dealer digit from auction
    md = f"{lin_dealer_code}{pbn_to_lin(s_hand)},{pbn_to_lin(w_hand)},{pbn_to_lin(n_hand)}"

    # Play cards: group into tricks of 4
    play_str = ''
    for i, card in enumerate(play_sequence):
        play_str += f'pc|{card}|'
        if (i + 1) % 4 == 0:
            play_str += 'pg||'

    return f"pn|{pn}|md|{md}|sv|o|{mb_str}{play_str}"

# ── Score calculation ─────────────────────────────────────────────────────────

def calculate_score(contract_str, tricks_made):
    level  = int(contract_str[0])
    suit   = contract_str[1]
    target = level + 6
    diff   = tricks_made - target
    if diff >= 0:
        base = (40 + (level-1)*30) if suit=='N' else \
               (level*30 if suit in ('S','H') else level*20)
        game_bonus   = 300 if base >= 100 else 50
        slam_bonus   = 1000 if level == 7 else (500 if level == 6 else 0)
        over_per_trk = 30 if suit in ('S','H','N') else 20
        return base + game_bonus + slam_bonus + diff * over_per_trk
    else:
        return 50 * diff

# ── Telegram helpers ──────────────────────────────────────────────────────────

def send_telegram(chat_id, text):
    """Fire-and-forget Telegram message. No-op when TELEGRAM_BOT_TOKEN not set."""
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    if not token or not chat_id:
        return
    url     = f'https://api.telegram.org/bot{token}/sendMessage'
    payload = json.dumps({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}).encode()
    req     = urllib.request.Request(url, data=payload,
                                     headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        app.logger.warning(f'Telegram send failed chat_id={chat_id}: {e}')


def _notify_students(conn, topic_name, message):
    """Send `message` to all users with Telegram linked who can see this topic."""
    topic_row  = conn.execute('SELECT restricted FROM topics WHERE name=?',
                              (topic_name,)).fetchone()
    restricted = topic_row['restricted'] if topic_row else 0

    if restricted:
        rows = conn.execute('''
            SELECT DISTINCT u.telegram_chat_id FROM users u
            JOIN user_groups ug ON ug.user_id = u.id
            JOIN topic_groups tg ON tg.group_id = ug.group_id
            WHERE tg.topic_name = ? AND u.telegram_chat_id IS NOT NULL
        ''', (topic_name,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT telegram_chat_id FROM users WHERE telegram_chat_id IS NOT NULL"
        ).fetchall()

    for r in rows:
        send_telegram(r['telegram_chat_id'], message)


# ── Static files ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('static', path)

# ── Health check ──────────────────────────────────────────────────────────────

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route('/auth/login', methods=['POST'])
def auth_login():
    d        = request.json
    username = d.get('username', '').strip()
    password = d.get('password', '')
    conn = get_db()
    row  = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    conn.close()
    if not row or not check_password_hash(row['password_hash'], password):
        return jsonify({'error': 'Invalid username or password'}), 401
    session['user_id'] = row['id']
    return jsonify({'id': row['id'], 'username': row['username'], 'role': row['role']})

@app.route('/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/auth/me', methods=['GET'])
def auth_me():
    user = current_user()
    if user:
        conn = get_db()
        row  = conn.execute('SELECT telegram_chat_id FROM users WHERE id=?',
                            (user['id'],)).fetchone()
        conn.close()
        user['telegram_linked'] = bool(row and row['telegram_chat_id'])
    return jsonify(user)

# ── Users (teacher only) ──────────────────────────────────────────────────────

@app.route('/api/users', methods=['GET'])
@teacher_required
def get_users():
    conn = get_db()
    rows = conn.execute(
        'SELECT id, username, role, created_at FROM users ORDER BY username COLLATE NOCASE'
    ).fetchall()
    users = [dict(r) for r in rows]
    for u in users:
        grps = conn.execute(
            'SELECT g.id, g.name FROM groups g '
            'JOIN user_groups ug ON ug.group_id=g.id WHERE ug.user_id=?', (u['id'],)
        ).fetchall()
        u['groups'] = [dict(g) for g in grps]
    conn.close()
    return jsonify(users)

@app.route('/api/users', methods=['POST'])
@teacher_required
def create_user():
    d        = request.json
    username = d.get('username', '').strip()
    password = d.get('password', '')
    role     = d.get('role', 'student')
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    if role not in ('teacher', 'student'):
        return jsonify({'error': 'Invalid role'}), 400
    conn = get_db()
    try:
        cur = conn.execute(
            'INSERT INTO users (username, password_hash, role) VALUES (?,?,?)',
            (username, generate_password_hash(password), role))
        uid = cur.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'Username already taken'}), 409
    conn.close()
    return jsonify({'id': uid, 'username': username, 'role': role}), 201

@app.route('/api/users/<int:uid>', methods=['DELETE'])
@teacher_required
def delete_user(uid):
    if session.get('user_id') == uid:
        return jsonify({'error': 'Cannot delete your own account'}), 400
    conn = get_db()
    conn.execute('DELETE FROM users WHERE id=?', (uid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/users/<int:uid>/password', methods=['PUT'])
@teacher_required
def change_password(uid):
    password = request.json.get('password', '')
    if not password:
        return jsonify({'error': 'Password required'}), 400
    conn = get_db()
    conn.execute('UPDATE users SET password_hash=? WHERE id=?',
                 (generate_password_hash(password), uid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Groups ────────────────────────────────────────────────────────────────────

@app.route('/api/groups', methods=['GET'])
def get_groups():
    conn = get_db()
    rows = conn.execute('SELECT id, name FROM groups ORDER BY name COLLATE NOCASE').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/groups', methods=['POST'])
@teacher_required
def create_group():
    name = request.json.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Group name required'}), 400
    conn = get_db()
    try:
        cur = conn.execute('INSERT INTO groups (name) VALUES (?)', (name,))
        gid = cur.lastrowid
        conn.commit()
    except Exception:
        conn.close()
        return jsonify({'error': 'Group name already exists'}), 409
    conn.close()
    return jsonify({'id': gid, 'name': name}), 201

@app.route('/api/groups/<int:gid>', methods=['DELETE'])
@teacher_required
def delete_group(gid):
    conn = get_db()
    conn.execute('DELETE FROM groups WHERE id=?', (gid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/users/<int:uid>/groups', methods=['PUT'])
@teacher_required
def set_user_groups(uid):
    group_ids = request.json.get('group_ids', [])
    conn = get_db()
    conn.execute('DELETE FROM user_groups WHERE user_id=?', (uid,))
    for gid in group_ids:
        conn.execute('INSERT OR IGNORE INTO user_groups (user_id, group_id) VALUES (?,?)', (uid, gid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Topics ────────────────────────────────────────────────────────────────────

@app.route('/api/topics', methods=['GET'])
def get_topics():
    conn = get_db()
    rows = conn.execute('''
        SELECT DISTINCT l.topic,
               COALESCE(t.restricted, 0) AS restricted,
               COALESCE(t.homework,   0) AS homework
        FROM lessons l
        LEFT JOIN topics t ON t.name = l.topic
        WHERE l.topic != ''
        ORDER BY l.topic COLLATE NOCASE
    ''').fetchall()
    topics = [dict(r) for r in rows]
    for t in topics:
        grps = conn.execute(
            'SELECT g.id, g.name FROM groups g '
            'JOIN topic_groups tg ON tg.group_id=g.id WHERE tg.topic_name=?', (t['topic'],)
        ).fetchall()
        t['groups'] = [dict(g) for g in grps]
    conn.close()
    return jsonify(topics)

@app.route('/api/topics/<path:name>/restricted', methods=['PUT'])
@teacher_required
def set_topic_restricted(name):
    restricted = 1 if request.json.get('restricted') else 0
    conn = get_db()
    conn.execute(
        'INSERT OR REPLACE INTO topics (name, restricted, homework) '
        'VALUES (?, ?, COALESCE((SELECT homework FROM topics WHERE name=?), 0))',
        (name, restricted, name))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/topics/<path:name>/homework', methods=['PUT'])
@teacher_required
def set_topic_homework(name):
    homework = 1 if request.json.get('homework') else 0
    conn = get_db()
    old = conn.execute('SELECT homework FROM topics WHERE name=?', (name,)).fetchone()
    old_hw = old['homework'] if old else 0
    conn.execute(
        'INSERT OR REPLACE INTO topics (name, restricted, homework) '
        'VALUES (?, COALESCE((SELECT restricted FROM topics WHERE name=?), 0), ?)',
        (name, name, homework))
    conn.commit()
    # Notify students the first time a topic is marked as homework
    if homework == 1 and old_hw == 0:
        n = conn.execute('SELECT COUNT(*) AS cnt FROM lessons WHERE topic=?',
                         (name,)).fetchone()['cnt']
        if n:
            plural = 's' if n != 1 else ''
            _notify_students(conn, name,
                f'📚 <b>New Homework Assigned</b>\n\n'
                f'Topic: <b>{name}</b>\n'
                f'{n} lesson{plural} waiting for you.\n\n'
                f'Log in to BM3K to start playing!')
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/topics/<path:name>/groups', methods=['PUT'])
@teacher_required
def set_topic_groups(name):
    group_ids = request.json.get('group_ids', [])
    conn = get_db()
    conn.execute('DELETE FROM topic_groups WHERE topic_name=?', (name,))
    for gid in group_ids:
        conn.execute('INSERT OR IGNORE INTO topic_groups (topic_name, group_id) VALUES (?,?)',
                     (name, gid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Lessons ───────────────────────────────────────────────────────────────────

@app.route('/api/lessons', methods=['GET'])
def get_lessons():
    user = current_user()
    conn = get_db()
    if user and user['role'] == 'teacher':
        # Teachers see everything
        rows = conn.execute(
            'SELECT * FROM lessons ORDER BY title COLLATE NOCASE ASC'
        ).fetchall()
    elif user:
        # Students: general topics + restricted topics with no groups + restricted topics where they're in an assigned group
        rows = conn.execute('''
            SELECT DISTINCT l.* FROM lessons l
            LEFT JOIN topics t ON t.name = l.topic
            WHERE
                COALESCE(t.restricted, 0) = 0
                OR (
                    COALESCE(t.restricted, 0) = 1 AND (
                        NOT EXISTS (SELECT 1 FROM topic_groups WHERE topic_name = l.topic)
                        OR EXISTS (
                            SELECT 1 FROM topic_groups tg
                            JOIN user_groups ug ON ug.group_id = tg.group_id
                            WHERE tg.topic_name = l.topic AND ug.user_id = ?
                        )
                    )
                )
            ORDER BY l.title COLLATE NOCASE ASC
        ''', (user['id'],)).fetchall()
    else:
        # Guests: general topics only
        rows = conn.execute('''
            SELECT l.* FROM lessons l
            LEFT JOIN topics t ON t.name = l.topic
            WHERE COALESCE(t.restricted, 0) = 0
            ORDER BY l.title COLLATE NOCASE ASC
        ''').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/lessons/<int:lid>', methods=['GET'])
def get_lesson(lid):
    conn = get_db()
    row = conn.execute('SELECT * FROM lessons WHERE id=?', (lid,)).fetchone()
    conn.close()
    return jsonify(dict(row)) if row else (jsonify({'error': 'Not found'}), 404)

@app.route('/api/lessons', methods=['POST'])
@teacher_required
def create_lesson():
    d = request.json
    pbn_err = validate_pbn(d.get('pbn', ''))
    if pbn_err:
        return jsonify({'error': f'Invalid deal: {pbn_err}'}), 400
    par_tricks = int(d['contract'][0]) + 6
    try:
        deal = Deal(d['pbn'])
        deal.trump = SUIT_MAP[d['contract'][1]]
        left_of = {Player.north: Player.west,  Player.east: Player.north,
                   Player.south: Player.east,  Player.west: Player.south}
        deal.first = left_of[PLAYER_MAP[d['declarer']]]
        results    = solve_board(deal)
        par_tricks = min(t for _, t in results)
    except Exception as e:
        print('DDS par error:', e)
    conn = get_db()
    cur  = conn.execute(
        'INSERT INTO lessons (title,topic,technique,explanation,pbn,contract,declarer,lead,par_tricks,auction) '
        'VALUES (?,?,?,?,?,?,?,?,?,?)',
        (d['title'], d.get('topic',''), d.get('technique',''), d.get('explanation',''),
         d['pbn'], d['contract'], d['declarer'], d['lead'], par_tricks, d.get('auction','')))
    lid = cur.lastrowid
    conn.commit()
    # Notify students if this lesson belongs to an already-active homework topic
    topic = d.get('topic', '')
    if topic:
        hw_row = conn.execute('SELECT homework FROM topics WHERE name=?', (topic,)).fetchone()
        if hw_row and hw_row['homework']:
            _notify_students(conn, topic,
                f'📚 <b>New Homework Lesson</b>\n\n'
                f'<b>{d["title"]}</b>\n'
                f'Topic: {topic}\n\n'
                f'Log in to BM3K to play!')
    conn.close()
    return jsonify({'id': lid, 'par_tricks': par_tricks}), 201

@app.route('/api/lessons/<int:lid>', methods=['PUT'])
@teacher_required
def update_lesson(lid):
    d = request.json
    pbn_err = validate_pbn(d.get('pbn', ''))
    if pbn_err:
        return jsonify({'error': f'Invalid deal: {pbn_err}'}), 400
    par_tricks = int(d['contract'][0]) + 6
    try:
        deal = Deal(d['pbn'])
        deal.trump = SUIT_MAP[d['contract'][1]]
        left_of = {Player.north: Player.west,  Player.east: Player.north,
                   Player.south: Player.east,  Player.west: Player.south}
        deal.first = left_of[PLAYER_MAP[d['declarer']]]
        results    = solve_board(deal)
        par_tricks = min(t for _, t in results)
    except Exception as e:
        print('DDS par error:', e)
    conn = get_db()
    conn.execute(
        'UPDATE lessons SET title=?,topic=?,technique=?,explanation=?,pbn=?,contract=?,declarer=?,lead=?,par_tricks=?,auction=? WHERE id=?',
        (d['title'], d.get('topic',''), d.get('technique',''), d.get('explanation',''),
         d['pbn'], d['contract'], d['declarer'], d['lead'], par_tricks, d.get('auction',''), lid))
    conn.commit(); conn.close()
    return jsonify({'id': lid, 'par_tricks': par_tricks})

@app.route('/api/lessons/<int:lid>', methods=['DELETE'])
@teacher_required
def delete_lesson(lid):
    conn = get_db()
    conn.execute('DELETE FROM lessons WHERE id=?', (lid,))
    conn.execute('DELETE FROM attempts WHERE lesson_id=?', (lid,))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

# ── Game progress ─────────────────────────────────────────────────────────────

@app.route('/api/progress/<int:lid>', methods=['PUT'])
def save_progress(lid):
    user = current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401
    conn = get_db()
    conn.execute(
        'INSERT OR REPLACE INTO game_progress (user_id, lesson_id, state_json, saved_at) '
        'VALUES (?, ?, ?, datetime("now"))',
        (user['id'], lid, json.dumps(request.json)))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/progress/<int:lid>', methods=['GET'])
def get_progress(lid):
    user = current_user()
    if not user:
        return jsonify(None)
    conn = get_db()
    row = conn.execute(
        'SELECT state_json FROM game_progress WHERE user_id=? AND lesson_id=?',
        (user['id'], lid)).fetchone()
    conn.close()
    return jsonify(json.loads(row['state_json']) if row else None)

@app.route('/api/progress/<int:lid>', methods=['DELETE'])
def clear_progress(lid):
    user = current_user()
    if not user:
        return jsonify({'ok': True})
    conn = get_db()
    conn.execute('DELETE FROM game_progress WHERE user_id=? AND lesson_id=?',
                 (user['id'], lid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── DDS ───────────────────────────────────────────────────────────────────────

def validate_pbn(pbn):
    """Return an error string if the PBN deal is invalid, else None."""
    try:
        parts = pbn.split(':')
        if len(parts) < 2:
            return 'PBN must be in format N:hand hand hand hand'
        hands_raw = parts[1].split()
        if len(hands_raw) != 4:
            return 'PBN must contain exactly 4 hands'
        suit_chars = ['S', 'H', 'D', 'C']
        cards = []
        for hand_str in hands_raw:
            suits = hand_str.split('.')
            if len(suits) != 4:
                return 'Each hand must have exactly 4 suits separated by dots'
            for s_idx, suit_ranks in enumerate(suits):
                if suit_ranks in ('', '-'):
                    continue  # void in this suit
                for rank in suit_ranks:
                    cards.append(suit_chars[s_idx] + rank)
        if len(cards) != 52:
            return f'Expected 52 cards total, found {len(cards)}'
        dupes = [c for c in set(cards) if cards.count(c) > 1]
        if dupes:
            return f'Duplicate card(s): {", ".join(sorted(dupes))}'
        return None
    except Exception as e:
        return str(e)

def remaining_to_pbn(remaining):
    rank_order = 'AKQJT98765432'
    suit_order = ['S', 'H', 'D', 'C']
    def hand_str(cards):
        suits = {s: [] for s in suit_order}
        for c in cards:
            suits[c[0]].append(c[1])
        return '.'.join(
            ''.join(sorted(suits[s], key=lambda r: rank_order.index(r)))
            for s in suit_order
        )
    return 'N:{} {} {} {}'.format(
        hand_str(remaining.get('N', [])),
        hand_str(remaining.get('E', [])),
        hand_str(remaining.get('S', [])),
        hand_str(remaining.get('W', []))
    )

def _ben_card_idx(card_str: str) -> int:
    """BEN 0-31 slot index (suit*8 + rank). Returns -1 for cards below 7."""
    suit, rank = card_str[0], card_str[1]
    if rank not in _BEN_RANK_IDX:
        return -1
    return _BEN_SUIT_IDX[suit] * 8 + _BEN_RANK_IDX[rank]


def _ben_hand_vec(cards: list) -> list:
    """32-float binary vector: 1.0 where card is present."""
    v = [0.0] * 32
    for c in cards:
        idx = _ben_card_idx(c)
        if idx >= 0:
            v[idx] = 1.0
    return v


def _ben_card_vec(card_str: str) -> list:
    """32-float one-hot for a single card (all zeros if card below 7)."""
    v = [0.0] * 32
    idx = _ben_card_idx(card_str)
    if idx >= 0:
        v[idx] = 1.0
    return v


def _ben_build_input(own_hand, dummy_hand, contract,
                     current_trick, last_trick, last_trick_leader):
    """
    Build the (1, 1, 298) float32 numpy array for BEN cardplay inference.

    Layout (298 dims):
      0-31   own hand           32-bit binary
      32-63  dummy hand         32-bit binary
      64-191 last trick         4 × 32-bit one-hot  (pad with zeros if first trick)
      192-287 current trick     3 × 32-bit one-hot  (cards played before us)
      288-291 last trick leader 4-bit one-hot (N/E/S/W)
      292    contract level     float (1-7)
      293-297 strain            5-bit one-hot (NT/S/H/D/C)
    """
    import numpy as np

    vec = _ben_hand_vec(own_hand)                   # 0-31
    vec.extend(_ben_hand_vec(dummy_hand))            # 32-63

    # 64-191: last trick — 4 cards in play order (pad if fewer)
    last_cards = [e['card'] for e in (last_trick or [])][:4]
    last_cards += [None] * (4 - len(last_cards))
    for c in last_cards:
        vec.extend(_ben_card_vec(c) if c else [0.0] * 32)

    # 192-287: current trick — up to 3 cards played so far (not including ours)
    cur_cards = [e['card'] for e in (current_trick or [])][:3]
    cur_cards += [None] * (3 - len(cur_cards))
    for c in cur_cards:
        vec.extend(_ben_card_vec(c) if c else [0.0] * 32)

    # 288-291: last trick leader one-hot
    leader_vec = [0.0] * 4
    if last_trick_leader and last_trick_leader in _BEN_PLAYER_IDX:
        leader_vec[_BEN_PLAYER_IDX[last_trick_leader]] = 1.0
    vec.extend(leader_vec)

    # 292: contract level
    try:
        vec.append(float(contract[0]))
    except Exception:
        vec.append(4.0)

    # 293-297: strain one-hot (NT=0, S=1, H=2, D=3, C=4)
    strain_map = {'N': 0, 'S': 1, 'H': 2, 'D': 3, 'C': 4}
    strain_vec = [0.0] * 5
    if len(contract) >= 2:
        strain_vec[strain_map.get(contract[1].upper(), 0)] = 1.0
    vec.extend(strain_vec)

    return np.array(vec, dtype=np.float32).reshape(1, 1, 298)


def _ben_pick(candidates, own_hand, dummy_hand, contract,
              current_trick, last_trick, last_trick_leader,
              next_player, declarer):
    """
    Run BEN ONNX inference and return the best candidate card string.
    Returns None if BEN models are unavailable or inference fails.
    """
    if not _BEN_MODELS:
        return None

    try:
        strain     = contract[1].upper() if len(contract) >= 2 else 'N'
        model_type = 'nt' if strain == 'N' else 'suit'
        side       = 'lefty' if next_player == _BEN_LEFT_OF[declarer] else 'righty'
        sess       = _BEN_MODELS.get(f'{side}_{model_type}')
        if sess is None:
            return None

        inp         = _ben_build_input(own_hand, dummy_hand, contract,
                                       current_trick, last_trick, last_trick_leader)
        input_name  = sess.get_inputs()[0].name
        output_name = sess.get_outputs()[0].name
        logits      = sess.run([output_name], {input_name: inp})[0].flatten()

        best_card, best_val = None, -1e9
        for card_str in candidates:
            idx = _ben_card_idx(card_str)
            if idx < 0:
                continue   # card below 7 — not in BEN vocabulary
            if logits[idx] > best_val:
                best_val, best_card = logits[idx], card_str
        return best_card   # None if every candidate is a low card (< 7)

    except Exception as e:
        app.logger.warning(f'BEN inference failed: {e}')
        return None


def _init_ben():
    """Load BEN ONNX models. Called once at startup; silently skips if unavailable."""
    try:
        import onnxruntime as ort
    except ImportError:
        app.logger.info('BEN: onnxruntime not installed — heuristics only')
        return

    loaded = 0
    for name in ('lefty_nt', 'righty_nt', 'lefty_suit', 'righty_suit'):
        path = os.path.join(BEN_MODELS_DIR, f'{name}.onnx')
        if os.path.isfile(path):
            try:
                _BEN_MODELS[name] = ort.InferenceSession(
                    path, providers=['CPUExecutionProvider'])
                loaded += 1
            except Exception as e:
                app.logger.warning(f'BEN: failed to load {name}.onnx: {e}')
        else:
            app.logger.info(f'BEN: {name}.onnx not found — skipping')

    lvl = 'info' if loaded else 'info'
    getattr(app.logger, lvl)(
        f'BEN: {loaded}/4 models loaded from {BEN_MODELS_DIR}'
        + ('' if loaded else ' — heuristics only'))


def _defender_tiebreak(candidates, hand_cards, current_trick,
                       partner_hand=None, dummy_hand=None,
                       contract=None, last_trick=None, last_trick_leader=None,
                       next_player=None, declarer=None):
    """
    Bridge-heuristic tiebreaker for defenders when DDS rates multiple cards
    equally good (same trick count).

    Position in trick drives the choice:

    1st to play (leading) — scored in three layers:
      Layer 1 (card quality):
        6 = Ace from AK
        5 = Top of a touching sequence (QJ→Q, JT→J)
        4 = Spot card (9 or lower) — lead the smallest
        1 = Isolated unsupported honour — last resort
      Layer 2 (cross-suit, only relevant when candidates span suits):
        + suit length (prefer 4-card suits)
        − dummy strength in that suit (avoid leading into A/K/Q in dummy)
        − tenace penalty (AQx or KJx — gift a free finesse)
      Layer 3 (card within suit):
        spots → prefer lowest; others → prefer highest

    2nd to play: cheapest card (second hand low).

    3rd to play: highest card (third hand high).
      Exception: lower of touching sequence at the top (e.g. Q from KQ).

    4th to play: cheapest card.

    Entry management: if partner has promoted winners in a suit and this
      is our only remaining card there, avoid leading it away.
    """
    if len(candidates) == 1:
        return candidates[0]

    # ── Try BEN ONNX first (falls through to heuristics on failure) ───────────
    if contract and next_player and declarer:
        ben = _ben_pick(candidates, hand_cards, dummy_hand or [],
                        contract, current_trick, last_trick or [],
                        last_trick_leader, next_player, declarer)
        if ben:
            return ben

    position = len(current_trick)   # 0=lead, 1=2nd, 2=3rd, 3=4th

    # ── 2nd / 4th hand: cheapest tied card ───────────────────────────────────
    if position in (1, 3):
        return max(candidates, key=lambda c: RANK_ORD.index(c[1]))

    # ── 3rd hand: high — but lower of touching sequence at the top ───────────
    if position == 2:
        sorted_cands = sorted(candidates, key=lambda c: RANK_ORD.index(c[1]))
        best_ridx = RANK_ORD.index(sorted_cands[0][1])
        if len(sorted_cands) >= 2:
            second_ridx = RANK_ORD.index(sorted_cands[1][1])
            if second_ridx == best_ridx + 1:    # touching pair at the top
                return sorted_cands[1]          # lower of touching → Q from KQ
        return sorted_cands[0]                  # plain third-hand-high

    # ── On lead: entry-management guard ──────────────────────────────────────
    if partner_hand:
        partner_by_suit: dict = {}
        for c in partner_hand:
            partner_by_suit.setdefault(c[0], []).append(RANK_ORD.index(c[1]))

        def is_sole_entry(card):
            suit = card[0]
            if sum(1 for c in hand_cards if c[0] == suit) != 1:
                return False
            p_idxs = partner_by_suit.get(suit, [])
            return bool(p_idxs) and min(p_idxs) <= 4   # partner has an honour

        protected = [c for c in candidates if not is_sole_entry(c)]
        if protected:
            candidates = protected

    # ── On lead: build suit index maps ───────────────────────────────────────
    hand_by_suit: dict = {}
    for c in hand_cards:
        hand_by_suit.setdefault(c[0], []).append(RANK_ORD.index(c[1]))
    for s in hand_by_suit:
        hand_by_suit[s].sort()          # ascending = best card first (A=0)

    dummy_by_suit: dict = {}
    if dummy_hand:
        for c in dummy_hand:
            dummy_by_suit.setdefault(c[0], []).append(RANK_ORD.index(c[1]))
        for s in dummy_by_suit:
            dummy_by_suit[s].sort()

    def lead_score(card):
        suit = card[0]
        ridx = RANK_ORD.index(card[1])          # 0=A … 12=2
        idxs = hand_by_suit.get(suit, [])
        if not idxs:
            return (0, 0, 0)

        # ── Layer 1: card quality ─────────────────────────────────────────
        if ridx == 0 and 1 in idxs:                        # A from AK
            card_q = 6
        elif ridx == idxs[0] and (ridx + 1) in idxs:      # top of sequence
            card_q = 5
        elif ridx >= 5:                                     # spot card
            card_q = 4
        else:                                               # isolated honour
            card_q = 1

        # ── Layer 2: cross-suit factors ───────────────────────────────────
        # Length: longer suits are better to lead from
        cross = len(idxs)

        # Dummy weakness: avoid leading into A/K/Q in dummy
        dum_idxs = dummy_by_suit.get(suit, [])
        if dum_idxs:
            best_in_dum = dum_idxs[0]           # lowest ridx = highest rank
            if best_in_dum <= 2:   cross -= 3   # A, K or Q in dummy — avoid
            elif best_in_dum <= 4: cross -= 1   # J or T in dummy — slightly worse

        # Tenace: AQx (no K) or KJx (no A,Q) — gifts a free finesse
        if 0 in idxs and 2 in idxs and 1 not in idxs:          cross -= 2  # AQ
        if 1 in idxs and 3 in idxs and 0 not in idxs \
                      and 2 not in idxs:                        cross -= 2  # KJ

        # ── Layer 3: card within suit ─────────────────────────────────────
        # Spots → prefer lowest (lead small); others → prefer highest
        within = ridx if card_q == 4 else -ridx

        return (card_q, cross, within)

    return max(candidates, key=lead_score)


@app.route('/api/dds/next_move', methods=['POST'])
def dds_next_move():
    d                  = request.json
    remaining          = d['remaining_hands']
    current_trick      = d.get('current_trick', [])
    next_player        = d['next_player']
    declarer           = d.get('declarer', 'S')
    contract           = d.get('contract', '')        # e.g. "4S" — for BEN
    last_trick         = d.get('last_trick', [])      # [{player,card}×4]
    last_trick_leader  = d.get('last_trick_leader')   # player who led last trick

    # Restore trick cards to the owners' hands so deal.play() can remove them.
    # Guard: only add a trick card if it isn't already in that player's remaining
    # (front-end strips them, but be defensive in case of timing edge-cases).
    hands_for_pbn = {p: list(cards) for p, cards in remaining.items()}
    for entry in current_trick:
        hand = hands_for_pbn.setdefault(entry['player'], [])
        if entry['card'] not in hand:
            hand.append(entry['card'])

    # Deduplicate across hands — safety net for lessons saved with invalid PBN data
    # (e.g. same card in two players' hands). First occurrence wins (N→E→S→W priority).
    seen_cards: set = set()
    for p in ['N', 'E', 'S', 'W']:
        if p in hands_for_pbn:
            before = len(hands_for_pbn[p])
            hands_for_pbn[p] = [c for c in hands_for_pbn[p] if not (c in seen_cards or seen_cards.add(c))]
            if len(hands_for_pbn[p]) < before:
                app.logger.warning(f'Removed {before - len(hands_for_pbn[p])} duplicate card(s) from {p}\'s hand')

    pbn  = remaining_to_pbn(hands_for_pbn)
    deal = Deal(pbn)
    deal.trump = SUIT_MAP[d['trump']]

    if current_trick:
        deal.first = PLAYER_MAP[current_trick[0]['player']]
        for entry in current_trick:
            try:
                deal.play(str_to_card(entry['card']))
            except Exception as ex:
                app.logger.warning(f'play error: {ex} | card={entry["card"]} player={entry["player"]}')
    else:
        deal.first = PLAYER_MAP[next_player]

    # Convert to strings immediately — avoids Card object attribute issues.
    # Card string format: "SR" where S=suit char ('S','H','D','C'), R=rank char.
    try:
        results_str = [(card_to_str(c), t) for c, t in solve_board(deal)]
    except Exception as dds_err:
        app.logger.error(f'DDS solve_board error: {dds_err}')
        app.logger.error(f'  remaining={remaining}')
        app.logger.error(f'  current_trick={current_trick}')
        app.logger.error(f'  hands_for_pbn={hands_for_pbn}')
        app.logger.error(f'  pbn={pbn}')
        return jsonify({'error': 'DDS failed', 'detail': str(dds_err)}), 500

    # solve_board returns tricks for the side of the player who is next to act
    # (EW tricks when an EW player leads/plays, NS tricks when NS).
    # Since this endpoint is only ever called for defenders (EW), we always
    # want to MAXIMISE — pick the card that gives EW the most tricks.
    target_tricks = max(t for _, t in results_str)

    # Among DDS-equivalent cards apply bridge defensive heuristics:
    # - Lead:       top of sequence / A from AK / lead low; guard partner's entries
    # - 2nd / 4th:  cheapest (second hand low; economy play)
    # - 3rd:        highest, or lower of touching honours at the top
    candidates    = [s for s, t in results_str if t == target_tricks]
    defender_hand = remaining.get(next_player, [])
    _partner      = {'N':'S','S':'N','E':'W','W':'E'}
    partner_hand  = remaining.get(_partner[next_player], [])
    dummy         = _partner[declarer]
    dummy_hand    = remaining.get(dummy, [])
    best_card_str = _defender_tiebreak(candidates, defender_hand, current_trick,
                                       partner_hand=partner_hand,
                                       dummy_hand=dummy_hand,
                                       contract=contract,
                                       last_trick=last_trick,
                                       last_trick_leader=last_trick_leader,
                                       next_player=next_player,
                                       declarer=declarer)

    trick_str = '|'.join(f'{e["player"]}:{e["card"]}' for e in current_trick)
    app.logger.warning(f'DDS next={next_player} trick=[{trick_str}] '
                       f'target={target_tricks} all={sorted(results_str,key=lambda x:x[1],reverse=True)} '
                       f'cands={candidates} best={best_card_str}')

    return jsonify({
        'best_card':   best_card_str,
        'tricks':      target_tricks,
        'all_options': sorted(results_str, key=lambda x: x[1], reverse=True)
    })

# ── Attempts ──────────────────────────────────────────────────────────────────

@app.route('/api/attempts', methods=['POST'])
def save_attempt():
    user = current_user()
    if not user:
        return jsonify({'error': 'Login required to save attempts'}), 401

    d           = request.json
    contract    = d['contract']
    tricks_made = d['tricks_made']
    level       = int(contract[0])
    diff        = tricks_made - (level + 6)
    result      = ('Made +'+str(diff) if diff > 0 else
                   'Made exactly'     if diff == 0 else
                   'Down '+str(abs(diff)))
    score        = calculate_score(contract, tricks_made)
    play_sequence = d.get('play_sequence', [])

    # Generate LIN
    lin_data = ''
    try:
        conn_r = get_db()
        lesson_row = conn_r.execute('SELECT * FROM lessons WHERE id=?', (d['lesson_id'],)).fetchone()
        conn_r.close()
        if lesson_row:
            lin_data = generate_lin(dict(lesson_row), play_sequence, user['username'])
    except Exception as e:
        print('LIN generation error:', e)

    conn = get_db()
    cur  = conn.execute(
        'INSERT INTO attempts '
        '(lesson_id, student_name, user_id, tricks_made, contract_level, result, score, play_sequence, lin_data) '
        'VALUES (?,?,?,?,?,?,?,?,?)',
        (d['lesson_id'], user['username'], user['id'], tricks_made, level,
         result, score, json.dumps(play_sequence), lin_data))
    aid = cur.lastrowid
    conn.commit(); conn.close()
    return jsonify({'id': aid, 'result': result, 'score': score}), 201

@app.route('/api/attempts/lesson/<int:lid>', methods=['GET'])
def get_lesson_attempts(lid):
    user = current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401
    conn = get_db()
    if user['role'] == 'teacher':
        rows = conn.execute(
            'SELECT a.*, l.title as lesson_title '
            'FROM attempts a JOIN lessons l ON a.lesson_id=l.id '
            'WHERE a.lesson_id=? ORDER BY a.played_at DESC', (lid,)).fetchall()
    else:
        rows = conn.execute(
            'SELECT a.*, l.title as lesson_title '
            'FROM attempts a JOIN lessons l ON a.lesson_id=l.id '
            'WHERE a.lesson_id=? AND a.user_id=? ORDER BY a.played_at DESC',
            (lid, user['id'])).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/attempts/all', methods=['GET'])
def get_all_attempts():
    conn = get_db()
    rows = conn.execute(
        'SELECT a.*, l.title as lesson_title '
        'FROM attempts a JOIN lessons l ON a.lesson_id=l.id '
        'ORDER BY a.played_at DESC LIMIT 500').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/attempts/<int:aid>', methods=['DELETE'])
@teacher_required
def delete_attempt(aid):
    conn = get_db()
    conn.execute('DELETE FROM attempts WHERE id=?', (aid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/attempts/my-status', methods=['GET'])
def my_attempt_status():
    user = current_user()
    if not user:
        return jsonify({})
    conn = get_db()
    rows = conn.execute(
        'SELECT lesson_id, result, played_at FROM attempts '
        'WHERE user_id=? ORDER BY lesson_id, played_at ASC',
        (user['id'],)
    ).fetchall()
    conn.close()
    status = {}
    for row in rows:
        lid  = str(row['lesson_id'])
        made = 'Made' in row['result']
        if lid not in status:
            status[lid] = {'attempts': 0, 'made': False, 'first_try': False}
        status[lid]['attempts'] += 1
        if made and not status[lid]['made']:
            status[lid]['made']      = True
            status[lid]['first_try'] = (status[lid]['attempts'] == 1)
    return jsonify(status)

@app.route('/api/teacher/students', methods=['GET'])
@teacher_required
def teacher_students():
    conn = get_db()
    groups = conn.execute('SELECT id, name FROM groups ORDER BY name').fetchall()

    def student_stats(student_rows, hw_lessons):
        hw_ids = [l['id'] for l in hw_lessons]
        total  = len(hw_ids)
        result = []
        for s in student_rows:
            attempts = conn.execute(
                'SELECT lesson_id, result FROM attempts WHERE user_id=? ORDER BY id ASC',
                (s['id'],)
            ).fetchall()
            per_lesson = {}
            for a in attempts:
                lid = a['lesson_id']
                if lid not in per_lesson:
                    per_lesson[lid] = {'cnt': 0, 'made': False, 'first_try': False}
                per_lesson[lid]['cnt'] += 1
                if 'Made' in a['result'] and not per_lesson[lid]['made']:
                    per_lesson[lid]['made'] = True
                    per_lesson[lid]['first_try'] = (per_lesson[lid]['cnt'] == 1)
            cnt_none = cnt_down = cnt_made = cnt_first = 0
            lesson_status = {}
            for lid in hw_ids:
                st = per_lesson.get(lid)
                if not st:
                    status = 'none';  cnt_none  += 1
                elif not st['made']:
                    status = 'down';  cnt_down  += 1
                elif st['first_try']:
                    status = 'first'; cnt_first += 1
                else:
                    status = 'made';  cnt_made  += 1
                lesson_status[lid] = status
            pct = round((cnt_made + cnt_first) / total * 100) if total else 0
            result.append({
                'id': s['id'], 'username': s['username'],
                'hw_total': total, 'hw_none': cnt_none, 'hw_down': cnt_down,
                'hw_made': cnt_made, 'hw_first': cnt_first, 'pct': pct,
                'lessons': lesson_status,
            })
        return result

    output = []
    for group in groups:
        gid = group['id']
        student_rows = conn.execute(
            'SELECT u.id, u.username FROM users u '
            'JOIN user_groups ug ON u.id=ug.user_id '
            'WHERE ug.group_id=? AND u.role="student" ORDER BY u.username',
            (gid,)
        ).fetchall()
        hw_rows = conn.execute(
            'SELECT l.id, l.title, l.topic FROM lessons l '
            'JOIN topics t ON t.name=l.topic '
            'WHERE t.homework=1 AND ('
            '  t.restricted=0 OR EXISTS ('
            '    SELECT 1 FROM topic_groups tg WHERE tg.topic_name=t.name AND tg.group_id=?'
            '  )'
            ') ORDER BY l.topic COLLATE NOCASE, l.title COLLATE NOCASE', (gid,)
        ).fetchall()
        hw_lessons = [dict(r) for r in hw_rows]
        output.append({
            'id': gid, 'name': group['name'],
            'hw_lessons': hw_lessons,
            'students': student_stats(student_rows, hw_lessons)
        })

    # Ungrouped students
    ungrouped = conn.execute(
        'SELECT u.id, u.username FROM users u '
        'WHERE u.role="student" AND u.id NOT IN (SELECT DISTINCT user_id FROM user_groups) '
        'ORDER BY u.username'
    ).fetchall()
    if ungrouped:
        hw_rows = conn.execute(
            'SELECT l.id, l.title, l.topic FROM lessons l '
            'JOIN topics t ON t.name=l.topic '
            'WHERE t.homework=1 AND t.restricted=0 '
            'ORDER BY l.topic COLLATE NOCASE, l.title COLLATE NOCASE'
        ).fetchall()
        hw_lessons = [dict(r) for r in hw_rows]
        output.append({
            'id': None, 'name': 'Ungrouped',
            'hw_lessons': hw_lessons,
            'students': student_stats(ungrouped, hw_lessons)
        })

    conn.close()
    return jsonify(output)

# ── Claim check ──────────────────────────────────────────────────────────────

@app.route('/api/claim/check', methods=['POST'])
def check_claim():
    """
    Single-dummy claim check.
    Samples plausible distributions of the opponents' hidden cards and runs DDS
    on each. Claim is allowed only when 100% of samples result in making.
    """
    d           = request.json
    hands       = d['hands']        # {N:[...], E:[...], S:[...], W:[...]} remaining card strings
    ns_made     = d['ns_made']
    ew_made     = d['ew_made']
    contract    = d['contract']     # e.g. '4S'
    declarer    = d['declarer']     # 'N','E','S','W'
    next_leader = d['next_leader']  # who leads the next trick

    target = int(contract[0]) + 6
    trump  = contract[1]            # 'S','H','D','C','N'

    partner   = {'N':'S','S':'N','E':'W','W':'E'}
    dummy     = partner[declarer]
    decl_side = {declarer, dummy}

    opp_players = [p for p in ('N','E','S','W') if p not in decl_side]
    opp_p1, opp_p2 = opp_players

    remaining_tricks = len(hands[declarer])   # same for all players between tricks
    decl_made        = ns_made if declarer in ('N','S') else ew_made
    decl_needed      = target - decl_made

    if decl_needed <= 0:
        return jsonify({'can_claim': True,  'pct': 100, 'samples': 0})
    if remaining_tricks < decl_needed:
        return jsonify({'can_claim': False, 'pct': 0,   'samples': 0})

    # Combined opponent cards — we don't know the exact split (single-dummy)
    combined_opp = hands[opp_p1] + hands[opp_p2]
    n_each       = len(hands[opp_p1])
    n_total      = len(combined_opp)

    # Enumerate all splits if small enough, otherwise random sample
    total_combos  = math.comb(n_total, n_each)
    MAX_ENUMERATE = 200
    N_SAMPLES     = 100

    if total_combos <= MAX_ENUMERATE:
        index_sets = [list(s) for s in combinations(range(n_total), n_each)]
    else:
        seen, index_sets = set(), []
        for _ in range(N_SAMPLES * 20):
            if len(index_sets) >= N_SAMPLES:
                break
            idx = tuple(sorted(random.sample(range(n_total), n_each)))
            if idx not in seen:
                seen.add(idx)
                index_sets.append(list(idx))

    rank_order = 'AKQJT98765432'
    def hand_to_pbn(cards):
        suits = {'S':[], 'H':[], 'D':[], 'C':[]}
        for c in cards:
            suits[c[0]].append(c[1])
        return '.'.join(
            ''.join(sorted(suits[s], key=lambda r: rank_order.index(r)))
            for s in ('S','H','D','C')
        )

    is_decl_leader = next_leader in decl_side
    making_count   = 0

    for p1_idx in index_sets:
        p1_set  = set(p1_idx)
        p1_hand = [combined_opp[i] for i in p1_idx]
        p2_hand = [combined_opp[i] for i in range(n_total) if i not in p1_set]

        sample = dict(hands)
        sample[opp_p1] = p1_hand
        sample[opp_p2] = p2_hand

        pbn  = 'N:{} {} {} {}'.format(
            hand_to_pbn(sample['N']), hand_to_pbn(sample['E']),
            hand_to_pbn(sample['S']), hand_to_pbn(sample['W'])
        )
        deal       = Deal(pbn)
        deal.trump = SUIT_MAP[trump]
        deal.first = PLAYER_MAP[next_leader]

        results          = solve_board(deal)
        tricks_this_side = max((t for _, t in results), default=0)

        decl_remaining = tricks_this_side if is_decl_leader else (remaining_tricks - tricks_this_side)
        if decl_made + decl_remaining >= target:
            making_count += 1

    n_samples = len(index_sets)
    pct       = round(making_count / n_samples * 100) if n_samples else 0
    return jsonify({'can_claim': pct == 100, 'pct': pct, 'samples': n_samples})

# ── Telegram bot ─────────────────────────────────────────────────────────────

@app.route('/api/user/telegram/link', methods=['POST'])
def telegram_link_request():
    """Generate a one-time deep-link so the user can connect their Telegram account."""
    user = current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401
    token = secrets.token_urlsafe(16)
    conn  = get_db()
    conn.execute('DELETE FROM telegram_link_tokens WHERE user_id=?', (user['id'],))
    conn.execute('INSERT INTO telegram_link_tokens (token, user_id) VALUES (?,?)',
                 (token, user['id']))
    conn.commit()
    conn.close()
    bot_username = os.environ.get('TELEGRAM_BOT_USERNAME', '')
    deep_link    = f'https://t.me/{bot_username}?start={token}' if bot_username else None
    return jsonify({'link': deep_link, 'token': token})


@app.route('/api/user/telegram', methods=['DELETE'])
def telegram_unlink():
    """Remove the Telegram link for the current user."""
    user = current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401
    conn = get_db()
    conn.execute('UPDATE users SET telegram_chat_id=NULL WHERE id=?', (user['id'],))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/telegram/webhook', methods=['POST'])
def telegram_webhook():
    """Receive updates from the Telegram bot."""
    data = request.json or {}
    msg  = data.get('message') or data.get('edited_message') or {}
    text = msg.get('text', '')
    chat_id = (msg.get('chat') or {}).get('id')
    if not chat_id:
        return jsonify({'ok': True})

    if text.startswith('/start'):
        parts = text.split(None, 1)
        token = parts[1].strip() if len(parts) > 1 else ''
        if token:
            conn = get_db()
            row  = conn.execute(
                'SELECT user_id FROM telegram_link_tokens '
                'WHERE token=? AND created_at > datetime("now","-1 hour")',
                (token,)
            ).fetchone()
            if row:
                conn.execute('UPDATE users SET telegram_chat_id=? WHERE id=?',
                             (str(chat_id), row['user_id']))
                conn.execute('DELETE FROM telegram_link_tokens WHERE token=?', (token,))
                conn.commit()
                conn.close()
                send_telegram(chat_id,
                    "✅ <b>Linked!</b> You'll receive a notification here whenever "
                    "new homework is assigned.")
            else:
                conn.close()
                send_telegram(chat_id,
                    "❌ Link expired or already used. Please generate a new one "
                    "from your profile page in BM3K.")
        else:
            send_telegram(chat_id,
                "Hi! To link your BM3K account tap <b>Connect Telegram</b> "
                "on your profile page.")

    elif text == '/stop':
        conn = get_db()
        conn.execute('UPDATE users SET telegram_chat_id=NULL WHERE telegram_chat_id=?',
                     (str(chat_id),))
        conn.commit()
        conn.close()
        send_telegram(chat_id,
            "🔕 Unlinked. You won't receive homework notifications anymore. "
            "You can re-link anytime from BM3K.")

    return jsonify({'ok': True})


# ── Quip unlocks (Mockédex) ───────────────────────────────────────────────────

@app.route('/api/quips/unlock', methods=['POST'])
def unlock_quip():
    user = current_user()
    if not user:
        return jsonify({'ok': False}), 401
    data  = request.json or {}
    qtype = data.get('type')
    idx   = data.get('idx')
    if qtype not in ('made', 'down') or not isinstance(idx, int):
        return jsonify({'error': 'invalid'}), 400
    conn = get_db()
    conn.execute(
        'INSERT OR IGNORE INTO quip_unlocks (user_id, quip_type, quip_idx) VALUES (?,?,?)',
        (user['id'], qtype, idx))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/quips/unlocked', methods=['GET'])
def get_unlocked_quips():
    user = current_user()
    if not user:
        return jsonify({'made': [], 'down': []})
    conn = get_db()
    rows = conn.execute(
        'SELECT quip_type, quip_idx FROM quip_unlocks WHERE user_id=?',
        (user['id'],)
    ).fetchall()
    conn.close()
    result = {'made': [], 'down': []}
    for r in rows:
        result[r['quip_type']].append(r['quip_idx'])
    return jsonify(result)

# ── Entry point ───────────────────────────────────────────────────────────────

init_db()
_init_ben()

# Bootstrap first teacher account from environment variables.
# Set BOOTSTRAP_USER and BOOTSTRAP_PASS in Railway dashboard.
# Account is created once on startup; remove the vars afterwards.
_bu = os.environ.get('BOOTSTRAP_USER', '').strip()
_bp = os.environ.get('BOOTSTRAP_PASS', '').strip()
if _bu and _bp:
    try:
        conn = get_db()
        existing = conn.execute('SELECT id FROM users WHERE username=?', (_bu,)).fetchone()
        hashed = generate_password_hash(_bp)
        if existing:
            conn.execute('UPDATE users SET password_hash=?, role=? WHERE username=?',
                         (hashed, 'teacher', _bu))
            print(f'  Bootstrap: password updated for teacher "{_bu}".')
        else:
            conn.execute('INSERT INTO users (username, password_hash, role) VALUES (?,?,?)',
                         (_bu, hashed, 'teacher'))
            print(f'  Bootstrap: teacher account "{_bu}" created.')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'  Bootstrap error: {e}')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f'\n{"="*50}')
    print('  Bad Manners 3000 — BM3K')
    print(f'{"="*50}')
    print(f'  DB:   {DB_PATH}')
    print(f'  Open: http://localhost:{port}')
    print(f'{"="*50}\n')
    app.run(host='0.0.0.0', port=port, debug=False)
