#  This file is part of telegram-bot.
#  Copyright (c) 2022 Joe Ma <rikkaneko23@gmail.com>
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Lesser General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.

import asyncio
import csv
from enum import IntEnum
import json
import logging
import os
import random
import re
import uuid
import textwrap
from datetime import datetime
from pathlib import Path
from time import sleep
from logging.handlers import RotatingFileHandler
from typing import List
from argparse import ArgumentParser

import requests
from bs4 import BeautifulSoup
from pyowm import OWM
from pyowm.utils import config as OWMConfig
from pyowm.weatherapi25.weather import Weather
from pixivpy3 import AppPixivAPI
from pixivpy3.utils import JsonDict
from telegram import (
    Update,
    InlineQueryResultArticle,
    InlineQueryResultPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputTextMessageContent,
    InputMediaPhoto,
    Message,
    User
  )
from telegram.ext import (
  Application,
  CallbackContext,
  CommandHandler,
  MessageHandler,
  InlineQueryHandler,
  filters,
  CallbackQueryHandler,
  ContextTypes
)
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from dotenv import load_dotenv
import shortuuid

# Load environment variable from .env file
load_dotenv()

# Bot setting
bot_id = os.getenv("TG_BOT_ID")
bot_pic_url = os.getenv("TG_BOT_PIC_URL")
should_log_pixiv_query = int(os.getenv("LOG_PIXIV_QUERY") or "1")
base_data_dir = "data"
start_time = datetime.now()
file_path = {
  "list-bookmark-id": f"{base_data_dir}/bookmarks.txt",
  "list-acg-quote": f"{base_data_dir}/moegirl-acg-quotes.csv",
  "list-admin": f"{base_data_dir}/admins.txt",
  "log-file": f"{base_data_dir}/{bot_id}-{start_time.strftime('%Y%m%d%H%M%S')}.log"
}


# OpenWeatherMap API (via pyowm)
owm_config = OWMConfig.get_default_config()
owm_config["language"] = "zh_tw"
owm = OWM(os.getenv("OWM_API_TOKEN"), config=owm_config)
owmwmgr = owm.weather_manager()

# Pixiv (via pixivpy)
api = AppPixivAPI()
api.auth(refresh_token=os.getenv("PIXIV_AUTH_TOKEN"))

# logger
log = logging.getLogger(__name__)

# Global shared variables
quotes: list[list[str]] = [[] for x in range(4)]
total_quotes_count = 0
bookmark_ids = []
admins = []

# Counter
query_count: dict[str, int] = {"pixiv": 0, "weather": 0, "lucky": 0}

# Gacha game
gacha_store: dict[str, dict[str, int]] = dict()
gacha_names = ["三星", "四星", "五星", "四星UP", "五星UP"]

gacha_config: dict[str, float] = {
  "4starup_prob": 0.5,
  "4star_prob": 0.051,
  "5starup_prob": 0.5,
  "5star_prob": 0.006,
  "5starup_prob": 0.5,
  "4star_pity": 10,
  "5star_pity": 90
}

gacha_init_profile: dict[str, int] = {
  "balance": 200,
  "total_pulls": 0,
  "3star_count": 0,
  "4star_count": 0,
  "4starup_count": 0,
  "5star_count": 0,
  "5starup_count": 0,
  "648_count": 0,
  "4star_pity_remain": gacha_config["4star_pity"],
  "5star_pity_remain": gacha_config["5star_pity"],
  "4starup_guarantee": 0,
  "5starup_guarantee": 0
}

help_text = f"""\
*＊ 使用說明 ＊*
目前支持__8__種命令：

*＊ 試試手氣* (0~1個參數)
`@{bot_id}` [查詢事項]

*＊ 模擬抽卡* (1個參數)
`@{bot_id}`

*＊ 來點色圖* (0個參數)
`@{bot_id}`

*＊ 看看色圖* (1個參數)
`@{bot_id}` p <Pixiv ID>

*＊ 相關色圖* (1個參數)
`@{bot_id} r `<Pixiv ID>

*＊ 生成動漫梗* (0~3個參數)
`@{bot_id} q `[替換OO] [替換XX]

*＊ 天氣報告* (1~3個參數)
`@{bot_id} w `<City>, [Country], [State]
＊ 目前只支持英文
＊ City: 城市名
＊ Country: 2位字元的地區編碼
＊ State: 2位字元的州份編碼
＊ 範例：Shanghai, CN

*＊ 抓取 Twitter 原圖* (1個參數)
`@{bot_id} m `<Twitter連結/ID>
"""

help_text = re.sub(r"([()<>\[\]~])", r"\\\1", help_text)

