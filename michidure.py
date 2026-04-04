# 260404_2107

import discord
from discord import app_commands # スラッシュコマンドの実装に必要
import os
from dotenv import load_dotenv
import datetime
# import csv
# import re
# from collections import deque # csvデータをキューとして扱うため
# from ffmpeg import _ffmpeg  # VCでの音声再生に必要（1gouのときはこっち）
from discord.ext import commands, tasks # 一定時間で1回動作させるtask利用のため
import datetime # 時刻取得のため
import json # jsonファイル操作のため
import asyncio # 音声再生が終了するまでの待機処理実装のため
import random # 再生する音声ファイルのランダム抽選のため


# ------------------------------
# ↓ 変数定義
# ------------------------------


load_dotenv()

# discordボットの設定
# Intent系
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = False  # メッセージの内容を読み取るために必要だが，本BotではスラッシュコマンドとVC入退室のみなのでFalseで良い
intents.voice_states = True # ボイスチャンネルの状態を取得するために必要（intentに追加という処理）

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# 稼働鯖（ギルドスラコマ登録用）
work_sv_ids = [
    int(os.getenv("WORK_SV_ID_TEST")), # type: ignore
    int(os.getenv("WORK_SV_ID_SAGYO")), # type: ignore
    int(os.getenv("WORK_SV_ID_SAGYO2")), # type: ignore
    int(os.getenv("WORK_SV_ID_SHINT")) # type: ignore
]

# log用
# log_chid = int(os.getenv("OIIA_LOG_TXID")) # type: ignore

# 本bot固有
# jsonファイルのパス
data_json = "data.json"

# 道連れカウント
count_json = "drag_count.json"

# ------------------------------
# ↑ 変数定義
# ↓ クラス・その他の関数
# ------------------------------


