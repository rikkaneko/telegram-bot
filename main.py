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
import logging
import os
import random
import re
import uuid
import textwrap
from pathlib import Path
from time import sleep

import requests
from bs4 import BeautifulSoup
from opencc import OpenCC
from pyowm import OWM
from pyowm.utils import config as OWMConfig
from pyowm.weatherapi25.weather import Weather
from pixivpy3 import AppPixivAPI
from telegram import Update, InlineQueryResultArticle, InlineQueryResultPhoto, InlineKeyboardMarkup, InlineKeyboardButton, InputTextMessageContent, Message, ParseMode
from telegram.ext import Updater, Dispatcher, CallbackContext, CommandHandler, MessageHandler, InlineQueryHandler, Filters
from telegram.utils.helpers import escape_markdown

bot_id = os.getenv("TG_BOT_ID")
# OpenWeatherMap API (via pyowm)
owm_config = OWMConfig.get_default_config()
owm_config["language"] = "zh_tw"
owm = OWM(os.getenv("OWM_API_TOKEN"), config=owm_config)
owmwmgr = owm.weather_manager()
# Pixiv (via pixivpy)
api = AppPixivAPI()
api.auth(refresh_token=os.getenv("PIXIV_AUTH_TOKEN"))

s2tcon = OpenCC("s2hk.json")
t2scon = OpenCC("hk2s.json")
quotes: list[list[str]] = [[], [], []]
bookmark_ids = []