help_inline_reply = InlineQueryResultArticle(
  id=uuid.uuid4().hex,
  title="查看幫助",
  input_message_content=InputTextMessageContent(help_text, parse_mode=ParseMode.MARKDOWN_V2)
)


# Bot command matching
def match_cmd(message: Message, cmd: str | None, required_bot_name: bool = True) -> bool:
  text = message.text
  # Command may require @[bot_id] in group
  if required_bot_name and message.chat.type in ["group", "supergroup"]:
    if (cmd is not None and text.startswith(f"/{cmd}@{bot_id}")):
      return True
  else:
    if cmd is not None and text.startswith(f"/{cmd}"):
      return True

  return False


async def handle_cmd(update: Update, context: CallbackContext):
  user = update.message.from_user
  log.info(
    f"Received command #user_id={user.id}, #text=\"{update.message.text}\"")
  if match_cmd(update.message, "start", True):
    await update.message.reply_text(text=f"哈囉～我是 {bot_id} ～！", quote=True)
  elif match_cmd(update.message, "say", True):
    await update.message.reply_text(text=quotes[0][random.randint(0, len(quotes[0]) - 1)], quote=True)
  elif match_cmd(update.message, "stats", True):
    await handle_bot_stats(update, context)
  elif match_cmd(update.message, None, True):
    await update.message.reply_text(text="Sorry～我不懂你在說啥呢～！", quote=True)


async def handle_bot_log(update: Update, context: CallbackContext):
  if update.message and update.message.chat.type == "private":
    if update.message.from_user.id in admins:
      await update.message.reply_document(document=open(file_path["log-file"], "r"), quote=True)
    else:
      await update.message.reply_text("不能看喔～", quote=True)


async def handle_bot_stats(update: Update, context: CallbackContext):
  reply_text = textwrap.dedent(f"""\
    *＊ {escape_markdown(bot_id, version=2)} 統計數據 ＊*
    *＊ 運行時間:* {datetime.now() - start_time}
    *＊ 色圖數量:* {len(bookmark_ids)}
    *＊ ACG名言數量:* {total_quotes_count}
    *＊ 色圖查詢次數:* {query_count.get("pixiv", 0)}
    *＊ 天氣查詢次數:* {query_count.get("weather", 0)}
    *＊ 占卜查詢次數:* {query_count.get("lucky", 0)}
    ＊ 使用 /bot\\_log 下載運行日誌""")
  reply_text = re.sub(r"([.-])", r"\\\1", reply_text)
  await update.message.reply_text(reply_text, parse_mode=ParseMode.MARKDOWN_V2, quote=True)


async def handle_update_bookmarks(update: Update, context: CallbackContext):
  if update.message and update.message.chat.type == "private":
    if update.message.from_user.id in admins:
      msg: Message = await context.bot.send_message(chat_id=update.effective_chat.id, text="正在更新 Pixiv 書籤索引")
      n = fetch_latest_bookmarks()
      await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id,
                                    text=f"新增了 {n} 個新項目")
    else:
      await update.message.reply_text("這個命令不能亂用喔～", quote=True)


# Generate Quote lists based on current input `query_text`
def make_quote_reply(query_text: str) -> List[InlineQueryResultArticle]:
  args = list(filter(None, re.split(r"\s+", query_text)))
  argc = len(args)
  results = []
  if argc > 2:
    return [help_inline_reply]

  for quote in quotes[argc]:
    reply_text = quote
    if argc >= 1:
      reply_text = re.sub(r"(o+)", args[0], reply_text)
    if argc >= 2:
      reply_text = re.sub(r"(x+)", args[1], reply_text)

    results.append(
      InlineQueryResultArticle(
        id=uuid.uuid4().hex,
        title=quote,
        input_message_content=InputTextMessageContent(reply_text),
        description=reply_text
      )
    )

  return results


