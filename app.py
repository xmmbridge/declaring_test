"""
Bridge Master — Declarer Play Trainer
Production-ready Flask app with SQLite + endplay DDS
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import sqlite3, json, os
from endplay.types import Deal, Player, Denom, Card, Rank
from endplay.dds import solve_board

app = Flask(__name__, static_folder='.')
CORS(app)

# Use /tmp for writable storage on cloud platforms (Render, Railway, Fly.io)
# Falls back to local directory for development
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
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS lessons (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
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
            tricks_made    INTEGER NOT NULL,
            contract_level INTEGER NOT NULL,
            result         TEXT NOT NULL,
            score          INTEGER NOT NULL,
            play_sequence  TEXT DEFAULT '[]',
            played_at      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (lesson_id) REFERENCES lessons(id)
        );
    ''')
    conn.commit()
    conn.close()

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

# ── Health check (for Railway / Render) ──────────────────────────────────────

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

# ── Lessons ───────────────────────────────────────────────────────────────────

@app.route('/api/lessons', methods=['GET'])
def get_lessons():
    conn = get_db()
    rows = conn.execute('SELECT * FROM lessons ORDER BY created_at DESC').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/lessons/<int:lid>', methods=['GET'])
def get_lesson(lid):
    conn = get_db()
    row = conn.execute('SELECT * FROM lessons WHERE id=?', (lid,)).fetchone()
    conn.close()
    return jsonify(dict(row)) if row else (jsonify({'error': 'Not found'}), 404)

@app.route('/api/lessons', methods=['POST'])
def create_lesson():
    d = request.json
    par_tricks = int(d['contract'][0]) + 6  # safe default
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
        'INSERT INTO lessons (title,technique,explanation,pbn,contract,declarer,lead,par_tricks) '
        'VALUES (?,?,?,?,?,?,?,?)',
        (d['title'], d.get('technique',''), d.get('explanation',''),
         d['pbn'], d['contract'], d['declarer'], d['lead'], par_tricks))
    lid = cur.lastrowid
    conn.commit(); conn.close()
    return jsonify({'id': lid, 'par_tricks': par_tricks}), 201

@app.route('/api/lessons/<int:lid>', methods=['PUT'])
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
        'UPDATE lessons SET title=?,technique=?,explanation=?,pbn=?,contract=?,declarer=?,lead=?,par_tricks=? WHERE id=?',
        (d['title'], d.get('technique',''), d.get('explanation',''),
         d['pbn'], d['contract'], d['declarer'], d['lead'], par_tricks, lid))
    conn.commit(); conn.close()
    return jsonify({'id': lid, 'par_tricks': par_tricks})

@app.route('/api/lessons/<int:lid>', methods=['DELETE'])
def delete_lesson(lid):
    conn = get_db()
    conn.execute('DELETE FROM lessons WHERE id=?', (lid,))
    conn.execute('DELETE FROM attempts WHERE lesson_id=?', (lid,))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

# ── DDS ───────────────────────────────────────────────────────────────────────

def remaining_to_pbn(remaining):
    """Build a PBN string directly from remaining hand card lists."""
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
        deal.first = PLAYER_MAP[current_trick[0]['player']]  # trick leader
        for entry in current_trick:
            try:
                deal.play(str_to_card(entry['card']), from_hand=False)
            except Exception as ex:
                print('play error:', ex)
    else:
        deal.first = PLAYER_MAP[d['next_player']]

    results = list(solve_board(deal))
    # solve_board returns tricks for the current player's side (always a defender here).
    # Maximise to get the best defensive play.
    best_card, best_tricks = max(results, key=lambda x: x[1])
    return jsonify({
        'best_card':       card_to_str(best_card),
        'tricks':          best_tricks,
        'all_options':     sorted([(card_to_str(c), t) for c, t in results],
                                  key=lambda x: x[1], reverse=True)
    })

# ── Attempts ──────────────────────────────────────────────────────────────────

@app.route('/api/attempts', methods=['POST'])
def save_attempt():
    d           = request.json
    contract    = d['contract']
    tricks_made = d['tricks_made']
    level       = int(contract[0])
    diff        = tricks_made - (level + 6)
    result      = ('Made +'+str(diff) if diff > 0 else
                   'Made exactly'     if diff == 0 else
                   'Down '+str(abs(diff)))
    score = calculate_score(contract, tricks_made)
    conn  = get_db()
    cur   = conn.execute(
        'INSERT INTO attempts (lesson_id,student_name,tricks_made,contract_level,'
        'result,score,play_sequence) VALUES (?,?,?,?,?,?,?)',
        (d['lesson_id'], d['student_name'], tricks_made, level,
         result, score, json.dumps(d.get('play_sequence', []))))
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
        'ORDER BY a.played_at DESC LIMIT 200').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

# ── Entry point ───────────────────────────────────────────────────────────────

init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f'\n{"="*50}')
    print('  Bridge Master — Declarer Play Trainer')
    print(f'{"="*50}')
    print(f'  DB:   {DB_PATH}')
    print(f'  Open: http://localhost:{port}')
    print(f'{"="*50}\n')
    app.run(host='0.0.0.0', port=port, debug=False)
