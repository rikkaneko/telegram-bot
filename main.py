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

import logging
import os
import random
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from opencc import OpenCC
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent, Message
from telegram.ext import Updater, Dispatcher, CallbackContext, CommandHandler, MessageHandler, InlineQueryHandler, Filters

bot_id: str = os.getenv("TG_BOT_ID")
s2tcon = OpenCC("s2hk.json")
t2scon = OpenCC("hk2s.json")
quotes: list[list[str]] = [[], [], []]
cached_inline_queries: dict[int, (str, int)] = { }


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
		text: str = "".join(context.args)
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
		text: str = "".join(context.args)
		if not text:
			return
		
		message_id = update.edited_message.message_id
		reply_text = s2tcon.convert(text)
		context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=context.user_data[message_id], text=reply_text)


def handle_t2s(update: Update, context: CallbackContext):
	# Call with arguments
	if update.message is not None:
		text: str = "".join(context.args)
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
		text: str = "".join(context.args)
		if not text:
			return
		
		message_id = update.edited_message.message_id
		reply_text = t2scon.convert(text)
		context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=context.user_data[message_id], text=reply_text)


# sub_quote_from_query(query_text: str) -> (original_quote, sub_quote, n)
def sub_quote_from_query(sender: int, query_text: str) -> (str, str, int):
	global cached_inline_queries
	query_text = query_text.strip()
	args = list(filter(None, re.split(r"\s+", query_text)))
	n = len(args)
	if n > 2:
		return None, None, n
	
	cache_hit = False
	quote = quotes[n][random.randint(0, len(quotes[n]) - 1)]
	# Check if cached inline query
	if sender in cached_inline_queries:
		cached_quote, arg_count = cached_inline_queries[sender]
		if arg_count == n:
			quote = cached_quote
			cache_hit = True
	
	# Insert/update new cache entry for the current sender
	if not cache_hit:
		cached_inline_queries[sender] = (quote, n)
	
	result = quote
	if n >= 1:
		result = re.sub(r"(o+)", args[0], result)
	if n >= 2:
		result = re.sub(r"(x+)", args[1], result)
	
	return quote, result, n


def handle_inline_respond(update: Update, context: CallbackContext):
	query = update.inline_query.query
	sender = update.inline_query.from_user.id
	original, reply, n = sub_quote_from_query(sender, query)
	result = []
	if n <= 2:
		result.append(
				InlineQueryResultArticle(
						id="quote",
						title=original,
						input_message_content=InputTextMessageContent(reply),
						description=reply
				))
	
	if n > 0:
		s2tresult = s2tcon.convert(query)
		t2sresult = t2scon.convert(query)
		result.append(
				InlineQueryResultArticle(
						id="s2t",
						title="簡轉繁",
						input_message_content=InputTextMessageContent(s2tresult),
						description=s2tresult
				))
		
		result.append(
				InlineQueryResultArticle(
						id="t2s",
						title="繁转简",
						input_message_content=InputTextMessageContent(t2sresult),
						description=t2sresult
				))
	
	context.bot.answer_inline_query(update.inline_query.id, result, cache_time=0)


def build_quote_list(path: str):
	global quotes
	file_path = Path(path)
	content: str = ""
	# Download the file if not exist
	if not file_path.exists():
		response = requests.get("https://zh.moegirl.org.cn/index.php?title=Template:ACG%E7%BB%8F%E5%85%B8%E5%8F%B0%E8%AF%8D&action=edit")
		soup = BeautifulSoup(response.text, "html5lib")
		content = soup.find("textarea", { "id": "wpTextbox1" }).text.strip()
		# Write to file
		with open(file_path, 'w') as f:
			f.write(content)
	
	# Read from existing source
	else:
		with open(file_path, 'r') as f:
			content = f.read()
	
	matches: list[str] = re.findall(r"-->\[\[(.+?)]]", content)
	for idx, quote in enumerate(matches):
		qs = quote.split('|')
		result = qs[0]
		if len(qs) > 1:
			for idxx, s in enumerate(qs):
				if not s and not re.match(r"[/({]", s):
					result = qs[idxx]
					break
		
		if re.search("o{2,}", result) and re.search("x{2,}", result):
			quotes[2].append(s2tcon.convert(result))
		elif re.search("o{2,}", result):
			quotes[1].append(s2tcon.convert(result))
		else:
			quotes[0].append(s2tcon.convert(result))


def main():
	logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
	build_quote_list("./moegirl-acg-words.txt")
	updater: Updater = Updater(token=os.getenv("TG_BOT_API_TOKEN"))
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