# Generate Pixiv illustration reply from `pixiv_id`
def make_pixiv_illust_reply(pixiv_id: int | None = None,
                            illust: JsonDict | None = None,
                            page: int = 0) -> InlineQueryResultPhoto | None:
  if (pixiv_id is None) == (illust is None):
    log.error("Detected incorrect usage, either pixiv_id or illust should provide value")
    return

  if pixiv_id is not None:
    if should_log_pixiv_query == 1:
      log.info(f"Querying Pixiv illustration #pixiv_id={pixiv_id}")
    result = api.illust_detail(pixiv_id)
    illust = result.illust
    if not illust:
      # Refresh token once if failed
      log.info("Pixiv token may expired, attempt to refresh...")
      api.auth(refresh_token=os.getenv("PIXIV_AUTH_TOKEN"))
      result = api.illust_detail(pixiv_id)
      illust = result.illust

  if illust:
    if not illust.visible:
      log.info(f"Queried ID exists but not currently accessible #pixiv_id={illust.id}")
      return

    if pixiv_id is not None and should_log_pixiv_query == 1:
      log.info(f"Query sucessful #pixiv_id={pixiv_id}, #title=\"{illust.title}\"")
    title = escape_markdown(illust.title, version=2)
    author = escape_markdown(illust.user.name, version=2)
    caption_text = textwrap.dedent(f"""\
    標題: [{title}](https://www.pixiv.net/artworks/{illust.id})
    畫師: [{author}](https://www.pixiv.net/users/{illust.user.id})
    標籤: """)
    for tag in illust.tags:
      # Replace some symbols that break hashtag
      name = re.sub(r"\u30FB|\u2606|[-?!:()/. ]", r"_", tag.name)
      caption_text += f"\\#{escape_markdown(name, version=2)} "
    caption_text += f"\\#pixiv [id\\={illust.id}](https://www.pixiv.net/artworks/{illust.id})"

    keyboard = [
      [
        InlineKeyboardButton(text="點我再來", switch_inline_query_current_chat=""),
        InlineKeyboardButton(text="相關作品", switch_inline_query_current_chat=f"r {illust.id}"),
        InlineKeyboardButton(text="換一張 🔁", callback_data=json.dumps({"action": "change", "type": "pixiv"}))
      ]
    ]
    
    # Get image of higher quality
    img_url = re.sub("c/600x1200_90/", "", illust.image_urls.large)
    
    if illust.meta_pages:
      keyboard.insert(0, [
        InlineKeyboardButton(
          text="上一頁 ⬅️", callback_data=json.dumps({"id": pixiv_id, "page": page-1, "type": "pixiv"})),
        InlineKeyboardButton(text=f"• {page} •", callback_data="{}"),
        InlineKeyboardButton(
          text="下一頁 ➡️", callback_data=json.dumps({"id": pixiv_id, "page": page+1, "type": "pixiv"}))
      ])
      
      if page < 0 or page >= len(illust.meta_pages):
        return
      img_url = re.sub("c/600x1200_90/", "", illust.meta_pages[page].image_urls.large)
        
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    query_count["pixiv"] += 1

    return InlineQueryResultPhoto(
      id=uuid.uuid4().hex,
      title="來點色圖",
      description=illust.title,
      photo_url=img_url,
      thumbnail_url=illust.image_urls.square_medium,
      caption=caption_text,
      parse_mode=ParseMode.MARKDOWN_V2,
      reply_markup=reply_markup
    )

  log.error(f"Query failed #pixiv_id={pixiv_id}")


# Fetch random Pixiv illustration
def get_random_pixiv_illust() -> InlineQueryResultPhoto | InlineQueryResultArticle:
  # Retry up to 3 times
  for retry_count in range(1, 4):
    pxid = bookmark_ids[random.randint(0, len(bookmark_ids) - 1)]
    reply_image = make_pixiv_illust_reply(pixiv_id=pxid)
    if reply_image:
      return reply_image
    log.warning(f"Retrying pixiv query for the {retry_count} of 3 times #pixiv_id={pxid}")

  log.warning("Retry limit reached")
  # Feedback reply
  return InlineQueryResultArticle(
    id=uuid.uuid4().hex,
    title="找不到色圖",
    input_message_content=InputTextMessageContent("沒有結果")
  )


# Fetch related Pixiv illustration
def get_related_pixiv_illust(pxid: int) -> List[InlineQueryResultPhoto]:
  result = api.illust_related(pxid)
  replies = []
  if not result.illusts:
    # Refresh token once if failed
    log.info("Pixiv token may expired, attempt to refresh...")
    api.auth(refresh_token=os.getenv("PIXIV_AUTH_TOKEN"))
    result = api.illust_related(pxid)

  if not result.illusts:
    return replies

  for illust in result.illusts:
    i = make_pixiv_illust_reply(illust=illust)
    if i is not None:
      replies.append(i)

  return replies

async def handle_pixiv_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
  query = update.callback_query
  callback_data: dict[str, int | str] = json.loads(query.data)
  
  if callback_data.get("action") == "change":
    update_result = get_random_pixiv_illust()
    
  elif "id" in callback_data and "page" in callback_data:
    update_result = make_pixiv_illust_reply(pixiv_id=callback_data["id"], page=callback_data["page"])
    if not update_result:
      await query.answer("已經到底啦！", show_alert=True)
      return
  
  # Update existing message
  await query.edit_message_media(
    media=InputMediaPhoto(
      media=update_result.photo_url, 
      caption=update_result.caption, 
      parse_mode=update_result.parse_mode
    ),
    reply_markup=update_result.reply_markup
  )


