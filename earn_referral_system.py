#!/usr/bin/env python3
"""
Global FIFA Rewards - Telegram Referral Bot + Admin Panel
Production-ready version (Waitress + Environment Variables)
"""

import asyncio
import logging
import os
import sys
import threading
from datetime import datetime, timedelta
from functools import wraps
import sqlite3
from contextlib import contextmanager

from flask import Flask, request, render_template_string, redirect, url_for, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import TelegramError

# ==================== কনফিগারেশন (এনভায়রনমেন্ট ভেরিয়েবল থেকে) ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable not set!")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# ফ্লাস্ক সিক্রেট কী (প্রোডাকশনে অবশ্যই সেট করবে)
app_secret = os.environ.get("SECRET_KEY", os.urandom(24).hex())

# ডিফল্ট সেটিংস
DEFAULT_REFERRAL_REWARD = 10
DEFAULT_DAILY_BONUS = 5
DEFAULT_MIN_WITHDRAW = 100
DEFAULT_WITHDRAW_METHODS = "TRC20 (USDT), BEP20 (USDT)"
DEFAULT_WELCOME_MSG = """🚀 PRE-LAUNCH AIRDROP EVENT IS NOW OPEN! 🚀

⚽ Welcome to Global FIFA Rewards!

Be among the first members and secure your spot in our upcoming reward campaign! 🎉

🎁 $500 AIRDROP GIVEAWAY POOL
👥 Invite friends and grow your team
🏆 Top 50 Referrers will get a chance to win up to $100 Bonus Rewards
💰 Multiple reward opportunities for active participants
🔥 Limited Pre-Launch Event

📋 How to Participate:
✅ Join the community
✅ Complete the pre-registration process
✅ Share your referral link
✅ Invite more friends to increase your ranking

💸 Supported Payment Networks
🔹 TRC20 (USDT)
🔹 BEP20 (USDT)

📈 The more referrals you bring, the higher your chance of ranking among the Top 50 participants.

⏳ Early supporters may receive exclusive event benefits and bonus opportunities!

━━━━━━━━━━━━━━━
🎁 Giveaway Pool: $500
🏆 Top 50 Referrer Challenge
💰 Up to $100 Reward Opportunity
⚡ Fast & Easy Participation
━━━━━━━━━━━━━━━

📢 Official Channel (Important Notice): @fifa_reward
💬 Official Group (Communication): @fifareward

🌍 Join now and start building your referral network before the official launch!

⚽ Global FIFA Rewards
🎯 Play • Refer • Earn

#Airdrop #Giveaway #USDT #TRC20 #BEP20 #ReferralRewards #GlobalFIFARewards 🚀⚽💰"""

