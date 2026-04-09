# 🃏 Bridge Master — Declarer Play Trainer

A web app for bridge teachers. Create hands, set contracts, and let students
play as declarer against a robot that **always defends perfectly** using
Double Dummy Solver (DDS) analysis. Scores are recorded in a database.

---

## What It Does

1. **Teacher** creates a lesson — enters 4 hands, contract, declarer, opening lead
2. **Student** picks a lesson, plays as declarer card by card
3. **Robot** (East/West defenders) plays the mathematically optimal card every time
4. **Scores** are saved — tricks made, bridge score, date

---

## Run Locally (Your Computer)

### Step 1 — Install Python
Download from [python.org](https://python.org) if you don't have it.

### Step 2 — Install dependencies
Open a terminal in this folder and run:
```bash
pip install -r requirements.txt
```

### Step 3 — Start the app
```bash
python app.py
```

### Step 4 — Open in browser
```
http://localhost:5000
```

---

## Deploy to Railway (Recommended — Free)

Railway is the easiest way to put this online for your whole bridge club.

### Step 1 — Push to GitHub
1. Create a free account at [github.com](https://github.com)
2. Create a new repository (click **+** → **New repository**)
3. Name it `bridge-master`, make it **Private**, click **Create repository**
4. Follow GitHub's instructions to push this folder:
```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/bridge-master.git
git push -u origin main
```

### Step 2 — Deploy on Railway
1. Go to [railway.app](https://railway.app) and sign up (free)
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your `bridge-master` repository
4. Railway automatically detects Python and deploys it
5. Click **Settings** → **Networking** → **Generate Domain**
6. Your app is live at something like `bridge-master-production.up.railway.app`

**That's it.** Every time you push to GitHub, Railway auto-redeploys.

---

## Deploy to Render (Alternative — Also Free)

1. Push to GitHub (same as Step 1 above)
2. Go to [render.com](https://render.com) and sign up
3. Click **New** → **Web Service** → **Connect a repository**
4. Select your repo — Render reads `render.yaml` automatically
5. Click **Create Web Service**
6. Your app gets a URL like `bridge-master.onrender.com`

> ⚠️ Render's free tier **spins down** after 15 mins of inactivity.
> First visit after idle takes ~30 seconds to wake up.
> Railway's free tier stays awake.

---

## Important Note About the Database

SQLite stores data on the server's disk. On **Railway and Render free tiers**,
the disk resets when the app redeploys. This means:

- ✅ Fine for a single bridge club session
- ⚠️ Lessons and scores may reset on redeploy

**To keep data permanently**, upgrade to Railway's $5/month plan which includes
a persistent volume, or switch to a free PostgreSQL database (ask for help if needed).

---

## Hand Format

Enter each hand as: **Spades.Hearts.Diamonds.Clubs**

```
AKQ.JT98.76.KJ32
```
means: ♠AKQ  ♥JT98  ♦76  ♣KJ32

- Use **T** for Ten
- All 4 hands must add up to exactly **52 cards**

## Opening Lead Format

Two characters: **suit letter + rank**

| Example | Meaning |
|---------|---------|
| `SA`    | Ace of Spades |
| `H7`    | 7 of Hearts |
| `DK`    | King of Diamonds |
| `CT`    | Ten of Clubs |

---

## File Structure

```
bridge-master/
├── app.py              ← Python backend (Flask + DDS engine)
├── static/
│   └── index.html      ← Complete frontend (one file)
├── requirements.txt    ← Python packages
├── Procfile            ← For Railway/Render/Heroku
├── railway.toml        ← Railway config
├── render.yaml         ← Render config
├── Dockerfile          ← For Docker/container deployment
├── .gitignore          ← Files to exclude from git
└── README.md           ← This file
```

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend   | Python / Flask |
| DDS Engine | endplay (python wrapper for Bo Haglund's DDS) |
| Database  | SQLite |
| Frontend  | Plain HTML + CSS + JavaScript (no framework needed) |
| Server    | Gunicorn |