# Generate weather reply based on given `locations`
async def make_owm_reply(locations: list) -> list[InlineQueryResultArticle]:
  results = []
  for target in locations:
    loc_name = f'{target[1]}, {target[2]}{", " if target[3] else ""}{target[3] or ""}'
    observation = owmwmgr.weather_at_coords(target[4], target[5])
    if observation is None:
      log.warning(f"0 result from OpenWeatherMap API received #location=\"{loc_name}\", #lat={target[4]}, #lon={target[5]}")
      return [InlineQueryResultArticle(
        id=uuid.uuid4().hex,
        title="沒有結果",
        input_message_content=InputTextMessageContent("沒有結果"),
        description="OWM目前不支持這個城市的天氣查詢"
      )]

    weather: Weather = observation.weather
    temp_data = weather.temperature(unit="celsius")
    wind_data = weather.wind(unit="km_hour")
    pressure = weather.barometric_pressure()
    log.info(f"Query sucessful #location=\"{loc_name}\"")
    reply_text = textwrap.dedent(f"""\
      *{loc_name} 天氣報告*
      
      *天氣狀況：*{weather.detailed_status}
      *體感温度：*{temp_data["feels_like"]:.1f}°C
      *實際温度：*{temp_data["temp"]:.1f}°C
      *最高温度：*{temp_data["temp_max"]:.1f}°C
      *最低温度：*{temp_data["temp_min"]:.1f}°C
      *風速：*{wind_data["speed"]:.1f} km/h \\({wind_data["deg"]}°\\)
      *濕度：*{weather.humidity}%
      *雲量：*{weather.clouds}%
      *大氣壓：*{pressure["press"]} hPa
      *能見度：*{weather.visibility_distance / 1000} km
      *過去1小時的降雨量：*{weather.rain.get("1h") or 0} mm""")

    reply_text = re.sub(r"([.-])", r"\\\1", reply_text)

    results.append(
      InlineQueryResultArticle(
        id=uuid.uuid4().hex,
        title=loc_name,
        input_message_content=InputTextMessageContent(message_text=reply_text, parse_mode=ParseMode.MARKDOWN_V2),
        description=f'{temp_data["temp"]:.1f}°C'
      )
    )

    query_count["weather"] += 1

  return results


# Generate downloadable illustration link reply
def make_twi_reply(twid: int) -> InlineQueryResultArticle | None:
  url = f"https://cdn.syndication.twimg.com/tweet?id={twid}"
  response = requests.get(url)
  # requests may not detect the correct encoding
  response.encoding = 'UTF-8'
  reply = response.text
  try:
    reply_dict: dict = json.loads(reply)
    illust_urls = reply_dict.get("photos", [])
    author = escape_markdown(reply_dict["user"]["name"], version=2)
    thumb_url = reply_dict["user"]["profile_image_url_https"]
    text = escape_markdown(reply_dict["text"], version=2)
    reply_text = textwrap.dedent(f"""
      *作者：*[{author}](https://twitter.com/{reply_dict["user"]["screen_name"]})
      *內容：*{text}
      
      """)

    if illust_urls:
      reply_text += "*插圖：*"
      thumb_url = illust_urls[0]["url"]

    for idx, illust in enumerate(illust_urls):
      reply_text += f"[\\[{idx}\\]]({illust['url']}) "

    return InlineQueryResultArticle(
      id=uuid.uuid4().hex,
      title=reply_dict["user"]["name"],
      description=reply_dict["text"],
      thumb_url=thumb_url,
      input_message_content=InputTextMessageContent(message_text=reply_text, parse_mode=ParseMode.MARKDOWN_V2)
    )

  except ValueError:
    return None


def make_lucky_reply(user: User, target: str | None = None) -> InlineQueryResultArticle:
  today = datetime.now().strftime("%Y-%m-%d")
  rng = random.Random(f"{today}+{user.id}+{target}")
  possible_results = ["大凶", "凶", "小凶", "普通", "吉", "小吉", "大吉"]
  result = possible_results[rng.randint(0,6)]
  user_str = user.full_name
  if user.username is not None:
    user_str += f" (@{user.username})"
  user_str = escape_markdown(user_str, version=2)
  
  message: str = ""
  keyboard = [[
      InlineKeyboardButton(text="我也試試", switch_inline_query_current_chat=""),
  ]]
  
  if target is not None:
    target = escape_markdown(target, version=2)
    message = textwrap.dedent(f"""\
      你好，{user_str}
      所求事物：{target}
      結果：{result}
      """)
    keyboard[0].append(InlineKeyboardButton(text=target, switch_inline_query_current_chat=target))
  else:
    message = textwrap.dedent(f"""\
      你好，{user_str}
      汝今天的運程：{result}
      """)
  
  query_count["lucky"] += 1
  
  return InlineQueryResultArticle(
      id=uuid.uuid4().hex,
      title="試試手氣",
      description=target,
      thumbnail_url=bot_pic_url,
      input_message_content=InputTextMessageContent(message, parse_mode=ParseMode.MARKDOWN_V2),
      reply_markup=InlineKeyboardMarkup(keyboard)
    )