# -------------------- লগিং --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# -------------------- ডাটাবেস --------------------
DB_NAME = "earn_referral.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        join_date TEXT,
        balance REAL DEFAULT 0,
        referral_count INTEGER DEFAULT 0,
        total_earned REAL DEFAULT 0,
        completed_tasks INTEGER DEFAULT 0,
        last_daily_claim TEXT,
        blocked INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER,
        created_at TEXT,
        UNIQUE(referrer_id, referred_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        description TEXT,
        reward REAL,
        link TEXT,
        type TEXT DEFAULT 'other',
        enabled INTEGER DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS task_completions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task_id INTEGER,
        completed_at TEXT,
        UNIQUE(user_id, task_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_username TEXT,
        channel_link TEXT,
        enabled INTEGER DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS withdraw_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        payment_method TEXT,
        account_details TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS admin (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event TEXT,
        timestamp TEXT
    )''')

    # অ্যাডমিন অ্যাকাউন্ট
    c.execute("INSERT OR IGNORE INTO admin (username, password) VALUES (?, ?)",
              (ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD)))

    # সেটিংস
    defaults = [
        ('referral_reward', str(DEFAULT_REFERRAL_REWARD)),
        ('daily_bonus', str(DEFAULT_DAILY_BONUS)),
        ('min_withdraw', str(DEFAULT_MIN_WITHDRAW)),
        ('withdraw_methods', DEFAULT_WITHDRAW_METHODS),
        ('welcome_message', DEFAULT_WELCOME_MSG),
        ('force_join_enabled', '1'),
        ('bot_token', BOT_TOKEN)
    ]
    for k, v in defaults:
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # ডিফল্ট টাস্ক
    tasks = [
        ("Join Official Channel", "Join our Telegram channel for updates.", 20, "https://t.me/fifa_reward", 'channel', 1),
        ("Join Official Group", "Join our community group.", 15, "https://t.me/fifareward", 'group', 1),
        ("Visit Website", "Visit our pre-launch page.", 10, "https://yourwebsite.com", 'website', 1),
        ("Share Referral Link", "Share your referral link in any group.", 15, "", 'share', 1),
        ("Invite 5 Friends", "Get 5 people to join using your link.", 50, "", 'invite', 1),
    ]
    for title, desc, reward, link, ttype, enabled in tasks:
        c.execute("INSERT OR IGNORE INTO tasks (title, description, reward, link, type, enabled) VALUES (?,?,?,?,?,?)",
                  (title, desc, reward, link, ttype, enabled))
    conn.commit()
    conn.close()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def get_setting(key):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row['value'] if row else None

def set_setting(key, value):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

# -------------------- টেলিগ্রাম বট --------------------
BOT_USERNAME = None
application = None
bot_loop = None

async def check_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    with get_db() as conn:
        channels = conn.execute("SELECT channel_username, channel_link FROM channels WHERE enabled = 1").fetchall()
    if not channels:
        return True
    not_joined = []
    for ch in channels:
        try:
            chat_member = await context.bot.get_chat_member(chat_id=ch['channel_username'], user_id=user_id)
            if chat_member.status in ['left', 'kicked', 'banned']:
                not_joined.append(ch)
        except:
            not_joined.append(ch)
    if not_joined:
        keyboard = []
        for ch in not_joined:
            keyboard.append([InlineKeyboardButton(f"Join {ch['channel_username']}", url=ch['channel_link'])])
        keyboard.append([InlineKeyboardButton("✅ Check Join", callback_data="check_join")])
        await update.message.reply_html(
            "⚠️ <b>Please join the required channel(s) to continue.</b>\nAfter joining, press <b>✅ Check Join</b>.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return False
    return True

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    with get_db() as conn:
        channels = conn.execute("SELECT channel_username, channel_link FROM channels WHERE enabled = 1").fetchall()
    if not channels:
        await query.edit_message_text("✅ You can now use the bot!", reply_markup=None)
        return
    not_joined = []
    for ch in channels:
        try:
            chat_member = await context.bot.get_chat_member(chat_id=ch['channel_username'], user_id=user_id)
            if chat_member.status in ['left', 'kicked', 'banned']:
                not_joined.append(ch)
        except:
            not_joined.append(ch)
    if not_joined:
        await query.edit_message_text("❌ You haven't joined all channels yet. Please join and try again.",
                                      reply_markup=query.message.reply_markup)
    else:
        await query.edit_message_text("✅ You are now subscribed! You can use the bot.", reply_markup=None)

def force_join_required(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_force_join(update, context):
            return
        return await func(update, context)
    return wrapper

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    with get_db() as conn:
        row = conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            join_date = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO users (user_id, username, first_name, join_date) VALUES (?, ?, ?, ?)",
                (user_id, user.username, user.first_name, join_date)
            )
            if context.args and context.args[0].isdigit():
                referrer_id = int(context.args[0])
                if referrer_id != user_id:
                    try:
                        conn.execute("INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?, ?, ?)",
                                     (referrer_id, user_id, datetime.now().isoformat()))
                        reward = float(get_setting('referral_reward') or DEFAULT_REFERRAL_REWARD)
                        conn.execute("UPDATE users SET balance = balance + ?, total_earned = total_earned + ?, referral_count = referral_count + 1 WHERE user_id = ?",
                                     (reward, reward, referrer_id))
                        conn.execute("INSERT INTO logs (event, timestamp) VALUES (?, ?)",
                                     (f"Referral: {referrer_id} referred {user_id}, reward {reward}", datetime.now().isoformat()))
                    except sqlite3.IntegrityError:
                        pass
            conn.commit()
    welcome = get_setting('welcome_message') or DEFAULT_WELCOME_MSG
    await update.message.reply_text(welcome)
    if await check_force_join(update, context):
        await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["💰 Balance", "👥 Referrals"],
        ["🎁 Daily Bonus", "📋 Tasks"],
        ["💳 Withdraw", "🏆 Leaderboard"],
        ["👤 Profile", "📊 My Statistics"],
        ["ℹ️ Help"]
    ]
    await update.message.reply_text("📌 Main Menu:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

@force_join_required
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    options = {
        "💰 Balance": balance,
        "👥 Referrals": referrals_info,
        "🎁 Daily Bonus": daily_bonus,
        "📋 Tasks": tasks_list,
        "💳 Withdraw": withdraw_request,
        "🏆 Leaderboard": leaderboard,
        "👤 Profile": profile,
        "📊 My Statistics": statistics,
        "ℹ️ Help": help_info,
    }
    if text in options:
        await options[text](update, context)
    else:
        await update.message.reply_text("Use the menu buttons.")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with get_db() as conn:
        row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        bal = row['balance'] if row else 0
    await update.message.reply_text(f"💰 Your Balance: <b>{bal:.2f}</b> coins", parse_mode='HTML')

async def referrals_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    with get_db() as conn:
        cnt = conn.execute("SELECT COUNT(*) as cnt FROM referrals WHERE referrer_id = ?", (user_id,)).fetchone()['cnt']
    msg = f"👥 Your Referrals: <b>{cnt}</b>\n🔗 Referral Link:\n<code>{ref_link}</code>"
    await update.message.reply_html(msg)

async def daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with get_db() as conn:
        row = conn.execute("SELECT last_daily_claim FROM users WHERE user_id = ?", (user_id,)).fetchone()
    now = datetime.now()
    if row and row['last_daily_claim']:
        last = datetime.fromisoformat(row['last_daily_claim'])
        if (now - last) < timedelta(hours=24):
            remaining = timedelta(hours=24) - (now - last)
            hours, remainder = divmod(remaining.seconds, 3600)
            minutes = remainder // 60
            await update.message.reply_text(f"⏳ You already claimed your daily bonus. Come back in {hours}h {minutes}m.")
            return
    bonus = float(get_setting('daily_bonus') or DEFAULT_DAILY_BONUS)
    with get_db() as conn:
        conn.execute("UPDATE users SET balance = balance + ?, total_earned = total_earned + ?, last_daily_claim = ? WHERE user_id = ?",
                     (bonus, bonus, now.isoformat(), user_id))
        conn.commit()
    await update.message.reply_text(f"🎁 Daily Bonus: <b>{bonus}</b> coins added!", parse_mode='HTML')

async def tasks_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with get_db() as conn:
        tasks = conn.execute("SELECT * FROM tasks WHERE enabled = 1").fetchall()
    if not tasks:
        await update.message.reply_text("📋 No available tasks.")
        return
    for task in tasks:
        with get_db() as conn:
            comp = conn.execute("SELECT 1 FROM task_completions WHERE user_id = ? AND task_id = ?", (user_id, task['id'])).fetchone()
        done = bool(comp)
        btn_text = "✅ Completed" if done else "📝 Complete"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(btn_text, callback_data=f"complete_task_{task['id']}")]])
        msg = f"📌 <b>{task['title']}</b>\n{task['description']}\n💰 Reward: {task['reward']} coins"
        await update.message.reply_html(msg, reply_markup=keyboard)

async def complete_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[2])
    user_id = query.from_user.id
    with get_db() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id = ? AND enabled = 1", (task_id,)).fetchone()
        if not task:
            await query.edit_message_text("❌ Task not available.", reply_markup=None)
            return
        comp = conn.execute("SELECT 1 FROM task_completions WHERE user_id = ? AND task_id = ?", (user_id, task_id)).fetchone()
        if comp:
            await query.edit_message_text("✅ Already completed.", reply_markup=None)
            return
        conn.execute("INSERT INTO task_completions (user_id, task_id, completed_at) VALUES (?, ?, ?)",
                     (user_id, task_id, datetime.now().isoformat()))
        conn.execute("UPDATE users SET balance = balance + ?, total_earned = total_earned + ?, completed_tasks = completed_tasks + 1 WHERE user_id = ?",
                     (task['reward'], task['reward'], user_id))
        conn.commit()
    await query.edit_message_text(f"✅ Task completed! +{task['reward']} coins", reply_markup=None)

async def withdraw_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with get_db() as conn:
        row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return
    bal = row['balance']
    min_wd = float(get_setting('min_withdraw') or DEFAULT_MIN_WITHDRAW)
    if bal < min_wd:
        await update.message.reply_text(f"❌ Minimum withdraw is {min_wd} coins. Your balance: {bal:.2f}")
        return
    await update.message.reply_text(
        "💳 Withdraw request\n\n"
        "Use format:\n<code>/withdraw AMOUNT METHOD ACCOUNT_DETAILS</code>\n"
        "Example: <code>/withdraw 100 TRC20 TYourWalletAddress</code>",
        parse_mode='HTML'
    )

async def process_withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args or len(context.args) < 3:
        await update.message.reply_text("⚠️ Use: /withdraw AMOUNT METHOD ACCOUNT_DETAILS")
        return
    try:
        amount = float(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")
        return
    method = context.args[1]
    details = " ".join(context.args[2:])
    min_wd = float(get_setting('min_withdraw') or DEFAULT_MIN_WITHDRAW)
    with get_db() as conn:
        row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not row or row['balance'] < amount:
            await update.message.reply_text("❌ Insufficient balance.")
            return
        if amount < min_wd:
            await update.message.reply_text(f"❌ Minimum withdraw is {min_wd}.")
            return
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
        conn.execute("INSERT INTO withdraw_requests (user_id, amount, payment_method, account_details, created_at) VALUES (?, ?, ?, ?, ?)",
                     (user_id, amount, method, details, datetime.now().isoformat()))
        conn.commit()
    await update.message.reply_text(f"✅ Withdraw request for {amount} coins submitted. Pending approval.")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with get_db() as conn:
        top_ref = conn.execute("SELECT user_id, username, first_name, referral_count FROM users ORDER BY referral_count DESC LIMIT 50").fetchall()
        top_earn = conn.execute("SELECT user_id, username, first_name, balance FROM users ORDER BY balance DESC LIMIT 50").fetchall()
    msg = "🏆 <b>Leaderboard</b> (Top 50)\n\n<b>👥 Referral Kings:</b>\n"
    for i, u in enumerate(top_ref, 1):
        name = u['first_name'] or u['username'] or str(u['user_id'])
        msg += f"{i}. {name} - {u['referral_count']} refs\n"
    msg += "\n<b>💰 Top Earners:</b>\n"
    for i, u in enumerate(top_earn, 1):
        name = u['first_name'] or u['username'] or str(u['user_id'])
        msg += f"{i}. {name} - {u['balance']:.2f} coins\n"
    await update.message.reply_html(msg)

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return
    msg = (f"👤 <b>Profile</b>\n"
           f"🆔 ID: {row['user_id']}\n"
           f"👤 Username: @{row['username'] or 'N/A'}\n"
           f"📅 Joined: {row['join_date'][:10]}\n"
           f"👥 Referrals: {row['referral_count']}\n"
           f"💰 Balance: {row['balance']:.2f}\n"
           f"🏆 Earned: {row['total_earned']:.2f}\n"
           f"📋 Tasks: {row['completed_tasks']}")
    await update.message.reply_html(msg)

async def statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return
    msg = (f"📊 <b>Statistics</b>\n"
           f"Total Earned: {row['total_earned']:.2f}\n"
           f"Referrals: {row['referral_count']}\n"
           f"Tasks Done: {row['completed_tasks']}\n"
           f"Balance: {row['balance']:.2f}")
    await update.message.reply_html(msg)

async def help_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = ("ℹ️ <b>Help</b>\n"
           "Invite friends using your referral link.\n"
           "Complete tasks, claim daily bonus.\n"
           "Withdraw when you reach minimum.\n"
           "Top 50 referrers will get special rewards!")
    await update.message.reply_html(msg)

# -------------------- ফ্লাস্ক অ্যাডমিন প্যানেল --------------------
app = Flask(__name__)
app.secret_key = app_secret

base_template = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }} - Global FIFA Rewards</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background-color: #0d1b2a; color: #e0e0e0; }
        .sidebar { background-color: #1b2838; min-height: 100vh; padding-top: 20px; }
        .sidebar a { color: #ccc; padding: 10px 20px; display: block; text-decoration: none; }
        .sidebar a:hover, .sidebar a.active { background-color: #2a3b4c; color: white; }
        .card { background-color: #1b2838; border: none; }
        .table-dark { --bs-table-bg: #1b2838; }
    </style>
</head>
<body>
<div class="container-fluid">
    <div class="row">
        <nav class="col-md-2 sidebar d-none d-md-block">
            <h4 class="text-center text-warning mb-4">⚽ FIFA Admin</h4>
            <a href="{{ url_for('admin_dashboard') }}" class="{{ 'active' if request.endpoint == 'admin_dashboard' }}"><i class="bi bi-speedometer2"></i> Dashboard</a>
            <a href="{{ url_for('admin_users') }}" class="{{ 'active' if request.endpoint == 'admin_users' }}"><i class="bi bi-people"></i> Users</a>
            <a href="{{ url_for('admin_tasks') }}" class="{{ 'active' if request.endpoint == 'admin_tasks' }}"><i class="bi bi-list-check"></i> Tasks</a>
            <a href="{{ url_for('admin_channels') }}" class="{{ 'active' if request.endpoint == 'admin_channels' }}"><i class="bi bi-broadcast"></i> Channels</a>
            <a href="{{ url_for('admin_withdrawals') }}" class="{{ 'active' if request.endpoint == 'admin_withdrawals' }}"><i class="bi bi-wallet2"></i> Withdrawals</a>
            <a href="{{ url_for('admin_broadcast') }}" class="{{ 'active' if request.endpoint == 'admin_broadcast' }}"><i class="bi bi-megaphone"></i> Broadcast</a>
            <a href="{{ url_for('admin_settings') }}" class="{{ 'active' if request.endpoint == 'admin_settings' }}"><i class="bi bi-gear"></i> Settings</a>
            <a href="{{ url_for('admin_logout') }}"><i class="bi bi-box-arrow-right"></i> Logout</a>
        </nav>
        <main class="col-md-10 ms-sm-auto px-4">
            <div class="pt-3 pb-2 mb-3 border-bottom"><h2>{{ title }}</h2></div>
            {{ content | safe }}
        </main>
    </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
'''

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        with get_db() as conn:
            admin = conn.execute("SELECT * FROM admin WHERE username = ?", (username,)).fetchone()
        if admin and check_password_hash(admin['password'], password):
            session['admin_logged_in'] = True
            session['admin_username'] = username
            return redirect(url_for('admin_dashboard'))
        return '<div class="container mt-5"><div class="alert alert-danger">Invalid credentials</div></div>'
    return '''
    <!DOCTYPE html><html><head><title>Admin Login</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>body{background:#0d1b2a;color:white;}.card{background:#1b2838;}</style></head>
    <body><div class="container mt-5"><div class="row justify-content-center"><div class="col-md-4">
    <div class="card p-4"><h4 class="text-center text-warning">⚽ FIFA Admin</h4>
    <form method="post"><div class="mb-3"><input class="form-control" name="username" placeholder="Username" required></div>
    <div class="mb-3"><input class="form-control" type="password" name="password" placeholder="Password" required></div>
    <button class="btn btn-warning w-100">Login</button></form></div></div></div></div></body></html>'''

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    stats = {}
    with get_db() as conn:
        stats['total_users'] = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()['cnt']
        stats['total_referrals'] = conn.execute("SELECT COUNT(*) as cnt FROM referrals").fetchone()['cnt']
        stats['total_tasks'] = conn.execute("SELECT COUNT(*) as cnt FROM task_completions").fetchone()['cnt']
        stats['total_withdraw'] = conn.execute("SELECT COUNT(*) as cnt FROM withdraw_requests").fetchone()['cnt']
        stats['total_rewards'] = conn.execute("SELECT SUM(total_earned) as s FROM users").fetchone()['s'] or 0
        today = datetime.now().strftime('%Y-%m-%d')
        stats['active_today'] = conn.execute("SELECT COUNT(*) as cnt FROM users WHERE join_date LIKE ?", (f'{today}%',)).fetchone()['cnt']

    content = f'''
    <div class="row g-4">
        <div class="col-md-3"><div class="card p-3"><h6>Total Users</h6><h3>{stats['total_users']}</h3></div></div>
        <div class="col-md-3"><div class="card p-3"><h6>Total Referrals</h6><h3>{stats['total_referrals']}</h3></div></div>
        <div class="col-md-3"><div class="card p-3"><h6>Tasks Completed</h6><h3>{stats['total_tasks']}</h3></div></div>
        <div class="col-md-3"><div class="card p-3"><h6>Withdraw Requests</h6><h3>{stats['total_withdraw']}</h3></div></div>
    </div>
    <div class="row mt-4">
        <div class="col-md-6"><canvas id="chartDailyUsers"></canvas></div>
        <div class="col-md-6"><canvas id="chartReferrals"></canvas></div>
    </div>
    <script>
    async function loadCharts(){{
        const du = await fetch('/admin/api/daily_users').then(r=>r.json());
        const rg = await fetch('/admin/api/referral_growth').then(r=>r.json());
        new Chart(document.getElementById('chartDailyUsers'), {{type:'line',data:{{labels:du.labels,datasets:[{{label:'New Users',data:du.values,borderColor:'#ffc107'}}]}}}});
        new Chart(document.getElementById('chartReferrals'), {{type:'line',data:{{labels:rg.labels,datasets:[{{label:'Referrals',data:rg.values,borderColor:'#28a745'}}]}}}});
    }} loadCharts();
    </script>
    '''
    return render_template_string(base_template, title="Dashboard", content=content)

@app.route('/admin/api/daily_users')
@admin_required
def api_daily_users():
    with get_db() as conn:
        rows = conn.execute("SELECT DATE(join_date) as date, COUNT(*) as cnt FROM users GROUP BY DATE(join_date) ORDER BY date DESC LIMIT 30").fetchall()
    return jsonify(labels=[r['date'] for r in reversed(rows)], values=[r['cnt'] for r in reversed(rows)])

@app.route('/admin/api/referral_growth')
@admin_required
def api_referral_growth():
    with get_db() as conn:
        rows = conn.execute("SELECT DATE(created_at) as date, COUNT(*) as cnt FROM referrals GROUP BY DATE(created_at) ORDER BY date DESC LIMIT 30").fetchall()
    return jsonify(labels=[r['date'] for r in reversed(rows)], values=[r['cnt'] for r in reversed(rows)])

@app.route('/admin/users')
@admin_required
def admin_users():
    search = request.args.get('search','')
    with get_db() as conn:
        if search:
            users = conn.execute("SELECT * FROM users WHERE user_id LIKE ? OR username LIKE ? OR first_name LIKE ?",(f'%{search}%',)*3).fetchall()
        else:
            users = conn.execute("SELECT * FROM users").fetchall()
    rows_html = ''
    for u in users:
        block_btn = 'Unblock' if u['blocked'] else 'Block'
        rows_html += f'''<tr>
            <td>{u['user_id']}</td><td>{u['first_name'] or u['username']}</td><td>{u['balance']}</td><td>{u['referral_count']}</td>
            <td>{'Yes' if u['blocked'] else 'No'}</td>
            <td>
                <a href="{url_for('admin_user_detail', user_id=u['user_id'])}" class="btn btn-sm btn-info">View</a>
                <a href="{url_for('admin_toggle_block', user_id=u['user_id'])}" class="btn btn-sm btn-warning">{block_btn}</a>
            </td></tr>'''
    content = f'''
    <form class="row g-2 mb-3"><div class="col-auto"><input name="search" class="form-control" placeholder="Search"></div><div class="col-auto"><button class="btn btn-warning">Search</button></div></form>
    <table class="table table-dark"><thead><tr><th>ID</th><th>Name</th><th>Balance</th><th>Refs</th><th>Blocked</th><th>Actions</th></tr></thead><tbody>{rows_html}</tbody></table>
    '''
    return render_template_string(base_template, title="Users", content=content)

@app.route('/admin/user/<int:user_id>')
@admin_required
def admin_user_detail(user_id):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        refs = conn.execute("SELECT r.*, u.first_name, u.username FROM referrals r JOIN users u ON r.referred_id = u.user_id WHERE r.referrer_id = ?", (user_id,)).fetchall()
        withdraws = conn.execute("SELECT * FROM withdraw_requests WHERE user_id = ?", (user_id,)).fetchall()
    refs_html = ''
    for r in refs:
        refs_html += f'<tr><td>{r["first_name"] or r["username"]} ({r["referred_id"]})</td><td>{r["created_at"][:10]}</td></tr>'
    wd_html = ''
    for w in withdraws:
        wd_html += f'<tr><td>{w["id"]}</td><td>{w["amount"]}</td><td>{w["payment_method"]}</td><td>{w["status"]}</td></tr>'
    content = f'''
    <div class="card p-3 mb-4"><h4>{user['first_name'] or user['username']}</h4><p>Balance: {user['balance']} | Refs: {user['referral_count']}</p>
    <form method="post" action="{url_for('admin_edit_balance')}"><input type="hidden" name="user_id" value="{user['user_id']}">
    <input name="balance" class="form-control" value="{user['balance']}"> <button class="btn btn-sm btn-success mt-1">Update</button></form></div>
    <h5>Referrals</h5><table class="table table-dark"><tr><th>Referred</th><th>Date</th></tr>{refs_html}</table>
    <h5>Withdrawals</h5><table class="table table-dark"><tr><th>ID</th><th>Amount</th><th>Method</th><th>Status</th></tr>{wd_html}</table>
    '''
    return render_template_string(base_template, title=f"User {user_id}", content=content)

@app.route('/admin/edit_balance', methods=['POST'])
@admin_required
def admin_edit_balance():
    with get_db() as conn:
        conn.execute("UPDATE users SET balance = ? WHERE user_id = ?", (request.form['balance'], request.form['user_id']))
        conn.commit()
    return redirect(url_for('admin_user_detail', user_id=request.form['user_id']))

@app.route('/admin/toggle_block/<int:user_id>')
@admin_required
def admin_toggle_block(user_id):
    with get_db() as conn:
        u = conn.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,)).fetchone()
        conn.execute("UPDATE users SET blocked = ? WHERE user_id = ?", (0 if u['blocked'] else 1, user_id))
        conn.commit()
    return redirect(url_for('admin_users'))