# 退出予約をjsonファイルへ書き込む関数
def save_exit_time_json(data: dict):
    with open(data_json, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
"""
このように保存される
{
    "123456789012345678": {
        "guild_id": 987654321098765432,
        "target_time": "2026-04-05T23:00:00"
    }
}
"""

# 登録された退出予約をjsonファイルから読み取る関数
def load_exit_time_json() -> dict:
    if not os.path.exists(data_json):
        return {}
    try:
        with open(data_json, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}

# 指定された時，分から次にその時間が来るdatatimeｵﾌﾞｼﾞｪｸﾄを作成する関数
def get_target_datatime(hour: int, minute:int) -> datetime.datetime:
    now = datetime.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # 予約された指定時刻が，すでに過去のものである場合，1日加算して明日の予約にする
    if target <= now:
        target += datetime.timedelta(days=1)
    return target

# 道連れ回数をjsonファイルから取得する関数
def load_drag_count():
    if os.path.exists(count_json):
        with open(count_json, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

# 道連れ回数をカウントする関数(drag_count.json)
def save_drag_count(data):
    with open(count_json, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ------------------------------
# ↑ クラス・その他の関数
# ↓ イベントハンドラ
# ------------------------------


# 1分に1回だけ動く部分
@tasks.loop(seconds=60)
async def check_disconnect_time():
    now = datetime.datetime.now()
    schedules = load_exit_time_json()
    updated = False # jsonファイルを更新する必要があるかどうかのフラグ(Trueで必要あり)

    # 切断対象のユーザIDをリストで記録する
    users_to_remove = []

    # VCごとに切断対象のユーザをまとめるための辞書
    targets_by_vc = {}
    """
    構造
    {
        VCオブジェクトA: [ユーザオブジェクトA, ユーザオブジェクトB, ...],
        VCオブジェクトB: [ユーザオブジェクトC, ユーザオブジェクトD, ...]
    }
    """

    # ------------------------------
    # 予約をチェックし，VCごとにグループ化する(振り分ける)
    # ------------------------------
    for user_id_str, schedule in schedules.items():
        # jsonの時刻(文字列)をdatetimeオブジェクトに変換
        target_time = datetime.datetime.fromisoformat(schedule['target_time']) # jsonのtarget_timeというキーに対して

        if now >= target_time:
            user_id = int(user_id_str)
            guild_id = schedule['guild_id']

            # ここに入るユーザ毎のjsonデータは処理時刻を迎えているので，VCに接続中かどうかにかかわらず削除リスト:users_to_removeへ入れる
            users_to_remove.append(user_id_str)

            guild = client.get_guild(guild_id) # ギルド情報を取得
            if guild:
                member = guild.get_member(user_id)

                # ユーザがVCに接続しているかを確認
                if member and member.voice and member.voice.channel:
                    voice_channel = member.voice.channel

                    # 辞書にそのVCがまだ登録されていなければ，空のリストを作成しておく(すぐ後で使う)
                    if voice_channel not in targets_by_vc:
                        targets_by_vc[voice_channel] = []

                    # ここで，切断対象のユーザを，各々のサーバー内のVCに振り分ける．(対象のVCのリストに，そのメンバーを追加する)
                    targets_by_vc[voice_channel].append(member)
    # ------------------------------
    # VCごとに処理
    # ------------------------------
    for voice_channel, members in targets_by_vc.items():
        try:
            # Botを対象のVCに接続
            vc_client = await voice_channel.connect()

            # ------------------------------
            # 音声再生処理
            # ------------------------------
            try:
                # 音声のロングとショートバージョンを確率で決定(80%の確率でショート)
                if random.random() < 0.2:
                    chosen_file = "leaving-music-long-15dB.wav" # 20％の確率でロングとし絶望させる
                else:
                    chosen_file = "leaving-music-15dB.wav"

                audio_source = discord.FFmpegPCMAudio(chosen_file, options='-vn')# wavファイル読み込み(安定化のオプション付きで)
                vc_client.play(audio_source)

                # 音声再生中は，1秒ずつ待機してループをまわし続ける
                while vc_client.is_playing():
                    await asyncio.sleep(1) # 1秒待機
            except Exception as e:
                print(f"音声再生エラー: {e}")
            # ------------------------------

            # 道連れカウント
            drag_data = load_drag_count() # カウントデータを読み込む(drag_count.json)
            updated_count = False # 更新フラグ

            # 切断対象ユーザを一人ずつVCから切断していく
            for member in members:
                try:
                    await member.move_to(None)
                    print(f"{member.display_name} を切断しました")

                    # ------------------------------
                    # 道連れカウント
                    # ------------------------------
                    user_id_str = str(member.id)
                    if user_id_str not in drag_data:
                        drag_data[user_id_str] = {
                            "name": member.display_name,
                            "count": 0
                        }
                    drag_data[user_id_str]["count"] += 1
                    # 名前が変わっているかもしれないので最新の表示名に更新
                    drag_data[user_id_str]["name"] = member.display_name
                    updated_count = True
                    # ------------------------------

                except Exception as e:
                        print(f"{member.display_name} 切断処理中にエラー発生: {e}")

            # ------------------------------
            # 道連れカウントの更新フラグより，一人でも切断したらcount用json上書き
            if updated_count:
                save_drag_count(drag_data)
            # ------------------------------


            # Botを対象のVCから切断
            await vc_client.disconnect()

        except Exception as e:
            print(f"VC({voice_channel.name})での処理中にエラー発生: {e}")


    # ------------------------------
    # 処理が完了したので辞書とjsonから削除して上書き
    # ------------------------------
    for uid in users_to_remove:
        del schedules[uid]
        updated = True # json書き換えのフラグをオン

    if updated:
        save_exit_time_json(schedules)


# ------------------------------
# ↑ イベントハンドラ
# ↓ スラッシュコマンド
# ------------------------------


# 退出時刻を設定する
@tree.command(name="setexittime", description="VCの退出時刻を設定する")
@app_commands.guild_only() # BotとのDMでの使用を制限するためのデコレータ
async def setexittime(interaction: discord.Interaction, hour: int, minute: int, isshow: bool|None):
    # DMから実行された場合は，guild_idが取得できないため，はじく
    if interaction.guild_id is None:
        await interaction.response.send_message("```このコマンドはサーバー内のテキストチャンネルでのみ使用可能です!!```", ephemeral=True)
        return

    # コマンド受付を知らせる
    await interaction.response.defer(thinking=True, ephemeral=bool(isshow)) # 以降, interaction.followupを使う

    # バリデーション
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        await interaction.followup.send("```時刻は 0~23時，0~59分の間で指定してください```")
        return

    # 入力時刻を，datetimeオブジェクトに変換する(同時に日付変更の繰り上げも行う)
    target_time = get_target_datatime(hour, minute)

    # jsonを読み込み，書き込む
    schedules = load_exit_time_json()

    # 既にそのユーザに1つ以上退出予約がある場合
    message_prefix = ""
    user_id_str = str(interaction.user.id)
    if user_id_str in schedules:
        old_time = datetime.datetime.fromisoformat(schedules[user_id_str]['target_time'])
        old_time_str = old_time.strftime("%H:%M")
        message_prefix = f"以前の予約（{old_time_str}）を上書きして\n"

    schedules[str(interaction.user.id)] = {
        "guild_id": interaction.guild_id,
        "target_time": target_time.isoformat()
    }
    save_exit_time_json(schedules) # json書き込み関数呼び出し

    time_str = target_time.strftime("%Y/%m/%d %H:%M")
    await interaction.followup.send(f"```{interaction.user.display_name} が {message_prefix}{time_str} にVC退出予約を設定しました```")


# 退出時刻をリセットする
@tree.command(name="clearexittime", description="VCの退出予約をリセットする")
@app_commands.guild_only() # BotとのDMでの使用を制限するためのデコレータ
async def clearexittime(interaction: discord.Interaction, isshow: bool|None):
    # DMから実行された場合は，guild_idが取得できないため，はじく
    if interaction.guild_id is None:
        await interaction.response.send_message("```このコマンドはサーバー内のテキストチャンネルでのみ使用可能です!!```", ephemeral=True)
        return

    # コマンド受付を知らせる
    await interaction.response.defer(thinking=True, ephemeral=bool(isshow)) # 以降, interaction.followupを使う

    schedules = load_exit_time_json()
    user_id_str = str(interaction.user.id)

    if user_id_str in schedules:
        del schedules[user_id_str]
        save_exit_time_json(schedules) # jsonファイル上書き
        await interaction.followup.send(f"```{interaction.user.display_name} が退出予約をキャンセルしました```")
    else:
        await interaction.followup.send(f"```現在，{interaction.user.display_name} の退出予約はありません```")


# 現在の退出設定を確認する
@tree.command(name="checkexittime", description="現在のVCの退出設定を確認する")
@app_commands.guild_only() # BotとのDMでの使用を制限するためのデコレータ
async def checkexittime(interaction: discord.Interaction, isshow: bool|None):
    # DMから実行された場合は，guild_idが取得できないため，はじく
    if interaction.guild_id is None:
        await interaction.response.send_message("```このコマンドはサーバー内のテキストチャンネルでのみ使用可能です!!```", ephemeral=True)
        return

    # コマンド受付を知らせる
    await interaction.response.defer(thinking=True, ephemeral=bool(isshow)) # 以降, interaction.followupを使う

    schedules = load_exit_time_json()
    user_id_str = str(interaction.user.id)

    if user_id_str in schedules:
        target_time = datetime.datetime.fromisoformat(schedules[user_id_str]['target_time'])
        time_str = target_time.strftime("%Y/%m/%d %H:%M")
        await interaction.followup.send(f"```現在の {interaction.user.display_name} の退出予約は {time_str} です```")
    else:
        await interaction.followup.send(f"```現在，{interaction.user.display_name} の退出予約はありません```")


# コマンド実行ユーザが道連れにされた回数を確認するコマンド
@tree.command(name="myexitcount", description="自分が今まで道連れにされた回数を確認する")
async def myexitcount(interaction: discord.Interaction):
    # コマンド受付を知らせる
    await interaction.response.defer(thinking=True, ephemeral=True) # 以降, interaction.followupを使う

    drag_data = load_drag_count()
    user_id_str = str(interaction.user.id)

    if user_id_str in drag_data:
        count = drag_data[user_id_str]["count"]
        # ephemeral=True で、結果はコマンドを打った本人にしか見えません
        await interaction.followup.send(f"```あなたのこれまでの道連れ回数: {count} 回```", ephemeral=True)
    else:
        await interaction.followup.send("```あなたのこれまでの道連れ回数: 0 回```", ephemeral=True)


# 【管理者用】全員の道連れ回数を確認するコマンド
@tree.command(name="allexitcount", description="【管理者用】全ユーザーの道連れ被害状況を確認します")
async def allexitcount(interaction: discord.Interaction, matchword: str):
    # コマンド受付を知らせる
    await interaction.response.defer(thinking=True, ephemeral=True) # 以降, interaction.followupを使う

    # 合言葉
    MATCHWORD = "fire-thunder"

    # 合言葉の判定
    if matchword != MATCHWORD:
        await interaction.followup.send("```合言葉不一致```", ephemeral=True)
        return

    drag_data = load_drag_count()
    if not drag_data:
        await interaction.followup.send("```まだ誰も道連れにされていません```", ephemeral=True)
        return

    # 回数が多い順（降順）に並び替える
    sorted_data = sorted(drag_data.items(), key=lambda x: x[1]["count"], reverse=True)

    # 表示用のテキストを作成
    result_text = "【道連れ回数ランキング】\n"
    for uid, info in sorted_data:
        result_text += f"・{info['name']}: {info['count']} 回\n"

    await interaction.followup.send(f"```{result_text}```", ephemeral=True)

# ------------------------------
# ↑ スラッシュコマンド
# ↓ discordボットの初期化処理
# ------------------------------


@client.event
async def on_ready():
    # taskの起動
    if not check_disconnect_time.is_running():
        check_disconnect_time.start()

    # 稼働鯖へのギルド同期を順に行う
    for guild_id in work_sv_ids:
        try:
            guild = discord.Object(id=guild_id)
            await tree.sync(guild=guild)
            print(f"サーバ (ID: {guild_id}) にコマンド同期成功")
        except Exception as e:
            print(f"サーバ (ID: {guild_id}) への同期に失敗: {e}")

    # ツリーコマンド動機
    await tree.sync()
    print('michidureBot is ready...')

# discordボット起動のトリガー
client.run(os.getenv("DISCORD_BOT_TOKEN")) # type: ignore