def make_gacha_reply(user: User) -> InlineQueryResultArticle:
  user_str = user.full_name
  if user.username is not None:
    user_str += f" (@{user.username})"
  user_str = escape_markdown(user_str, version=2)
  message = textwrap.dedent(f"""\
      你好，{user_str}
      
      汝辛苦刷了一整個大版本，終於存到 200 抽的石頭
      現在是見證奇蹟的時候啦！
      
      點下任意躍遷按鈕開始
      """)
  
  keyboard = [
    [
      InlineKeyboardButton(text="躍遷1次", callback_data=json.dumps({"action": "1pull", "owner": user.id, "type": "gacha"})),
      InlineKeyboardButton(text="躍遷10次", callback_data=json.dumps({"action": "10pull", "owner": user.id, "type": "gacha"})),
    ], [
      InlineKeyboardButton(text="我也試試", switch_inline_query_current_chat="")
    ]
  ]
  
  return InlineQueryResultArticle(
      id=uuid.uuid4().hex,
      title="抽卡！",
      description="汝今天適合抽卡嗎?",
      thumbnail_url=bot_pic_url,
      input_message_content=InputTextMessageContent(message, parse_mode=ParseMode.MARKDOWN_V2),
      reply_markup=InlineKeyboardMarkup(keyboard)
    )

class Gacha(IntEnum):
  NO_GACHA = 0,
  THREE = 3,
  FOUR = 4,
  FIVE = 5,
  FOUR_UP = 6,
  FIVE_UP = 7,

def do_gacha(gacha_data: dict[str, int]) -> Gacha:
  if gacha_data["balance"] <= 0:
    return Gacha.NO_GACHA
  gacha_data["balance"] -= 1
  gacha_data["total_pulls"] += 1
  rng = random.Random(datetime.now().timestamp())
  result = rng.random()
  
  # 5-star
  if result < gacha_config["5star_prob"] or gacha_data["5star_pity_remain"] <= 1:
    # Reset pity count
    gacha_data["5star_pity_remain"] = gacha_config["5star_pity"]
    result = rng.random()
    # 5-star UP!
    if result < gacha_config["5starup_prob"] or gacha_data["5starup_guarantee"] == 1:
      # Reset UP guarantee
      gacha_data["5starup_guarantee"] = 0
      gacha_data["5starup_count"] += 1
      return Gacha.FIVE_UP
    else:
      gacha_data["5star_count"] += 1
      gacha_data["5starup_guarantee"] = 1
      return Gacha.FIVE
    
  # 4-star
  elif result < gacha_config["5star_prob"] + gacha_config["4star_prob"] or gacha_data["4star_pity_remain"] <= 1:
    gacha_data["5star_pity_remain"] -= 1
    # Reset pity count
    gacha_data["4star_pity_remain"] = gacha_config["4star_pity"]
    result = rng.random()
    # 4-star UP!
    if result < gacha_config["4starup_prob"] or gacha_data["4starup_guarantee"] == 1:
      # Reset UP guarantee
      gacha_data["4starup_guarantee"] = 0
      gacha_data["4starup_count"] += 1
      return Gacha.FOUR_UP
    else:
      gacha_data["4star_count"] += 1
      gacha_data["4starup_guarantee"] = 1
      return Gacha.FOUR
    pass
  # 3-star
  else:
    gacha_data["3star_count"] += 1
    gacha_data["4star_pity_remain"] -= 1
    gacha_data["5star_pity_remain"] -= 1
    return Gacha.THREE
  