@app.route('/admin/tasks')
@admin_required
def admin_tasks():
    with get_db() as conn:
        tasks = conn.execute("SELECT * FROM tasks").fetchall()
    rows = ''
    for t in tasks:
        rows += f'''<tr><td>{t['id']}</td><td>{t['title']}</td><td>{t['reward']}</td><td>{'Yes' if t['enabled'] else 'No'}</td>
            <td><a href="{url_for('admin_task_edit', task_id=t['id'])}" class="btn btn-sm btn-info">Edit</a></td></tr>'''
    content = f'''<a href="{url_for('admin_task_edit')}" class="btn btn-warning mb-3">Add Task</a>
        <table class="table table-dark"><tr><th>ID</th><th>Title</th><th>Reward</th><th>Enabled</th><th>Actions</th></tr>{rows}</table>'''
    return render_template_string(base_template, title="Tasks", content=content)

@app.route('/admin/task/edit', defaults={'task_id': None})
@app.route('/admin/task/edit/<int:task_id>')
@admin_required
def admin_task_edit(task_id):
    task = None
    if task_id:
        with get_db() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    content = f'''
    <form method="post" action="{url_for('admin_task_save')}">
        {"<input type='hidden' name='task_id' value='"+str(task['id'])+"'>" if task else ""}
        <div class="mb-3"><label>Title</label><input class="form-control" name="title" value="{task['title'] if task else ''}" required></div>
        <div class="mb-3"><label>Description</label><textarea class="form-control" name="description">{task['description'] if task else ''}</textarea></div>
        <div class="mb-3"><label>Reward</label><input class="form-control" name="reward" type="number" step="0.01" value="{task['reward'] if task else ''}" required></div>
        <div class="mb-3"><label>Link</label><input class="form-control" name="link" value="{task['link'] if task else ''}"></div>
        <div class="mb-3"><label>Enabled</label><select class="form-control" name="enabled">
            <option value="1" {"selected" if task and task['enabled'] else ""}>Yes</option>
            <option value="0" {"selected" if task and not task['enabled'] else ""}>No</option>
        </select></div>
        <button class="btn btn-warning">Save</button>
    </form>'''
    return render_template_string(base_template, title="Edit Task" if task else "New Task", content=content)