help_text = f"""\
*使用說明*
目前支持__6__種命令：

*試試手氣* (0個參數)
`@{bot_id}`

*來點色圖* (0個參數)
`@{bot_id}`

*簡轉繁* (2個參數)
`@{bot_id} s `<文字>

*繁转简* (2個參數)
`@{bot_id} t `<文字>

*生成動漫梗* (1~3個參數)
`@{bot_id} q `[替換OO] [替換XX]

*天氣報告* (2~4個參數)
`@{bot_id} w `<City>, [Country], [State]
＊ 目前只支持英文
＊ City: 城市名
＊ Country: 2位字元的地區編碼
＊ State: 2位字元的州份編碼
＊ 範例：Shanghai, CN
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
	if match_cmd(update.message, "start", True):
		update.message.reply_text(text="哈囉～我是貓空～！", quote=True)
	elif match_cmd(update.message, "say", True):
		update.message.reply_text(text=quotes[0][random.randint(0, len(quotes[0]) - 1)], quote=True)
	elif match_cmd(update.message, None, True):
		update.message.reply_text(text="Sorry～我不懂你在說啥呢～！", quote=True)


def handle_s2t(update: Update, context: CallbackContext):
	# Call with arguments
	if update.message is not None:
		text = "".join(context.args)
		# Call with replied message as argument
		if not text:
			if update.message.reply_to_message is not None:
				reply_text = s2tcon.convert(update.message.reply_to_message.text)
				update.message.reply_text(text=reply_text, quote=True)
			return
		
		message_id = update.message.message_id
		reply_text = s2tcon.convert(text)
		# Message id of the response message
		replied: Message = context.bot.send_message(chat_id=update.effective_chat.id, text=reply_text)
		context.user_data[message_id] = replied.message_id
	
	else:
		text = "".join(context.args)
		if not text:
			return
		
		message_id = update.edited_message.message_id
		reply_text = s2tcon.convert(text)
		context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=context.user_data[message_id], text=reply_text)


def handle_t2s(update: Update, context: CallbackContext):
	# Call with arguments
	if update.message is not None:
		text = "".join(context.args)
		# Call with replied message as argument
		if not text:
			if update.message.reply_to_message is not None:
				reply_text = t2scon.convert(update.message.reply_to_message.text)
				update.message.reply_text(text=reply_text, quote=True)
			return
		
		message_id = update.message.message_id
		reply_text = t2scon.convert(text)
		# Message id of the response message
		replied: Message = context.bot.send_message(chat_id=update.effective_chat.id, text=reply_text)
		context.user_data[message_id] = replied.message_id
	
	else:
		text = "".join(context.args)
		if not text:
			return
		
		message_id = update.edited_message.message_id
		reply_text = t2scon.convert(text)
		context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=context.user_data[message_id], text=reply_text)


def make_pixiv_illust_reply(pxid: int) -> InlineQueryResultPhoto:
	logging.info(f"[make_pixiv_illust_reply] Querying Pixiv illustration of id=\"{pxid}\"")
	result = api.illust_detail(pxid)
	illust = result.illust
	if illust and illust.visible:
		logging.info(f"[make_pixiv_illust_reply] Query sucessful => id=\"{pxid}\", title=\"{illust.title}\"")
		title = escape_markdown(illust.title, version=2)
		author = escape_markdown(illust.user.name, version=2)
		caption_text = textwrap.dedent(f"""\
		標題：[{title}](https://www.pixiv.net/artworks/{illust.id})
		畫師：[{author}](https://www.pixiv.net/users/{illust.user.id})
		標籤： """)
		for tag in illust.tags:
			# Replace some symbols that break hashtag
			name = re.sub(r"\u30FB|[- ]", r"_", tag.name)
			caption_text += f"\\#{escape_markdown(name, version=2)} "
		caption_text += f"\\#pixiv [id\\={illust.id}](https://www.pixiv.net/artworks/{illust.id})"
		
		keyboard = [[InlineKeyboardButton(text="點我再來", switch_inline_query_current_chat="")]]
		reply_markup = InlineKeyboardMarkup(keyboard)
		
		return InlineQueryResultPhoto(
			id=uuid.uuid4().hex,
			title="來點色圖",
			description=title,
			photo_url=illust.image_urls.large,
			thumb_url=illust.image_urls.square_medium,
			caption=caption_text,
			parse_mode=ParseMode.MARKDOWN_V2,
			reply_markup=reply_markup
		)
	
	logging.error(f"[make_pixiv_illust_reply] Query failed of id=\"{pxid}\"")


def handle_inline_respond(update: Update, context: CallbackContext):
	query = update.inline_query.query.strip()
	if not query:
		reply_quote = quotes[0][random.randint(0, len(quotes[0]) - 1)]
		pxid = bookmark_ids[random.randint(0, len(bookmark_ids) - 1)]
		reply_image = make_pixiv_illust_reply(pxid)
		retry_count = 0
		while reply_image is None and retry_count < 3:
			sleep(random.randint(0, 2) + 1)
			pxid = bookmark_ids[random.randint(0, len(bookmark_ids) - 1)]
			reply_image = make_pixiv_illust_reply(pxid)
			retry_count += 1
		
		if reply_image is None:
			reply_image = InlineQueryResultArticle(
				id=uuid.uuid4().hex,
				title="沒有色圖了",
				input_message_content=InputTextMessageContent("沒有色圖了")
			)
		
		update.inline_query.answer(results=[
			reply_image,
			InlineQueryResultArticle(
				id=uuid.uuid4().hex,
				title="試試手氣～",
				input_message_content=InputTextMessageContent(reply_quote)
			), help_inline_reply], cache_time=0)
		
		return
	
	match query[0]:
		# Get quotes
		case 'q':
			args = list(filter(None, re.split(r"\s+", query[1:])))
			argc = len(args)
			results = []
			if argc > 2:
				update.inline_query.answer(results=[help_inline_reply], cache_time=3600)
				return
			
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
				targets = city_ids.ids_for(*city_loc, matching="like")
				logging.info(f"[city_id_registry] Found {len(targets)} results for the query_text=\"{query[2:].strip()}\"")
				# Only not more than 8 results
				if len(targets) == 0:
					update.inline_query.answer(results=[
						InlineQueryResultArticle(
							id=uuid.uuid4().hex,
							title="沒有結果",
							input_message_content=InputTextMessageContent("沒有結果"),
							description="你所搜尋的城市可能不存在"
						)
					], cache_time=3600)
					
					return
				
				if len(targets) > 5:
					update.inline_query.answer(results=[
						InlineQueryResultArticle(
							id=uuid.uuid4().hex,
							title="結果太多",
							input_message_content=InputTextMessageContent("結果太多"),
							description="存在多個同名城市，請加入地區編碼"
						)
					], cache_time=3600)
					
					return
				
				results = []
				for target in targets:
					observation = owmwmgr.weather_at_coords(target[4], target[5])
					if observation is None:
						update.inline_query.answer(results=[
							InlineQueryResultArticle(
								id=uuid.uuid4().hex,
								title="沒有結果",
								input_message_content=InputTextMessageContent("沒有結果"),
								description="OWM目前不支持這個城市的天氣查詢"
							)
						], cache_time=3600)
						
						return
					
					weather: Weather = observation.weather
					temp_data = weather.temperature(unit="celsius")
					wind_data = weather.wind(unit="km_hour")
					pressure = weather.barometric_pressure()
					loc_name = f'{target[1]}, {target[2]}{", " if target[3] else ""}{target[3] or ""}'
					logging.info(f"[owmwmgr] Replied the weather for \"{loc_name}\"")
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
						*過去1小時的降雨量：*{weather.rain.get("1h") or 0} mm""" \
							.replace(".", "\\.") \
							.replace("-", "\\-"))
					
					results.append(
						InlineQueryResultArticle(
							id=uuid.uuid4().hex,
							title=loc_name,
							input_message_content=InputTextMessageContent(message_text=reply_text, parse_mode=ParseMode.MARKDOWN_V2),
							description=f'{temp_data["temp"]:.1f}°C'
						)
					)
				
				update.inline_query.answer(results, cache_time=300, auto_pagination=True)
				
			else:
				update.inline_query.answer(results=[help_inline_reply], cache_time=3600)
				return
		
		case _:
			update.inline_query.answer(results=[help_inline_reply], cache_time=3600)


def build_quote_list(path: str):
	global quotes
	file_path = Path(path)
	# Download the file if not exist
	if not file_path.exists():
		response = requests.get("https://zh.moegirl.org.cn/index.php?title=Template:ACG%E7%BB%8F%E5%85%B8%E5%8F%B0%E8%AF%8D&action=edit")
		soup = BeautifulSoup(response.text, "html5lib")
		content = soup.find("textarea", { "id": "wpTextbox1" }).text.strip()
		
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
		with open(file_path, "w") as f:
			writer = csv.writer(f)
			writer.writerow(fields)
			for idx, quote_list in enumerate(quotes):
				for quote in quote_list:
					writer.writerow([quote, idx])
	
	else:
		# Read from local quote sources
		with open(file_path, "r") as f:
			reader = csv.reader(f)
			# Skip header
			next(reader, None)
			for [quote, param_count] in list(reader):
				quotes[int(param_count)].append(quote)


def build_pixivid_list(path: str):
	global bookmark_ids
	file_path = Path(path)
	next_qs = { "user_id": os.getenv("PIXIV_USER_ID") }
	should_break = False
	
	file_path.touch(exist_ok=True)
	with open(file_path, "r") as f:
		for line in f:
			bookmark_ids.append(int(line.rstrip("\n")))
	
	newly_add_bookmarks = []
	while next_qs:
		result = api.user_bookmarks_illust(**next_qs)
		for illust in result.illusts:
			# Skip if the illustration not accessible
			if not illust.visible:
				continue
			
			if bookmark_ids and illust.id == bookmark_ids[-1]:
				should_break = True
				break
			
			newly_add_bookmarks.append(illust.id)
		
		if should_break:
			break
		
		next_qs = api.parse_qs(result.next_url)
		sleep(random.randint(0, 4) + 1)
		
	with open(file_path, "a") as f:
		for pxid in reversed(newly_add_bookmarks):
			bookmark_ids.append(pxid)
			f.write(f"{pxid}\n")
	

def main():
	logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
	build_quote_list("moegirl-acg-quotes.csv")
	build_pixivid_list("bookmarks.txt")
	updater = Updater(token=os.getenv("TG_BOT_API_TOKEN"))
	dispatcher: Dispatcher = updater.dispatcher
	handlers = [
		CommandHandler("s2t", handle_s2t),
		CommandHandler("t2s", handle_t2s),
		InlineQueryHandler(handle_inline_respond),
		MessageHandler(Filters.command & ~Filters.update.edited_message, handle_cmd)
	]
	
	for ent in handlers:
		dispatcher.add_handler(ent)
	updater.start_polling()
	updater.idle()


if __name__ == '__main__':
	main()
