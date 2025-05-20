import discord
from discord import app_commands
from discord.ext import commands
import os
import google.generativeai as genai
from dotenv import load_dotenv
import json
import math
import datetime
from functools import wraps
from discord import Interaction
from discord.ui import View, Button
import random
from discord import app_commands, Embed
from flask import Flask
import psycopg2
from psycopg2.extras import RealDictCursor
import threading
import logging



DATABASE_URL = os.environ['DATABASE_URL']

USER_DATA_FILE = "users.json"
ALLOWED_CHANNEL_ID = 1374299836538425344
MAX_BET = 255

# Flaskサーバー（Render無料プラン維持）
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

    
# ユーザーデータの読み込み・保存
def load_user_data():
    if not os.path.exists(USER_DATA_FILE):
        return {}
    with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_user_data(data):
    with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user_profile(user_id):
    with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            result = cur.fetchone()
            if result:
                return result
            else:
                # 新規作成
                cur.execute("""
                    INSERT INTO users (user_id, money, affection, streak, titles, gamble_count)
                    VALUES (%s, 500, 0, 0, ARRAY[]::TEXT[], 0)
                    RETURNING *;
                """, (user_id,))
                return cur.fetchone()


def update_user_profile(user_id, profile):
    with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users SET money=%s, affection=%s, streak=%s, last_daily=%s, titles=%s, gamble_count=%s
                WHERE user_id = %s
            """, (
                profile["money"], profile["affection"], profile["streak"],
                profile.get("last_daily"), profile["titles"], profile["gamble_count"],
                user_id
            ))
            conn.commit()


def check_titles(user_id, profile):
    if "titles" not in profile:
        profile["titles"] = []
    new_titles = []
    conditions = [
        ("常連", lambda p: p.get("streak",0) >= 7),
        ("もはや家", lambda p: p.get("streak",0) >= 30),
        ("富豪", lambda p: p.get("money",0) >= 100000),
        ("国家予算並みの資産", lambda p: p.get("money",0) >= 10000000),
        ("ビギナーズラック", lambda p: p.get("gamble_count",0) >= 20),
        ("中堅どころ", lambda p: p.get("gamble_count",0) >= 200),
        ("賭ケグルイ", lambda p: p.get("gamble_count",0) >= 2000),
        ("VIP待遇", lambda p: p.get("affection",0) >= 75 and p.get("gamble_count",0) >= 200)
    ]
    for title, condition in conditions:
        if title not in profile["titles"] and condition(profile):
            profile["titles"].append(title)
            new_titles.append(title)

    if new_titles:
        update_user_profile(user_id, profile)  # 忘れずに保存

    return new_titles


def has_vip(profile):
    return "VIP待遇" in profile["titles"]

# .env読み込み
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Gemini設定
genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel('models/gemini-1.5-flash-latest')
with open("character.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# チャンネル制限デコレーター
def channel_only(func):
    @wraps(func)
    async def wrapper(interaction: Interaction, *args, **kwargs):
        if interaction.channel_id != ALLOWED_CHANNEL_ID:
            await interaction.response.send_message("このコマンドは指定されたチャンネルでのみ使用できます。", ephemeral=True)
            return
        return await func(interaction, *args, **kwargs)
    return wrapper


# /daily
@bot.tree.command(name="daily", description="1日1回のログインボーナスを受け取ろう！")
@channel_only
async def daily(interaction: discord.Interaction):
    await interaction.response.defer()  # 遅延応答
    profile = get_user_profile(interaction.user.id)
    today = datetime.date.today()

    last_daily = profile.get("last_daily")
    last_date = None

    if last_daily:
        # 文字列型 or datetime型のどちらでも対応
        if isinstance(last_daily, str):
            try:
                last_date = datetime.date.fromisoformat(last_daily)
            except ValueError:
                pass
        elif isinstance(last_daily, datetime.datetime):
            last_date = last_daily.date()
        elif isinstance(last_daily, datetime.date):
            last_date = last_daily

    # ログ出力（Renderダッシュボードで確認可能）
    logging.info(f"[daily] today: {today}, last_daily: {last_daily}, last_date: {last_date}")

    if last_date == today:
        await interaction.followup.send("今日はもう受け取り済みです！")
        return

    # streak 判定
    if last_date == today - datetime.timedelta(days=1):
        profile["streak"] += 1
    else:
        profile["streak"] = 1

    bonus = 200
    msg = f"本日のログインボーナス：200グラント（{profile['streak']}日連続）"

    if profile["streak"] % 7 == 0:
        bonus += 1500
        msg += "\n🎁 1週間連続ログインボーナス：+1500グラント！"
    elif profile["streak"] % 5 == 0:
        bonus += 100
        msg += "\n🎁 5日連続ログインボーナス：+100グラント！"

    profile["money"] += bonus
    profile["last_daily"] = today.isoformat()
    profile["total_logins"] += 1

    update_user_profile(interaction.user.id, profile)
    titles = check_titles(interaction.user.id, profile)
    if titles:
        msg += "\n" + "\n".join([f"🏅 新しい称号獲得：{t}" for t in titles])

    await interaction.followup.send(msg)




# /status
@bot.tree.command(name="status", description="現在の状態を確認します")
@channel_only
async def status(interaction: discord.Interaction):
    profile = get_user_profile(interaction.user.id)
    await interaction.response.send_message(
        f"👤 ニックネーム：{profile['nickname']}\n"
        f"💰 所持金：{profile['money']}グラント\n"
        f"📆 連続ログイン：{profile['streak']}日\n"
        f"🎰 ギャンブル回数：{profile['gamble_count']}\n"
        f"🏅 称号：{', '.join(profile['titles']) if profile['titles'] else 'なし'}"
    )

# /achievement
@bot.tree.command(name="achievement", description="称号一覧と獲得状況を表示します")
@channel_only
async def achievement(interaction: discord.Interaction):
    profile = get_user_profile(interaction.user.id)
    achievements = [
        ("常連", "7日連続ログイン"),
        ("もはや家", "30日連続ログイン"),
        ("富豪", "100,000グラント所持"),
        ("国家予算並みの資産", "10,000,000グラント所持"),
        ("ビギナーズラック", "ギャンブル回数20回"),
        ("中堅どころ", "ギャンブル回数100回"),
        ("賭ケグルイ", "ギャンブル回数1000回"),
        ("VIP待遇", "???")
    ]
    result = []
    for title, condition in achievements:
        if title in profile["titles"]:
            result.append(f"🏅 {title}：{condition}")
        else:
            result.append(f"❌ 未所持：{condition if title != 'VIP待遇' else '???'}")
    await interaction.response.send_message("\n".join(result))

# /coinflip
@bot.tree.command(name="coinflip", description="表か裏を当ててみよう！")
@app_commands.describe(guess="表か裏、または0(表)・1(裏)")
@channel_only
async def coinflip(interaction: discord.Interaction, guess: str):
    await interaction.response.defer()  # これを追加

    result = random.choice(["表", "裏"])
    profile = get_user_profile(interaction.user.id)
    guess = guess.strip()
    if guess == "0":
        guess = "表"
    elif guess == "1":
        guess = "裏"
    if guess not in ["表", "裏"]:
        await interaction.followup.send("表・裏、または0・1で入力してください。")
        return
    profile["gamble_count"] += 1
    if guess == result:
        profile["money"] += 50
        msg = f"当たり！{result}でした。+50グラント"
    else:
        profile["money"] -= 50
        msg = f"はずれ……{result}でした。-50グラント"
    update_user_profile(interaction.user.id, profile)
    titles = check_titles(interaction.user.id, profile)
    if titles:
        msg += "\n" + "\n".join([f"🏅 新しい称号獲得：{t}" for t in titles])
    await interaction.followup.send(msg)


# /russianRoulette
roulette_sessions = {}
class RussianRouletteView(View):
    def __init__(self, user_id: int, bet: int):
        super().__init__(timeout=30)
        self.user_id = user_id
        self.bet = bet
        self.survival_rewards = 0  # 撃つたびに貯める報酬

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("これはあなたのゲームではありません。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="撃つ", style=discord.ButtonStyle.danger, custom_id="shoot")
    async def shoot(self, interaction: discord.Interaction, button: Button):
        user_id = interaction.user.id
        session = roulette_sessions.get(user_id)
        profile = get_user_profile(user_id)

        if not session:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return

        session["shots"] += 1

        # 生存報酬を加算（掛金の30%）
        reward = self.bet // 10*3
        self.survival_rewards += reward

        if session["shots"] == session["chamber"]:
            # 死亡
            roulette_sessions.pop(user_id)
            profile["money"] -= self.bet
            update_user_profile(user_id, profile)
            await interaction.response.edit_message(
                content=f"💥 バン！死亡しました… 掛金 {self.bet}グラントを失いました。報酬は没収です。",
                view=None
            )
        elif session["shots"] >= 6:
            # 生還（全報酬支払い）
            roulette_sessions.pop(user_id)
            profile["money"] += self.survival_rewards
            profile["gamble_count"] += 1
            update_user_profile(user_id, profile)
            await interaction.response.edit_message(
                content=(
                    f"🎉 生還しました！\n"
                    f"撃った回数: {session['shots']} 発\n"
                    f"累計報酬: {self.survival_rewards} グラントを獲得！"
                ),
                view=None
            )
        else:
            remaining = 6 - session["shots"]
            await interaction.response.edit_message(
                content=(
                    f"カチッ……助かりました！（残り {remaining} 発）\n"
                    f"今回の報酬 +{reward}グラントです。\n"
                    f"次はどうしますか？"
                ),
                view=RussianRouletteView(user_id, self.bet)
            )

    @discord.ui.button(label="やめる", style=discord.ButtonStyle.secondary, custom_id="quit")
    async def quit(self, interaction: discord.Interaction, button: Button):
        roulette_sessions.pop(self.user_id, None)
        await interaction.response.edit_message(content=f"ゲームを中断しました。累計報酬 {self.survival_rewards} グラントは獲得できません。", view=None)


@bot.tree.command(name="russianroulette", description="ロシアンルーレットに挑戦（掛金自由）")
@channel_only
@app_commands.describe(bet="賭ける金額（1〜255）")
async def russianroulette(interaction: discord.Interaction, bet: int):
    user_id = interaction.user.id
    profile = get_user_profile(user_id)

    if user_id in roulette_sessions:
        await interaction.response.send_message("現在進行中のロシアンルーレットがあります。", ephemeral=True)
        return
    if bet < 1 or bet > 255:
        await interaction.response.send_message("賭け金は1〜255の範囲で指定してください。", ephemeral=True)
        return
    if profile["money"] < bet:
        await interaction.response.send_message("所持金が足りません。", ephemeral=True)
        return

    roulette_sessions[user_id] = {
        "chamber": random.randint(1, 6),
        "shots": 0
    }

    await interaction.response.send_message(
        f"🔫 ロシアンルーレット開始！\n賭け金：{bet}グラント\n1発ずつ撃っていきます…どうする？",
        view=RussianRouletteView(user_id, bet)
    )


# /roulette
@bot.tree.command(name="roulette", description="赤・黒・数字のいずれかにベット")
@app_commands.describe(bet="賭けグラント (最大255)", choice="赤・黒 または 0〜36 の数字")
async def roulette(interaction: discord.Interaction, bet: int, choice: str):
    await interaction.response.defer()  # 応答保留
    profile = get_user_profile(interaction.user.id)
    if bet <= 0 or (not has_vip(profile) and bet > MAX_BET):
        await interaction.followup.send(f"賭け金は1〜{MAX_BET}グラントまでです。")
        return
    if profile["money"] < bet:
        await interaction.followup.send("所持金が足りません。")
        return

    result = random.randint(0, 36)
    red = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
    black = {2,4,6,8,10,11,13,15,17,20,22,24,26,28,29,31,33,35}
    outcome = "赤" if result in red else "黒" if result in black else "緑"

    profile["gamble_count"] += 1
    payout = 0

    if choice == outcome:
        payout = bet * 2
    elif choice.isdigit() and int(choice) == result:
        payout = bet * 35

    if payout > 0:
        profile["money"] += payout
        msg = f"🎯 出目：{result}（{outcome}）！勝利 +{payout}グラント"
    else:
        profile["money"] -= bet
        msg = f"💥 出目：{result}（{outcome}）…はずれ -{bet}グラント"

    update_user_profile(interaction.user.id, profile)
    titles = check_titles(interaction.user.id, profile)
    if titles:
        msg += "\n" + "\n".join([f"🏅 新しい称号獲得：{t}" for t in titles])
    await interaction.followup.send(msg)

# /blackjack
@bot.tree.command(name="blackjack", description="ブラックジャックをプレイ")
@app_commands.describe(bet="賭けグラント (最大255)")
async def blackjack(interaction: discord.Interaction, bet: int):
    await interaction.response.defer()
    profile = get_user_profile(interaction.user.id)
    if bet <= 0 or (not has_vip(profile) and bet > MAX_BET):
        await interaction.followup.send(f"賭け金は1〜{MAX_BET}グラントまでです。")
        return
    if profile["money"] < bet:
        await interaction.followup.send("所持金が足りません。")
        return
    # ここからブラックジャックゲームロジック開始

    # トランプのデッキ作成
    deck = [2,3,4,5,6,7,8,9,10,10,10,10,11] * 4  # 11はエースとして扱う
    random.shuffle(deck)

    player_cards = [deck.pop(), deck.pop()]
    dealer_cards = [deck.pop(), deck.pop()]

    def hand_value(cards):
        val = sum(cards)
        ace_count = cards.count(11)
        while val > 21 and ace_count > 0:
            val -= 10
            ace_count -= 1
        return val

    player_val = hand_value(player_cards)
    dealer_val = hand_value(dealer_cards)

    # プレイヤーにヒット or スタンドの選択肢を表示するView
    class BlackjackView(View):
        def __init__(self):
            super().__init__(timeout=60)
            self.stand = False

        async def end_game(self, interaction, player_val, dealer_val):
            # 結果判定と報酬計算
            user_id = interaction.user.id  # ここで取得
            if player_val > 21:
                # バースト
                profile["money"] -= bet
                result_msg = f"あなたの手札は {player_cards} （合計{player_val}）でバースト。負けです。- {bet}グラント"
            else:
                # ディーラーのターン
                while dealer_val < 17:
                    dealer_cards.append(deck.pop())
                    dealer_val = hand_value(dealer_cards)

                if dealer_val > 21 or player_val > dealer_val:
                    profile["money"] += bet
                    result_msg = (f"あなたの勝ち！あなた: {player_cards}（{player_val}） ディーラー: {dealer_cards}（{dealer_val}）\n"
                                  f"+{bet}グラント獲得！")
                elif player_val == dealer_val:
                    result_msg = (f"引き分けです。あなた: {player_cards}（{player_val}） ディーラー: {dealer_cards}（{dealer_val}）\n"
                                  f"賭け金は戻ります。")
                else:
                    profile["money"] -= bet
                    result_msg = (f"あなたの負け。あなた: {player_cards}（{player_val}） ディーラー: {dealer_cards}（{dealer_val}）\n"
                                  f"-{bet}グラント失います。")

            profile["gamble_count"] += 1
            update_user_profile(user_id, profile)
            titles = check_titles(user_id, profile)
            if titles:
                result_msg += "\n" + "\n".join([f"🏅 新しい称号獲得：{t}" for t in titles])

            await interaction.response.edit_message(content=result_msg, view=None)

        @discord.ui.button(label="ヒット", style=discord.ButtonStyle.primary)
        async def hit(self, interaction: discord.Interaction, button: Button):
            player_cards.append(deck.pop())
            val = hand_value(player_cards)
            if val > 21:
                await self.end_game(interaction, val, dealer_val)
                self.stop()
            else:
                await interaction.response.edit_message(content=f"手札: {player_cards}（合計{val}） ヒットかスタンドを選んでください。", view=self)

        @discord.ui.button(label="スタンド", style=discord.ButtonStyle.secondary)
        async def stand(self, interaction: discord.Interaction, button: Button):
            player_val_final = hand_value(player_cards)
            await self.end_game(interaction, player_val_final, dealer_val)
            self.stop()

    await interaction.followup.send(f"ブラックジャック開始！\nあなたの手札: {player_cards}（合計{player_val}）\nディーラーの見えているカード: [{dealer_cards[0]}, ?]\nヒットかスタンドを選んでください。", view=BlackjackView())


slot_emojis = ["🍒", "🍋", "🍉", "🍇", "⭐"]

@bot.tree.command(name="slot", description="スロットマシンで遊ぼう！")
@app_commands.describe(bet="掛金（1〜255の整数）")
async def slot(interaction: Interaction, bet: int):
    if bet < 1 or bet > 255:
        await interaction.response.send_message("掛金は1から255の間で指定してください。", ephemeral=True)
        return

    profile = get_user_profile(interaction.user.id)
    if profile["money"] < bet:
        await interaction.response.send_message("所持金が足りません。", ephemeral=True)
        return

    # 所持金から掛金を引く（先に払う形）
    profile["money"] -= bet

    # 3つの絵柄をランダム抽選
    result = [random.choice(slot_emojis) for _ in range(3)]

    # 揃い判定
    if result[0] == result[1] == result[2]:
        multiplier = 5
        msg = "🎉 3つ揃い！大当たりです！"
    elif result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
        multiplier = 2
        msg = "😊 2つ揃い！当たりです！"
    else:
        multiplier = 0
        msg = "😢 ハズレです。"

    win_amount = bet * multiplier
    profile["money"] += win_amount
    profile["gamble_count"] += 1
    update_user_profile(interaction.user.id, profile)

    embed = Embed(title="スロットマシンの結果", color=0x00ff00)
    embed.add_field(name="絵柄", value=" | ".join(result), inline=False)
    embed.add_field(name="結果", value=msg, inline=False)
    embed.add_field(name="掛金", value=f"{bet} グラント", inline=True)
    embed.add_field(name="獲得額", value=f"{win_amount} グラント", inline=True)
    embed.set_footer(text=f"現在の所持金: {profile['money']} グラント")

    await interaction.response.send_message(embed=embed)

if __name__ == "__main__":
    # Flaskサーバーを別スレッドで起動
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

# 起動処理
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Botログイン完了: {bot.user}")



bot.run(TOKEN)
