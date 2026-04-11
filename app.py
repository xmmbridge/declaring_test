"""
Bad Manners 3000 — Declarer Play Trainer
Flask app with SQLite, endplay DDS, and username/password accounts
"""

from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, json, os, functools
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
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'student',
            created_at    TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS topics (
            name          TEXT PRIMARY KEY,
            restricted    INTEGER NOT NULL DEFAULT 0
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
    ''')
    conn.commit()
    # Migrations for existing databases
    for stmt in [
        'ALTER TABLE lessons  ADD COLUMN topic     TEXT DEFAULT ""',
        'ALTER TABLE attempts ADD COLUMN user_id   INTEGER',
        'ALTER TABLE attempts ADD COLUMN lin_data  TEXT DEFAULT ""',
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

    # md: dealer=1 (South), then S/W/N hands (E is computed by viewer)
    md = f"1{pbn_to_lin(s_hand)},{pbn_to_lin(w_hand)},{pbn_to_lin(n_hand)}"

    # Synthetic auction: passes up to declarer, then contract bid, then 3 passes
    pos_of = {'S': 0, 'W': 1, 'N': 2, 'E': 3}
    level, suit = contract[0], contract[1]
    auction = ['p'] * pos_of[declarer] + [f"{level}{suit}", 'p', 'p', 'p']
    mb_str  = ''.join(f'mb|{c}|' for c in auction) + 'pg||'

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

@app.route('/auth/debug')
def auth_debug():
    """Temporary: shows whether any users exist and bootstrap vars are present."""
    conn = get_db()
    count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    users = conn.execute('SELECT username, role FROM users').fetchall()
    conn.close()
    return jsonify({
        'db_path': DB_PATH,
        'user_count': count,
        'users': [dict(u) for u in users],
        'bootstrap_user_set': bool(os.environ.get('BOOTSTRAP_USER')),
        'bootstrap_pass_set': bool(os.environ.get('BOOTSTRAP_PASS')),
    })

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
    return jsonify(current_user())

# ── Users (teacher only) ──────────────────────────────────────────────────────

@app.route('/api/users', methods=['GET'])
@teacher_required
def get_users():
    conn = get_db()
    rows = conn.execute(
        'SELECT id, username, role, created_at FROM users ORDER BY username COLLATE NOCASE'
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

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

# ── Topics ────────────────────────────────────────────────────────────────────

@app.route('/api/topics', methods=['GET'])
def get_topics():
    conn = get_db()
    rows = conn.execute('''
        SELECT DISTINCT l.topic, COALESCE(t.restricted, 0) AS restricted
        FROM lessons l
        LEFT JOIN topics t ON t.name = l.topic
        WHERE l.topic != ''
        ORDER BY l.topic COLLATE NOCASE
    ''').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/topics/<path:name>/restricted', methods=['PUT'])
@teacher_required
def set_topic_restricted(name):
    restricted = 1 if request.json.get('restricted') else 0
    conn = get_db()
    conn.execute('INSERT OR REPLACE INTO topics (name, restricted) VALUES (?,?)',
                 (name, restricted))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Lessons ───────────────────────────────────────────────────────────────────

@app.route('/api/lessons', methods=['GET'])
def get_lessons():
    user = current_user()
    conn = get_db()
    if user:  # logged-in users see all lessons
        rows = conn.execute(
            'SELECT * FROM lessons ORDER BY title COLLATE NOCASE ASC'
        ).fetchall()
    else:     # guests see only non-restricted topics
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
        'INSERT INTO lessons (title,topic,technique,explanation,pbn,contract,declarer,lead,par_tricks) '
        'VALUES (?,?,?,?,?,?,?,?,?)',
        (d['title'], d.get('topic',''), d.get('technique',''), d.get('explanation',''),
         d['pbn'], d['contract'], d['declarer'], d['lead'], par_tricks))
    lid = cur.lastrowid
    conn.commit(); conn.close()
    return jsonify({'id': lid, 'par_tricks': par_tricks}), 201

@app.route('/api/lessons/<int:lid>', methods=['PUT'])
@teacher_required
def update_lesson(lid):
    d = request.json
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
        'UPDATE lessons SET title=?,topic=?,technique=?,explanation=?,pbn=?,contract=?,declarer=?,lead=?,par_tricks=? WHERE id=?',
        (d['title'], d.get('topic',''), d.get('technique',''), d.get('explanation',''),
         d['pbn'], d['contract'], d['declarer'], d['lead'], par_tricks, lid))
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

# ── DDS ───────────────────────────────────────────────────────────────────────

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

@app.route('/api/dds/next_move', methods=['POST'])
def dds_next_move():
    d         = request.json
    remaining = d['remaining_hands']
    pbn       = remaining_to_pbn(remaining)
    deal      = Deal(pbn)
    deal.trump = SUIT_MAP[d['trump']]

    current_trick = d.get('current_trick', [])
    if current_trick:
        deal.first = PLAYER_MAP[current_trick[0]['player']]
        for entry in current_trick:
            try:
                deal.play(str_to_card(entry['card']), from_hand=False)
            except Exception as ex:
                print('play error:', ex)
    else:
        deal.first = PLAYER_MAP[d['next_player']]

    results = list(solve_board(deal))
    best_card, best_tricks = max(results, key=lambda x: x[1])
    return jsonify({
        'best_card':   card_to_str(best_card),
        'tricks':      best_tricks,
        'all_options': sorted([(card_to_str(c), t) for c, t in results],
                              key=lambda x: x[1], reverse=True)
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
    conn = get_db()
    rows = conn.execute(
        'SELECT a.*, l.title as lesson_title '
        'FROM attempts a JOIN lessons l ON a.lesson_id=l.id '
        'WHERE a.lesson_id=? ORDER BY a.played_at DESC', (lid,)).fetchall()
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

# ── Entry point ───────────────────────────────────────────────────────────────

init_db()

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