@app.route('/admin/task/save', methods=['POST'])
@admin_required
def admin_task_save():
    task_id = request.form.get('task_id')
    with get_db() as conn:
        if task_id:
            conn.execute("UPDATE tasks SET title=?,description=?,reward=?,link=?,enabled=? WHERE id=?",
                         (request.form['title'], request.form['description'], request.form['reward'], request.form['link'], request.form['enabled'], task_id))
        else:
            conn.execute("INSERT INTO tasks (title,description,reward,link,enabled) VALUES (?,?,?,?,?)",
                         (request.form['title'], request.form['description'], request.form['reward'], request.form['link'], request.form['enabled']))
        conn.commit()
    return redirect(url_for('admin_tasks'))

@app.route('/admin/channels')
@admin_required
def admin_channels():
    with get_db() as conn:
        channels = conn.execute("SELECT * FROM channels").fetchall()
    rows = ''
    for ch in channels:
        rows += f'''<tr><td>{ch['id']}</td><td>{ch['channel_username']}</td><td>{ch['channel_link']}</td><td>{'Yes' if ch['enabled'] else 'No'}</td>
            <td><a href="{url_for('admin_channel_edit', channel_id=ch['id'])}" class="btn btn-sm btn-info">Edit</a></td></tr>'''
    content = f'''<a href="{url_for('admin_channel_edit')}" class="btn btn-warning mb-3">Add Channel</a>
        <table class="table table-dark"><tr><th>ID</th><th>Username</th><th>Link</th><th>Enabled</th><th>Actions</th></tr>{rows}</table>'''
    return render_template_string(base_template, title="Channels", content=content)

