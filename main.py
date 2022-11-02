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

import csv
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

import requests
from bs4 import BeautifulSoup
from opencc import OpenCC
from pyowm import OWM
from pyowm.utils import config as OWMConfig
from pyowm.weatherapi25.weather import Weather
from pixivpy3 import AppPixivAPI
from pixivpy3.utils import JsonDict
from telegram import Update, InlineQueryResultArticle, InlineQueryResultPhoto, InlineKeyboardMarkup, \
  InlineKeyboardButton, InputTextMessageContent, \
  Message, ParseMode
from telegram.ext import Updater, Dispatcher, CallbackContext, CommandHandler, MessageHandler, InlineQueryHandler, \
  Filters
from telegram.utils.helpers import escape_markdown
from dotenv import load_dotenv

# Load environment variable from .env file
load_dotenv()

# Bot setting
bot_id = os.getenv("TG_BOT_ID")
start_time = datetime.now()
file_path = {
  "list-bookmark-id": "bookmarks.txt",
  "list-acg-quote": "moegirl-acg-quotes.csv",
  "list-admin": "admins.txt",
  "log-file": f"{bot_id}-{start_time.strftime('%Y%m%d%H%M%S')}.log"
}

# OpenWeatherMap API (via pyowm)
owm_config = OWMConfig.get_default_config()
owm_config["language"] = "zh_tw"
owm = OWM(os.getenv("OWM_API_TOKEN"), config=owm_config)
owmwmgr = owm.weather_manager()

# Pixiv (via pixivpy)
api = AppPixivAPI()
api.auth(refresh_token=os.getenv("PIXIV_AUTH_TOKEN"))

# OpenCC
s2tcon = OpenCC("s2hk.json")
t2scon = OpenCC("hk2s.json")

# Global shared variables
quotes: list[list[str]] = [[], [], []]
total_quotes_count = 0
bookmark_ids = []
admins = []

# Counter
query_count: dict[str, int] = {"pixiv": 0, "weather": 0}

