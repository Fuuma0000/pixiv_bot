import atexit
import os
import discord
from discord.ext import tasks
from discord import app_commands
from discord.ext import commands
import time
import sqlite3
from pixivpy3 import AppPixivAPI, PixivError
from itertools import chain
from dotenv import load_dotenv

load_dotenv()

# Discord botのトークン
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
# CHANNEL_ID = os.getenv("CHANNEL_ID")
_REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")
_REQUESTS_KWARGS = {}

intents = discord.Intents.default()
client = discord.Client(intents=discord.Intents.all())
tree = app_commands.CommandTree(client)

dbname = "PIXIV_DB.db"
conn = sqlite3.connect(dbname)
cur = conn.cursor()


# もしtableがなければ作成する
cur.execute(
    "CREATE TABLE IF NOT EXISTS pixiv_data ( id INTEGER PRIMARY KEY, data TEXT )"
)
conn.commit()


def save_new_bookmarks(cur, conn, new_bookmarks):
    # 最新のブックマーク情報をDBに保存する処理

    for id, data in new_bookmarks.items():
        # dataを文字列に変換
        data = str(data)
        cur.execute("UPDATE pixiv_data SET data=? WHERE id=?", (data, id))

    conn.commit()


# 辞書のユーザーのブックマーク情報を取得する関数
def get_user_bookmarks(aapi, old_bookmarks):
    # 前回のブックマークからidのリストを取得
    old_bookmarks_id_list = old_bookmarks.keys()

    # id, dataの辞書型に登録する
    result_id_data = {}

    for user_id in old_bookmarks_id_list:
        # ユーザーのブックマークページをスクレイピングしてブックマークされている作品IDのリストを取得する処理
        json_result = aapi.user_bookmarks_illust(user_id)
        artworks_array = []
        for i in range(10):
            result_id = json_result.illusts[i]
            artworks_array.append(result_id.id)

        result_id_data.update({user_id: artworks_array})

    return result_id_data


# 1人のユーザーのブックマーク情報を取得する関数
def get_new_bookmarks_for_id(aapi, id):
    # ユーザーのブックマークページをスクレイピングしてブックマークされている作品IDのリストを取得する処理
    json_result = aapi.user_bookmarks_illust(id)
    artworks_array = []
    for i in range(10):
        result_id = json_result.illusts[i]
        artworks_array.append(result_id.id)

    return artworks_array


# 1人のユーザのブックマーク情報をインサートする関数
def save_new_bookmarks_for_id(cur, conn, id, artworks_array):
    # atworks_arrayを文字列に変換
    artworks_array = str(artworks_array)

    # 最新のブックマーク情報をDBに保存する処理
    cur.execute("INSERT INTO pixiv_data (id, data) VALUES (?, ?)", (id, artworks_array))
    conn.commit()


# 前回のブックマーク情報を持つ変数（リレーションやDBに保存）
def get_old_bookmarks(cur):
    old_bookmarks = {}

    cur.execute("SELECT * FROM pixiv_data")
    rows = cur.fetchall()

    for row in rows:
        id_value, data_str = row
        id_value = int(id_value)

        # 文字列形式のデータをリストに変換
        data_list = eval(data_str)

        old_bookmarks[id_value] = data_list

    return old_bookmarks


def check_new_bookmarks(old_bookmarks, new_bookmarks):
    difference_bookmarks = []

    for id, new_bookmarks_data in new_bookmarks.items():
        old_bookmarks_data = old_bookmarks.get(id, [])  # デフォルト値を空リストに設定

        # 2つのブックマークリストを比較し、差分を取得する
        difference = list(set(new_bookmarks_data) - set(old_bookmarks_data))

        # 差分があればリストに追加する
        if difference:
            difference_bookmarks.append(difference)

    # 全てのリストを1つに結合して返す
    return list(chain.from_iterable(difference_bookmarks))


# 新しいブックマークをDiscordに通知する
async def notify_new_bookmarks(channel, new_bookmarks):
    for new_bookmark in new_bookmarks:
        await channel.send(f"https://www.pixiv.net/artworks/{new_bookmark}")


def close_db_connection():
    cur.close()
    conn.close()


@client.event
async def on_ready():
    await tree.sync()
    loop.start()


# idを引数にとって、そのidを表示するコマンド
@tree.command(name="join", description="pixivのIDを入力してね")
@commands.is_owner()
async def show_id(interaction: discord.Interaction, id: int):
    # app-api
    aapi = AppPixivAPI(**_REQUESTS_KWARGS)

    _e = None
    for _ in range(3):
        try:
            aapi.auth(refresh_token=_REFRESH_TOKEN)
            break
        except PixivError as e:
            _e = e
            time.sleep(10)
    else:  # failed 3 times
        raise _e

    # DBに保存されているか確認
    cur.execute("SELECT * FROM pixiv_data WHERE id=?", (id,))

    existing_entry = cur.fetchone()

    text = f""

    if not existing_entry:
        # 最新のブックマーク情報を取得
        artworks_array = get_new_bookmarks_for_id(aapi, id)

        # DBに保存する
        save_new_bookmarks_for_id(cur, conn, id, artworks_array)
        text = f"登録しました"
    else:
        text = f"登録済みです"
    await interaction.response.send_message(text)


@tree.command(name="leave", description="pixivのIDを入力してね")
@commands.is_owner()
async def delete_id(interaction: discord.Interaction, id: int):
    cur.execute("SELECT * FROM pixiv_data WHERE id=?", (id,))
    existing_entry = cur.fetchone()

    if not existing_entry:
        await interaction.response.send_message("登録されていません")
    else:
        cur.execute("DELETE FROM pixiv_data WHERE id=?", (id,))
        conn.commit()

    await interaction.response.send_message("削除しました")


# ユーザ数を表示するコマンド
@tree.command(name="count", description="登録ユーザ数を表示するよ")
@commands.is_owner()
async def show_count(interaction: discord.Interaction):
    cur.execute("SELECT COUNT(*) FROM pixiv_data")
    count = cur.fetchone()[0]
    await interaction.response.send_message(f"現在の登録ユーザ数は{count}人です")


# 60秒に一回ループ
@tasks.loop(seconds=60)
async def loop():
    # botが起動するまで待つ
    await client.wait_until_ready()

    current_time = time.localtime()
    if current_time.tm_min == 0:
        # app-api
        aapi = AppPixivAPI(**_REQUESTS_KWARGS)

        _e = None
        for _ in range(3):
            try:
                aapi.auth(refresh_token=_REFRESH_TOKEN)
                break
            except PixivError as e:
                _e = e
                time.sleep(10)
        else:  # failed 3 times
            raise _e

        # 前回のブックマーク情報を取得
        old_bookmarks = get_old_bookmarks(cur)
        # 最新のブックマーク情報を取得
        new_bookmarks = get_user_bookmarks(aapi, old_bookmarks)
        # 最新のブックマーク情報をDBに保存する
        save_new_bookmarks(cur, conn, new_bookmarks)
        # 前回と最新のブックマーク情報を比較する
        difference_bookmarks = check_new_bookmarks(old_bookmarks, new_bookmarks)
        # 新しいブックマークがあればDiscordに通知する
        if difference_bookmarks:
            channel = client.get_channel(CHANNEL_ID)
            await notify_new_bookmarks(channel, difference_bookmarks)


atexit.register(close_db_connection)


# Botの起動とDiscordサーバーへの接続
client.run(TOKEN)