@app.route('/admin/channel/edit', defaults={'channel_id': None})
@app.route('/admin/channel/edit/<int:channel_id>')
@admin_required
def admin_channel_edit(channel_id):
    channel = None
    if channel_id:
        with get_db() as conn:
            channel = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    content = f'''
    <form method="post" action="{url_for('admin_channel_save')}">
        {"<input type='hidden' name='channel_id' value='"+str(channel['id'])+"'>" if channel else ""}
        <div class="mb-3"><label>Channel Username (@...)</label><input class="form-control" name="channel_username" value="{channel['channel_username'] if channel else ''}" required></div>
        <div class="mb-3"><label>Channel Link</label><input class="form-control" name="channel_link" value="{channel['channel_link'] if channel else ''}" required></div>
        <div class="mb-3"><label>Enabled</label><select class="form-control" name="enabled">
            <option value="1" {"selected" if channel and channel['enabled'] else ""}>Yes</option>
            <option value="0" {"selected" if channel and not channel['enabled'] else ""}>No</option>
        </select></div>
        <button class="btn btn-warning">Save</button>
    </form>'''
    return render_template_string(base_template, title="Edit Channel" if channel else "New Channel", content=content)

@app.route('/admin/channel/save', methods=['POST'])
@admin_required
def admin_channel_save():
    channel_id = request.form.get('channel_id')
    with get_db() as conn:
        if channel_id:
            conn.execute("UPDATE channels SET channel_username=?, channel_link=?, enabled=? WHERE id=?",
                         (request.form['channel_username'], request.form['channel_link'], request.form['enabled'], channel_id))
        else:
            conn.execute("INSERT INTO channels (channel_username, channel_link, enabled) VALUES (?,?,?)",
                         (request.form['channel_username'], request.form['channel_link'], request.form['enabled']))
        conn.commit()
    return redirect(url_for('admin_channels'))