help_text = f"""\
*＊ 使用說明 ＊*
目前支持__8__種命令：

*＊ 試試手氣* (0個參數)
`@{bot_id}`

*＊ 來點色圖* (0個參數)
`@{bot_id}`

*＊ 相關色圖* (2個參數)
`@{bot_id} r `<Pixiv ID>

*＊ 簡轉繁* (2個參數)
`@{bot_id} s `<文字>

*＊ 繁转简* (2個參數)
`@{bot_id} t `<文字>

*＊ 生成動漫梗* (1~3個參數)
`@{bot_id} q `[替換OO] [替換XX]

*＊ 天氣報告* (3~5個參數)
`@{bot_id} w `<City>, [Country], [State]
＊ 目前只支持英文
＊ City: 城市名
＊ Country: 2位字元的地區編碼
＊ State: 2位字元的州份編碼
＊ 範例：Shanghai, CN

*＊ 抓取 Twitter 原圖* (2個參數)
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
    if (cmd is not None and text.startswith(f"/{cmd}@{bot_id}")) or \
        re.match(f"^/.+@{bot_id}", text):
      return True
  else:
    if cmd is not None and text.startswith(f"/{cmd}") or \
        cmd is None and text.startswith("/"):
      return True

  return False


def handle_cmd(update: Update, context: CallbackContext):
  user = update.message.from_user
  logging.info(
    f"Received command {json.dumps({'from_user': {'id': user.id, 'username': user.name}, 'text': update.message.text}, ensure_ascii=False)}")
  if match_cmd(update.message, "start", True):
    update.message.reply_text(text=f"哈囉～我是 {bot_id} ～！", quote=True)
  elif match_cmd(update.message, "say", True):
    update.message.reply_text(text=quotes[0][random.randint(0, len(quotes[0]) - 1)], quote=True)
  elif match_cmd(update.message, "stats", True):
    handle_bot_stats(update, context)
  elif match_cmd(update.message, None, True):
    update.message.reply_text(text="Sorry～我不懂你在說啥呢～！", quote=True)


def handle_trans_cc(update: Update, context: CallbackContext, cc_profile: OpenCC):
  # Call with arguments
  if update.message is not None:
    text = "".join(context.args)
    # Call with replied message as argument
    if not text:
      if update.message.reply_to_message is not None:
        reply_text = cc_profile.convert(update.message.reply_to_message.text)
        update.message.reply_text(text=reply_text, quote=True)
      return

    message_id = update.message.message_id
    reply_text = cc_profile.convert(text)
    # Message id of the response message
    replied: Message = context.bot.send_message(chat_id=update.effective_chat.id, text=reply_text)
    # TODO Make user_data persistence
    context.user_data[message_id] = replied.message_id

  else:
    text = "".join(context.args)
    if not text:
      return

    message_id = update.edited_message.message_id
    reply_text = cc_profile.convert(text)
    context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=context.user_data[message_id],
                                  text=reply_text)


def handle_bot_log(update: Update, context: CallbackContext):
  if update.message and update.message.chat.type == "private":
    if update.message.from_user.id in admins:
      update.message.reply_document(document=open(file_path["log-file"], "r"), quote=True)
    else:
      update.message.reply_text("不能看喔～", quote=True)


def handle_bot_stats(update: Update, context: CallbackContext):
  reply_text = textwrap.dedent(f"""\
    *＊ {escape_markdown(bot_id, version=2)} 統計數據 ＊*
    *＊ 運行時間:* {datetime.now() - start_time}
    *＊ 色圖數量:* {len(bookmark_ids)}
    *＊ ACG名言數量:* {total_quotes_count}
    *＊ 色圖查詢次數:* {query_count.get("pixiv", 0)}
    *＊ 天氣查詢次數:* {query_count.get("weather", 0)}
    ＊ 使用 /bot\\_log 下載運行日誌""")
  reply_text = re.sub(r"([.-])", r"\\\1", reply_text)
  update.message.reply_text(reply_text, parse_mode=ParseMode.MARKDOWN_V2, quote=True)


def handle_update_bookmarks(update: Update, context: CallbackContext):
  if update.message and update.message.chat.type == "private":
    if update.message.from_user.id in admins:
      msg: Message = context.bot.send_message(chat_id=update.effective_chat.id, text="正在更新 Pixiv 書籤索引")
      n = fetch_latest_bookmarks()
      context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id,
                                    text=f"新增了 {n} 個新項目")
    else:
      update.message.reply_text("這個命令不能亂用喔～", quote=True)


# Generate Quote lists based on current input `query_text`
def make_quote_reply(query_text: str) -> [InlineQueryResultArticle]:
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
                            illust: JsonDict | None = None) -> InlineQueryResultPhoto | None:
  if (pixiv_id is None) == (illust is None):
    logging.error(f"Detected incorrect usage, either pixiv_id or illust should provide value")
    return

  if pixiv_id is not None:
    logging.info(f"Querying Pixiv illustration {json.dumps({'pixiv_id': pixiv_id})}")
    result = api.illust_detail(pixiv_id)
    illust = result.illust
    if not illust:
      # Refresh token once if failed
      logging.info(f"Pixiv token may expired, attempt to refresh...")
      api.auth(refresh_token=os.getenv("PIXIV_AUTH_TOKEN"))
      result = api.illust_detail(pixiv_id)
      illust = result.illust

  if illust:
    if not illust.visible:
      logging.info(f"Queried ID exists but not currently accessible {json.dumps({'pixiv_id': illust.id})}")
      return

    if pixiv_id is not None:
      logging.info(f"Query sucessful {json.dumps({'pixiv_id': pixiv_id, 'title': illust.title}, ensure_ascii=False)}")
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

    keyboard = [[
      InlineKeyboardButton(text="點我再來", switch_inline_query_current_chat=""),
      InlineKeyboardButton(text="相關作品", switch_inline_query_current_chat=f"r {illust.id}")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    query_count["pixiv"] += 1

    # Get image of higher quality
    img_url = re.sub("c/600x1200_90/", "", illust.image_urls.large)

    return InlineQueryResultPhoto(
      id=uuid.uuid4().hex,
      title="來點色圖",
      description=illust.title,
      photo_url=img_url,
      thumb_url=illust.image_urls.square_medium,
      caption=caption_text,
      parse_mode=ParseMode.MARKDOWN_V2,
      reply_markup=reply_markup
    )

  logging.error(f"Query failed {json.dumps({'pixiv_id': pixiv_id})}")


# Fetch random Pixiv illustration
def get_random_pixiv_illust() -> InlineQueryResultPhoto | InlineQueryResultArticle:
  # Retry up to 3 times
  for retry_count in range(1, 4):
    pxid = bookmark_ids[random.randint(0, len(bookmark_ids) - 1)]
    reply_image = make_pixiv_illust_reply(pixiv_id=pxid)
    if reply_image:
      return reply_image
    logging.warning(f"Retrying pixiv query for the {retry_count} of 3 times {json.dumps({'pixiv_id': pxid})}")

  logging.warning(f"Retry limit reached")
  # Feedback reply
  return InlineQueryResultArticle(
    id=uuid.uuid4().hex,
    title="找不到色圖",
    input_message_content=InputTextMessageContent("沒有結果")
  )


# Fetch related Pixiv illustration
def get_related_pixiv_illust(pxid: int) -> [InlineQueryResultPhoto]:
  result = api.illust_related(pxid)
  replies = []
  if not result.illusts:
    # Refresh token once if failed
    logging.info(f"Pixiv token may expired, attempt to refresh...")
    api.auth(refresh_token=os.getenv("PIXIV_AUTH_TOKEN"))
    result = api.illust_related(pxid)

  if not result.illusts:
    return replies

  for illust in result.illusts:
    i = make_pixiv_illust_reply(illust=illust)
    if i is not None:
      replies.append(i)

  return replies


# Generate weather reply based on given `locations`
def make_owm_reply(locations: list) -> list[InlineQueryResultArticle]:
  results = []
  for target in locations:
    loc_name = f'{target[1]}, {target[2]}{", " if target[3] else ""}{target[3] or ""}'
    observation = owmwmgr.weather_at_coords(target[4], target[5])
    if observation is None:
      logging.warning(f"0 result from OpenWeatherMap API received \
        {json.dumps({'location': loc_name, 'lat': target[4], 'lon': target[5]}, ensure_ascii=False)}")
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
    logging.info(f"Query sucessful {json.dumps({'location': loc_name}, ensure_ascii=False)}")
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
    username = reply_dict["user"]["screen_name"]
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


def handle_inline_respond(update: Update, context: CallbackContext):
  query = update.inline_query.query.strip()
  user = update.inline_query.from_user
  logging.info(
    f"Received user query {json.dumps({'from_user': {'id': user.id, 'username': user.name}, 'query_text': query}, ensure_ascii=False)}")
  if not query:
    reply_quote = quotes[0][random.randint(0, len(quotes[0]) - 1)]
    reply_image = get_random_pixiv_illust()

    update.inline_query.answer(results=[
      reply_image,
      InlineQueryResultArticle(
        id=uuid.uuid4().hex,
        title="試試手氣～",
        input_message_content=InputTextMessageContent(reply_quote)
      ), help_inline_reply], cache_time=0)

    return

  match query[0]:
    # Get help
    case 'h':
      update.inline_query.answer([help_inline_reply], auto_pagination=True, cache_time=3600)

    # Get quotes
    case 'q':
      results = make_quote_reply(query[1:])
      update.inline_query.answer(results, auto_pagination=True, cache_time=3600)

    # Simplified-Traditional Chinese translate
    case 's':
      if len(query) > 2 and query[1] == ' ':
        reply_text = s2tcon.convert(query[2:].strip())
        update.inline_query.answer(results=[
          InlineQueryResultArticle(
            id=uuid.uuid4().hex,
            title="簡轉繁",
            input_message_content=InputTextMessageContent(reply_text),
            description=reply_text
          )], cache_time=3600)

      else:
        update.inline_query.answer(results=[help_inline_reply], cache_time=3600)

    # Traditional-Simplified Chinese translate
    case 't':
      if len(query) > 2 and query[1] == ' ':
        reply_text = t2scon.convert(query[2:].strip())
        update.inline_query.answer(results=[
          InlineQueryResultArticle(
            id=uuid.uuid4().hex,
            title="繁转简",
            input_message_content=InputTextMessageContent(reply_text),
            description=reply_text
          )], cache_time=3600)

      else:
        update.inline_query.answer(results=[help_inline_reply], cache_time=3600)

    # Get weather forecast
    case 'w':
      if len(query) > 2 and query[1] == ' ':
        city_loc = list(filter(None, re.split(r"\s*,\s*", query[2:].strip())))
        argc = len(city_loc)
        if argc == 0 or argc > 3 or \
            (argc == 2 and len(city_loc[1]) != 2) or \
            (argc == 3 and (len(city_loc[1]) != 2 or len(city_loc[2]) != 2)):
          update.inline_query.answer(results=[help_inline_reply], cache_time=3600)
          return

        city_ids = owm.city_id_registry()
        locations = city_ids.ids_for(*city_loc, matching="like")
        logging.info(
          f"Found {len(locations)} locations for {json.dumps({'query_text': query[2:].strip()}, ensure_ascii=False)}")
        # Only not more than 8 results
        if len(locations) == 0:
          update.inline_query.answer(results=[
            InlineQueryResultArticle(
              id=uuid.uuid4().hex,
              title="沒有結果",
              input_message_content=InputTextMessageContent("沒有結果"),
              description="你所搜尋的城市可能不存在"
            )
          ], cache_time=3600)

          return

        if len(locations) > 5:
          update.inline_query.answer(results=[
            InlineQueryResultArticle(
              id=uuid.uuid4().hex,
              title="結果太多",
              input_message_content=InputTextMessageContent("結果太多"),
              description="存在多個同名城市，請加入地區編碼"
            )
          ], cache_time=3600)

          return

        update.inline_query.answer(make_owm_reply(locations), cache_time=300, auto_pagination=True)

      else:
        update.inline_query.answer(results=[help_inline_reply], cache_time=3600)
        return

    case 'r':
      if len(query) > 2 and query[1] == ' ':
        try:
          pxid = int(query[2:])
          results = get_related_pixiv_illust(pxid)
          if results:
            update.inline_query.answer(results=results, cache_time=300, auto_pagination=True)
          else:
            update.inline_query.answer(results=[
              InlineQueryResultArticle(
                id=uuid.uuid4().hex,
                title="找不到相關色圖",
                input_message_content=InputTextMessageContent("沒有結果")
              )], cache_time=300, auto_pagination=True)

        except ValueError:
          update.inline_query.answer(results=[help_inline_reply], cache_time=3600)
          return

    case 'm':
      if len(query) > 2 and query[1] == ' ':
        twid: int
        try:
          twid = int(query[2:])

        except ValueError:
          matches = re.match(r"(?:https://)?(?:\w+\.)?twitter\.com/(\w+)/status/(\d+)", query[2:])
          if matches is not None:
            twid = matches.group(2)
          else:
            update.inline_query.answer(results=[help_inline_reply], cache_time=3600)
            return

        result = make_twi_reply(twid)
        if result:
          update.inline_query.answer(results=[result], cache_time=3600)
        else:
          update.inline_query.answer(results=[
            InlineQueryResultArticle(
              id=uuid.uuid4().hex,
              title="找不到相關 Tweet",
              input_message_content=InputTextMessageContent("沒有結果")
            )], cache_time=300)

      else:
        update.inline_query.answer(results=[help_inline_reply], cache_time=3600)

    case _:
      update.inline_query.answer(results=[help_inline_reply], cache_time=3600)


# Build quote list from file `path`
def build_quote_list():
  global quotes, total_quotes_count
  path = Path(file_path["list-acg-quote"])
  # Download the file if not exist
  if not path.exists():
    response = requests.get(
      "https://zh.moegirl.org.cn/index.php?title=Template:ACG%E7%BB%8F%E5%85%B8%E5%8F%B0%E8%AF%8D&action=edit")
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

      if re.search("o{2,}", result) and re.search("x{2,}", result):
        quotes[2].append(s2tcon.convert(result))
      elif re.search("o{2,}", result):
        quotes[1].append(s2tcon.convert(result))
      else:
        quotes[0].append(s2tcon.convert(result))

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
  logging.info(f"Built ACG quote list with {total_quotes_count} elements")


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
      logging.info(f"Pixiv token may expired, attempt to refresh...")
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

  logging.info(f"Added {n} new elements")
  logging.info(f"Built pixiv list with {len(bookmark_ids)} elements")


def build_admin_list():
  path = Path(file_path["list-admin"])
  path.touch(exist_ok=True)
  with open(path, "r") as f:
    for line in f:
      admins.append(int(line.rstrip("\n")))

  if not admins:
    logging.warning("No admin exist!")
  else:
    logging.info(f"Found {len(admins)} admins user_ids={admins}")


def main():
  logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(filename)s:%(lineno)s]: %(funcName)s - %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    handlers=[
      RotatingFileHandler(file_path["log-file"], mode="w+", maxBytes=5 * 1024 * 1024, backupCount=2),
      logging.StreamHandler()
    ])

  logging.info(f"Bot {bot_id} is starting")
  # Build lists
  build_quote_list()
  build_pixivid_list()
  build_admin_list()
  updater = Updater(token=os.getenv("TG_BOT_API_TOKEN"))
  dispatcher: Dispatcher = updater.dispatcher
  handlers = [
    CommandHandler("s2t", lambda update, context: handle_trans_cc(update, context, s2tcon)),
    CommandHandler("t2s", lambda update, context: handle_trans_cc(update, context, t2scon)),
    CommandHandler("bot_log", handle_bot_log),
    CommandHandler("update_bookmarks", handle_update_bookmarks),
    InlineQueryHandler(handle_inline_respond),
    MessageHandler(Filters.command & ~Filters.update.edited_message, handle_cmd)
  ]

  for ent in handlers:
    dispatcher.add_handler(ent)
  updater.start_polling()
  updater.idle()


if __name__ == '__main__':
  main()
