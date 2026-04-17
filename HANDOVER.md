# Developer Handover — Bridge Master (BM3k)

Last updated: 2026-04-17

---

## Project Overview

Flask + SQLite web app for bridge teachers. Teachers create hands/contracts,
students play them out. Robots play optimally using Double Dummy Solver (DDS).
Deployed on Railway. Telegram bot integration for lesson delivery.

**Key files:**
- `app.py` — Flask backend, DDS engine, BEN ONNX inference
- `index.html` — Complete frontend (single-file, no framework)

---

## Recent Features Implemented (this session)

### 1. Defence Mode (`mode = 'defence'`)
Students now play as a **defender** instead of declarer. Robots play:
- **Robot declarer**: DDS highest-card tiebreaker (simple greedy)
- **Robot partner-defender**: BEN ONNX models (lefty/righty × nt/suit)

**DB columns added** (with migrations in `app.py`):
```sql
ALTER TABLE lessons ADD COLUMN mode TEXT DEFAULT "declarer";
ALTER TABLE lessons ADD COLUMN student_seat TEXT DEFAULT "";
```

**`create_lesson` / `update_lesson`** — both include `mode` and `student_seat`.

**`dds_next_move` endpoint** — detects whether acting player is declarer/dummy
(uses highest-card tiebreak) or defender (uses BEN + heuristics).

### 2. Quip System (player-centric, not contract-centric)

`QUIPS_MADE` = player **succeeded** (regardless of mode)
`QUIPS_DOWN` = player **failed**

In defence mode: `playerSucceeded = diff < 0` (player held declarer below target).
In declarer mode: `playerSucceeded = diff >= 0` (contract made).

Defence-mode quip filter sets (only these indices shown in defence mode):
```javascript
const QUIPS_DOWN_DEFENCE_OK = new Set([
  1, 4, 5, 7, 10, 15, 19, 21, 22, 23, 25, 26, 27, 32, 34, 36, 37, 40,
  43, 44, 45, 46, 47, 49, 52, 53, 54, 55, 57, 59, 61, 64, 65, 66, 67,
  68, 72, 73, 74, 75, 76, 77, 78, 79, 81, 85, 88, 90, 92, 94, 95, 97,
  98, 101, 102, 104, 106, 108, 110,
]);
const QUIPS_MADE_DEFENCE_OK = new Set([3, 6, 10, 12, 13, 17, 19, 20]);
```

### 3. View Rotation — Player Always at Bottom

`buildViewMap(playerSeat)` in `index.html` maps compass seats to visual DOM slots
so the student's hand is always rendered at the bottom of the table.

Visual DOM slots: `'S'` = bottom, `'N'` = top, `'W'` = left, `'E'` = right

```javascript
function buildViewMap(playerSeat) {
  const cw  = ['N', 'E', 'S', 'W'];
  const idx = cw.indexOf(playerSeat);
  return {
    [cw[ idx         ]]: 'S',   // player        → bottom
    [cw[(idx + 1) % 4]]: 'W',   // LHO (cw next) → left
    [cw[(idx + 2) % 4]]: 'N',   // partner       → top
    [cw[(idx + 3) % 4]]: 'E',   // RHO           → right
  };
}
function viewSlot(player) {
  return (state.viewMap && state.viewMap[player]) || player;
}
```

All 6 DOM lookup sites use `viewSlot()` instead of raw compass seat.

`state` object fields added:
```javascript
mode: 'declarer',
studentSeat: '',
viewMap: null,
lastTrick: [],
lastTrickLeader: null,
```

### 4. Dummy Card Layout — **PENDING IMPLEMENTATION**

**This is the next task to implement.**

**Design spec:**
- When `viewSlot(state.dummy)` is `'N'` or `'S'` (dummy at top or bottom of screen):
  - Render 4 **vertical columns**, one per suit
  - Cards stacked top-to-bottom (A→2), slight vertical fanning (negative `margin-top`)
- When `viewSlot(state.dummy)` is `'W'` or `'E'` (dummy at left or right of screen):
  - Render 4 **horizontal rows**, one per suit
  - Cards stacked left-to-right (A→2), slight horizontal fanning (negative `margin-left`)