@app.route('/admin/withdrawals')
@admin_required
def admin_withdrawals():
    with get_db() as conn:
        requests = conn.execute("SELECT w.*, u.first_name, u.username FROM withdraw_requests w JOIN users u ON w.user_id = u.user_id ORDER BY w.created_at DESC").fetchall()
    rows = ''
    for r in requests:
        actions = ''
        if r['status'] == 'pending':
            actions = f'''<a href="{url_for('admin_withdraw_action', req_id=r['id'], action='approve')}" class="btn btn-sm btn-success">Approve</a>
                        <a href="{url_for('admin_withdraw_action', req_id=r['id'], action='reject')}" class="btn btn-sm btn-danger">Reject</a>'''
        rows += f'''<tr><td>{r['id']}</td><td>{r['first_name'] or r['username']} ({r['user_id']})</td><td>{r['amount']}</td><td>{r['payment_method']}</td><td>{r['status']}</td><td>{actions}</td></tr>'''
    content = f'<table class="table table-dark"><tr><th>ID</th><th>User</th><th>Amount</th><th>Method</th><th>Status</th><th>Actions</th></tr>{rows}</table>'
    return render_template_string(base_template, title="Withdraw Requests", content=content)

@app.route('/admin/withdraw/<int:req_id>/<action>')
@admin_required
def admin_withdraw_action(req_id, action):
    with get_db() as conn:
        req = conn.execute("SELECT * FROM withdraw_requests WHERE id = ?", (req_id,)).fetchone()
        if req and req['status'] == 'pending':
            if action == 'approve':
                conn.execute("UPDATE withdraw_requests SET status='approved' WHERE id=?", (req_id,))
            elif action == 'reject':
                conn.execute("UPDATE withdraw_requests SET status='rejected' WHERE id=?", (req_id,))
                conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (req['amount'], req['user_id']))
            conn.commit()
    return redirect(url_for('admin_withdrawals'))