async def handle_gacha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user = update.callback_query.from_user
  user_str = user.full_name
  if user.username is not None:
    user_str += f" (@{user.username})"
  user_str = escape_markdown(user_str, version=2)
  query = update.callback_query
  callback_data: dict[str, int | str] = json.loads(query.data)
  gacha_id = callback_data.get("id")
  gacha_message: str = ""
  has_5star = False
  
  # Initate game if it is first run
  if gacha_id is None:
    gacha_id = shortuuid.uuid()[:8]
    gacha_store[gacha_id] = dict(gacha_init_profile)
    gacha_store[gacha_id]["owner"] = callback_data["owner"]
  
  gacha_data = gacha_store.get(gacha_id)
  if gacha_data is None:
    await query.answer("你錯過了本次躍遷活動，請開個新的吧！\n可能本BOT曾經重啟過！", show_alert=True)
    return
  if gacha_data["owner"] != user.id:
    await query.answer("這不是你的按鈕！\n再亂點我要叫公司的人去你家收債了！", show_alert=True)
    return
  
  match callback_data["action"]:
    case "1pull":
      if gacha_data["balance"] >= 1:
        result = do_gacha(gacha_data)
        await asyncio.sleep(0)
        gacha_message = f"你抽到了1個{gacha_names[result-3]}"
        if result == Gacha.FIVE or result == Gacha.FIVE_UP:
          has_5star = True
      else:
        await query.answer("你沒有足夠的石頭躍遷1次¯⁠\⁠_⁠(⁠ ͡⁠°⁠ ͜⁠ʖ⁠ ͡⁠°⁠)⁠_⁠/⁠¯", show_alert=True)
        return
      
    case "10pull":
      if gacha_data["balance"] >= 10:
        # No. of THREE, FOUR, FIVE, FOUR_UP, FIVE_UP
        results = [0, 0, 0, 0, 0]
        for i in range(10):
          result = do_gacha(gacha_data)
          results[result-3] += 1
          if result == Gacha.FIVE or result == Gacha.FIVE_UP:
            has_5star = True
          await asyncio.sleep(0)
          
        
        gacha_message = f"你抽到了"
        for i in range(5):
          if results[i] == 0:
            continue
          gacha_message += f"{results[i]}個{gacha_names[i]}, "
        gacha_message = gacha_message[:-2]
        gacha_message += "。"
      else:
        await query.answer("你沒有足夠的石頭躍遷10次¯⁠\⁠_⁠(⁠ ͡⁠°⁠ ͜⁠ʖ⁠ ͡⁠°⁠)⁠_⁠/⁠¯", show_alert=True)
        return
    
    case "648":
      gacha_data["balance"] += 81
      gacha_data["648_count"] += 1
      gacha_message = "你忍受不住課金的誘惑，下了一單648！"
     
  message = textwrap.dedent(f"""\
      你好，{user_str}
      
      {"🎊🎊" if has_5star else ""}__{gacha_message}__{"🎊🎊" if has_5star else ""}
      
      *目前的成果*
      三星: {gacha_data["3star_count"]}
      四星: {gacha_data["4star_count"] + gacha_data["4starup_count"]}
      五星: {gacha_data["5star_count"] + gacha_data["5starup_count"]}
      四星UP: {gacha_data["4starup_count"]}
      五星UP: {gacha_data["5starup_count"]}

      已抽卡次數：{gacha_data["total_pulls"]}
      剩餘的抽卡次數：{gacha_data["balance"]}
      """)
  
  if gacha_data["648_count"] > 0:
    message += f"課金次數：{gacha_data['648_count']}\n"
  
  if gacha_data["4star_pity_remain"] <= 0:
    message += "_下次保證四星_\n"
  if gacha_data["4starup_guarantee"] == 1:
    message += "_下次抽中四星保證Up_\n"
  if gacha_data["5star_pity_remain"] <= 0:
    message += "_下次保證五星_\n"
  if gacha_data["5starup_guarantee"] == 1:
    message += "_下次抽中五星保證Up（你歪了）_\n"
  
  keyboard = [
    [
      InlineKeyboardButton(text="躍遷1次", callback_data=json.dumps({"action": "1pull", "id": gacha_id, "type": "gacha"})),
      InlineKeyboardButton(text="躍遷10次", callback_data=json.dumps({"action": "10pull", "id": gacha_id, "type": "gacha"})),
    ],
    [ 
      InlineKeyboardButton(text="來一單648！", callback_data=json.dumps({"action": "648", "id": gacha_id, "type": "gacha"})),
      InlineKeyboardButton(text="我也試試", switch_inline_query_current_chat=""),
    ]
  ]

  await query.edit_message_text(message, ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_inline_respond(update: Update, context: CallbackContext):
  query = update.inline_query.query.strip()
  user = update.inline_query.from_user
  log.info(
    f"Received user query #user_id={user.id}, #query=\"{query}\"")
  if not query:
    reply_quote = quotes[0][random.randint(0, len(quotes[0]) - 1)]
    reply_image = get_random_pixiv_illust()
    reply_lucky = make_lucky_reply(user, None)
    reply_gacha = make_gacha_reply(user)

    await update.inline_query.answer(results=[
      reply_image,
      reply_lucky,
      reply_gacha,
      InlineQueryResultArticle(
        id=uuid.uuid4().hex,
        title="生成動漫梗 (0~3個參數)",
        input_message_content=InputTextMessageContent(reply_quote)
      ), help_inline_reply], cache_time=0)

    return

  match query[0]:
    # Get help
    case 'h':
      await update.inline_query.answer([help_inline_reply], cache_time=3600)

    # Get quotes
    case 'q':
      results = make_quote_reply(query[1:])
      await update.inline_query.answer(results, auto_pagination=True, cache_time=3600)

    # Get weather forecast
    case 'w':
      if len(query) > 2 and query[1] == ' ':
        city_loc = list(filter(None, re.split(r"\s*,\s*", query[2:].strip())))
        argc = len(city_loc)
        if argc == 0 or argc > 3 or \
            (argc == 2 and len(city_loc[1]) != 2) or \
            (argc == 3 and (len(city_loc[1]) != 2 or len(city_loc[2]) != 2)):
          await update.inline_query.answer(results=[help_inline_reply], cache_time=3600)
          return

        city_ids = owm.city_id_registry()
        locations = city_ids.ids_for(*city_loc, matching="like")
        log.info(
          f"Found {len(locations)} locations for #query=\"{query[2:].strip()}\"")
        # Only not more than 8 results
        if len(locations) == 0:
          await update.inline_query.answer(results=[
            InlineQueryResultArticle(
              id=uuid.uuid4().hex,
              title="沒有結果",
              input_message_content=InputTextMessageContent("沒有結果"),
              description="你所搜尋的城市可能不存在"
            )
          ], cache_time=3600)

          return

        if len(locations) > 5:
          await update.inline_query.answer(results=[
            InlineQueryResultArticle(
              id=uuid.uuid4().hex,
              title="結果太多",
              input_message_content=InputTextMessageContent("結果太多"),
              description="存在多個同名城市，請加入地區編碼"
            )
          ], cache_time=3600)

          return

        await update.inline_query.answer(await make_owm_reply(locations), cache_time=300, auto_pagination=True)

      else:
        await update.inline_query.answer(results=[help_inline_reply], cache_time=3600)
        return

    case 'r' | 'p':
      if len(query) > 2 and query[1] == ' ':
        try:
          pxid = int(query[2:])
          results = get_related_pixiv_illust(pxid) if query[0] == 'r' else [make_pixiv_illust_reply(pxid)]
          if results:
            await update.inline_query.answer(results=results, cache_time=300, auto_pagination=True)
          else:
            await update.inline_query.answer(results=[
              InlineQueryResultArticle(
                id=uuid.uuid4().hex,
                title="找不到相關色圖",
                input_message_content=InputTextMessageContent("沒有結果")
              )], cache_time=300, auto_pagination=True)

        except ValueError:
          await update.inline_query.answer(results=[help_inline_reply], cache_time=3600)
          return

    case 'm':
      if len(query) > 3 and query[1] == ' ':
        twid: int
        try:
          twid = int(query[2:])

        except ValueError:
          matches = re.match(r"(?:https://)?(?:\w+\.)?twitter\.com/(\w+)/status/(\d+)", query[2:])
          if matches is not None:
            twid = matches.group(2)
          else:
            await update.inline_query.answer(results=[help_inline_reply], cache_time=3600)
            return

        result = make_twi_reply(twid)
        if result:
          await update.inline_query.answer(results=[result], cache_time=3600)
        else:
          await update.inline_query.answer(results=[
            InlineQueryResultArticle(
              id=uuid.uuid4().hex,
              title="找不到相關 Tweet",
              input_message_content=InputTextMessageContent("沒有結果")
            )], cache_time=300)

      else:
        await update.inline_query.answer(results=[help_inline_reply], cache_time=3600)

    case _:
      reply_lucky = make_lucky_reply(user, query)
      await update.inline_query.answer(results=[reply_lucky, help_inline_reply], cache_time=0)


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
  query = update.callback_query
  callback_data: dict[str, int | str] = json.loads(query.data)
  
  if not "type" in callback_data:
    return

  match callback_data["type"]:
    case "pixiv":
      await handle_pixiv_callback(update, context)
    
    case "gacha":
      await handle_gacha_callback(update, context)
  

# Build quote list from file `path`
def build_quote_list(*, build_only=False):
  global quotes, total_quotes_count
  path = Path(file_path["list-acg-quote"])
  # Download the file if not exist
  if not path.exists() or build_only:
    quote_list_sources = json.loads(os.getenv('QUOTE_MOEGIRL_LIST'))
    for url in quote_list_sources:
      print(f'Processing {url}')
      response = requests.get(url)
      # requests may not detect the correct encoding
      response.encoding = 'UTF-8'
      soup = BeautifulSoup(response.text, "html5lib")
      content = soup.find("textarea", {"id": "wpTextbox1"}).text.strip()

      matches = re.findall(r"\[\[(.+?)]]", content)
      for idx, quote in enumerate(matches[2:]):
        qs = quote.split('|')
        result = ""
        for s in qs:
          if s and not re.search(r"[/(){}]", s):
            result = s
            break

        if not result:
          continue
        
        params = re.findall("(?<![a-zA-Z])(o|x){1,}(?![a-zA-Z])", result)
        quotes[len(params)].append(result)

    # Build quote list
    fields = ["quote_text", "param_count"]
    with open(path, "w") as f:
      writer = csv.writer(f)
      writer.writerow(fields)
      for idx, quote_list in enumerate(quotes):
        for quote in quote_list:
          writer.writerow([quote, idx])

  else:
    # Read from local quote sources
    with open(path, "r") as f:
      reader = csv.reader(f)
      # Skip header
      next(reader, None)
      for [quote, param_count] in list(reader):
        quotes[int(param_count)].append(quote)

  total_quotes_count = len(quotes[0]) + len(quotes[1]) + len(quotes[2])
  log.info(f"Built ACG quote list with {total_quotes_count} elements")


# Fetch the latest user bookmarked ids
def fetch_latest_bookmarks() -> int:
  global bookmark_ids
  next_qs = {"user_id": os.getenv("PIXIV_USER_ID")}
  should_break = False
  new_ids = []
  while next_qs:
    result = api.user_bookmarks_illust(**next_qs)
    if not result.illusts:
      # Refresh token once if failed
      log.info("Pixiv token may expired, attempt to refresh...")
      api.auth(refresh_token=os.getenv("PIXIV_AUTH_TOKEN"))
      result = api.user_bookmarks_illust(**next_qs)

    for illust in result.illusts:
      # Skip if the illustration not accessible
      if not illust.visible:
        continue

      if bookmark_ids and illust.id == bookmark_ids[-1]:
        should_break = True
        break

      new_ids.append(illust.id)

    if should_break:
      break

    next_qs = api.parse_qs(result.next_url)
    sleep(random.randint(0, 2))

  with open(file_path["list-bookmark-id"], "a") as f:
    for pxid in reversed(new_ids):
      bookmark_ids.append(pxid)
      f.write(f"{pxid}\n")

  return len(new_ids)


# Build Pixiv ids index from file `path`
def build_pixivid_list():
  global bookmark_ids
  path = Path(file_path["list-bookmark-id"])
  path.touch(exist_ok=True)
  with open(path, "r") as f:
    # Scan the whole file to build the index
    for line in f:
      bookmark_ids.append(int(line.rstrip("\n")))

  n = fetch_latest_bookmarks()

  log.info(f"Added {n} new elements")
  log.info(f"Built pixiv list with {len(bookmark_ids)} elements")


def build_admin_list():
  path = Path(file_path["list-admin"])
  path.touch(exist_ok=True)
  with open(path, "r") as f:
    for line in f:
      admins.append(int(line.rstrip("\n")))

  if not admins:
    log.warning("No admin exist!")
  else:
    log.info(f"Found {len(admins)} admins user_ids={admins}")


def main() -> None:
  logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(filename)s:%(lineno)s]: %(funcName)s - %(message)s",
    level=logging.WARN,
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    handlers=[
      RotatingFileHandler(file_path["log-file"], mode="w+", maxBytes=5 * 1024 * 1024, backupCount=2),
      logging.StreamHandler()
    ])
  log.setLevel(logging.INFO)
  log.info(f"Bot {bot_id} is starting")
  # Build lists
  build_quote_list()
  build_pixivid_list()
  build_admin_list()
  
  application = Application.builder().token(token=os.getenv("TG_BOT_API_TOKEN")).build()
  handlers = [
    CommandHandler("bot_log", handle_bot_log),
    CommandHandler("update_bookmarks", handle_update_bookmarks),
    InlineQueryHandler(handle_inline_respond),
    MessageHandler(filters.COMMAND & (~ filters.UpdateType.EDITED), handle_cmd),
    CallbackQueryHandler(handle_callback_query)
  ]

  application.add_handlers(handlers=handlers)
  application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
  parser = ArgumentParser()
  subparsers = parser.add_subparsers()
  parser.set_defaults(func=lambda _: main())
  build_quote_parser = subparsers.add_parser("build_quote")
  build_quote_parser.set_defaults(func=lambda _: build_quote_list(build_only=True))
  build_bookmarks_parser = subparsers.add_parser("build_bookmarks")
  build_bookmarks_parser.set_defaults(func=lambda _: build_pixivid_list())
  help_parser = subparsers.add_parser("help")
  help_parser.set_defaults(func=lambda _: parser.print_usage())
  args = parser.parse_args()
  args.func(args)