- All 4 suits always shown even if void (display `—` for empty suits)
- Trump suit first, then remaining suits in S/H/D/C order (minus trump)
- Cards still have `data-card` attribute for existing click handlers
- Fix `isSideHand` in `renderHand` to use `['E','W'].includes(viewSlot(player))` instead of raw compass direction

**CSS to add:**
```css
/* Dummy vertical layout (dummy at top/bottom) */
.dummy-vert {
  display: flex;
  flex-direction: row;       /* columns side by side */
  gap: 6px;
  justify-content: center;
}
.dummy-suit-col {
  display: flex;
  flex-direction: column;    /* cards stack downward */
  align-items: center;
}
.dummy-suit-col .card + .card {
  margin-top: -60px;         /* fanning overlap */
}

/* Dummy horizontal layout (dummy at left/right) */
.dummy-horiz {
  display: flex;
  flex-direction: column;    /* rows stacked */
  gap: 4px;
}
.dummy-suit-row {
  display: flex;
  flex-direction: row;       /* cards spread right */
  align-items: center;
}
.dummy-suit-row .card + .card {
  margin-left: -30px;        /* fanning overlap */
}

.suit-hdr {
  font-size: 14px;
  font-weight: bold;
  margin-bottom: 2px;
}
```

**`renderHand(player)` logic change (in `index.html`):**
```javascript
// Near the top of renderHand, fix isSideHand:
const isSideHand = ['E', 'W'].includes(viewSlot(player));

// When player is dummy, use new layout:
if (player === state.dummy) {
  const dummySlot = viewSlot(player);
  const isVertical = dummySlot === 'N' || dummySlot === 'S';
  const suitOrder = trumpFirstSuitOrder(state.contract); // S/H/D/C with trump first
  const bysuit = groupCardsBySuit(hand);
  // render .dummy-vert or .dummy-horiz wrapper with suit cols/rows
  // each col/row: suit header (♠/♥/♦/♣) + cards with data-card
  // void suit: header + '—'
}
```

---

## Known Bugs / Dead Code

- `LEFT_OF` constant in `index.html` around line 1905 has wrong direction
  `{ N:'W', E:'N', S:'E', W:'S' }` — but it's **unused dead code**, safe to ignore or delete.
- `_BEN_LEFT_OF` in `app.py` was fixed this session to `{'N':'E','E':'S','S':'W','W':'N'}`.

---

## Architecture Notes

### BEN ONNX (defender AI)
- Models: `lefty_nt.onnx`, `righty_nt.onnx`, `lefty_suit.onnx`, `righty_suit.onnx`
- 298-dim input tensor per trick
- `_BEN_LEFT_OF = {'N':'E','E':'S','S':'W','W':'N'}` (clockwise = LHO direction)
- Selected by `_defender_tiebreak(candidates, defender_hand, current_trick, ...)`

### DDS Endpoint (`/dds_next_move`)
- `solve_board` from `endplay` library maximises tricks for the acting side
- Declarer/dummy → `min(candidates, key=lambda c: RANK_ORD.index(c[1]))` (highest rank)
- Defender → `_defender_tiebreak(...)` using BEN + heuristics

### Database
- SQLite, file: `bridge.db`
- Tables: `lessons`, `results`
- Migrations run at startup in `app.py`

### Telegram Bot
- Webhook registration:
  ```bash
  curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
       -d "url=https://<YOUR_RAILWAY_URL>/telegram"
  ```

---

## Git / Deployment

- Repo: `xmmbridge/declaring_test` on GitHub
- Dev branch for this feature work: `claude/project-onboarding-PnCiK`
- Production: `main` branch → auto-deploys on Railway

---

## Immediate Next Step

**Implement dummy card layout** (see section 4 above).
Changes needed:
1. Add CSS for `.dummy-vert`, `.dummy-horiz`, `.dummy-suit-col`, `.dummy-suit-row`, `.suit-hdr`
2. Modify `renderHand(player)` in `index.html`:
   - Fix `isSideHand` to use `viewSlot()`
   - Add dummy-specific rendering branch
3. Commit and push to `claude/project-onboarding-PnCiK`, then merge/push to `main`