@app.route('/admin/broadcast', methods=['GET', 'POST'])
@admin_required
def admin_broadcast():
    sent = request.args.get('sent', '')
    if request.method == 'POST':
        message = request.form['message']
        with get_db() as conn:
            users = conn.execute("SELECT user_id FROM users WHERE blocked = 0").fetchall()
        user_ids = [u['user_id'] for u in users]
        if user_ids and message and bot_loop:
            asyncio.run_coroutine_threadsafe(broadcast_message(user_ids, message), bot_loop)
            return redirect(url_for('admin_broadcast', sent=len(user_ids)))
    alert = f'<div class="alert alert-success mt-3">Broadcast sent to {sent} users.</div>' if sent else ''
    content = f'''<form method="post"><textarea class="form-control mb-3" name="message" rows="5" placeholder="Message to all users"></textarea>
        <button class="btn btn-warning">Send Broadcast</button></form>{alert}'''
    return render_template_string(base_template, title="Broadcast", content=content)

async def broadcast_message(user_ids, text):
    for uid in user_ids:
        try:
            await application.bot.send_message(chat_id=uid, text=text)
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Broadcast failed for {uid}: {e}")

@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    if request.method == 'POST':
        for key in ['referral_reward', 'daily_bonus', 'min_withdraw', 'withdraw_methods', 'welcome_message']:
            val = request.form.get(key)
            if val is not None:
                set_setting(key, val)
        new_token = request.form.get('bot_token')
        if new_token:
            set_setting('bot_token', new_token)
            # টোকেন পরিবর্তন হলে সার্ভার রিস্টার্ট হবে না, শুধু ডাটাবেসে সেভ হবে।
            # সম্পূর্ণ রিস্টার্ট চাইলে নিচের লাইনটি ব্যবহার করবে:
            # os.execv(sys.executable, ['python'] + sys.argv)
        return redirect(url_for('admin_settings'))
    settings = {}
    for key in ['referral_reward','daily_bonus','min_withdraw','withdraw_methods','welcome_message']:
        settings[key] = get_setting(key) or ''
    content = f'''
    <form method="post">
        <div class="mb-3"><label>Referral Reward</label><input class="form-control" name="referral_reward" value="{settings['referral_reward']}"></div>
        <div class="mb-3"><label>Daily Bonus</label><input class="form-control" name="daily_bonus" value="{settings['daily_bonus']}"></div>
        <div class="mb-3"><label>Minimum Withdraw</label><input class="form-control" name="min_withdraw" value="{settings['min_withdraw']}"></div>
        <div class="mb-3"><label>Withdraw Methods</label><input class="form-control" name="withdraw_methods" value="{settings['withdraw_methods']}"></div>
        <div class="mb-3"><label>Welcome Message</label><textarea class="form-control" name="welcome_message" rows="8">{settings['welcome_message']}</textarea></div>
        <button class="btn btn-warning">Save Settings</button>
    </form>'''
    return render_template_string(base_template, title="Settings", content=content)

# -------------------- প্রোডাকশন WSGI সার্ভার (Waitress) --------------------
def run_flask():
    from waitress import serve
    logger.info("Starting Waitress production server on port 5000...")
    serve(app, host='0.0.0.0', port=5000)

if __name__ == '__main__':
    init_db()
    db_token = get_setting('bot_token') or BOT_TOKEN
    # ফ্লাস্ক আলাদা থ্রেডে চালু
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # টেলিগ্রাম বট চালু
    application = Application.builder().token(db_token).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('withdraw', process_withdraw_command))
    application.add_handler(CallbackQueryHandler(check_join_callback, pattern='check_join'))
    application.add_handler(CallbackQueryHandler(complete_task_callback, pattern='complete_task_'))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))

    async def post_init(app: Application):
        global BOT_USERNAME, bot_loop
        bot = app.bot
        me = await bot.get_me()
        BOT_USERNAME = me.username
        bot_loop = asyncio.get_running_loop()
        logger.info(f"Global FIFA Rewards Bot @{BOT_USERNAME} started.")

    application.post_init = post_init
    logger.info("Starting Telegram bot polling...")
    application.run_polling()