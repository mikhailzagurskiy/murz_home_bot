from base64 import b64encode
import urllib.parse

import logging
from deluge_client import DelugeRPCClient

from telegram import MessageEntity, Update
from telegram.ext import ContextTypes, ApplicationBuilder, CommandHandler, filters, MessageHandler

from settings import Settings

logging.basicConfig(
  format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
  level=logging.INFO
)

async def donwload_torrent_by_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
  '''Download torrent by link with deluge'''
  if update.effective_user is None:
    logging.error("Effective user doesn't exist")
    return
  
  if update.effective_message is None or update.message is None:
    logging.error("Effective message doesn't exist")
    return
  
  if update.effective_message.text is None or update.message.text is None:
    logging.error("Effective message text doesn't exist")
    return

  command_entity = update.effective_message.entities[0]
  if command_entity.type != MessageEntity.BOT_COMMAND:
    logging.error("Message without command is not allowed")
    return
  
  text = urllib.parse.unquote(update.effective_message.text)
  link = text[command_entity.length + 1:]

  torrent_urls = [link]

  logging.info(f'Add {len(torrent_urls)} torrents')

  with DelugeRPCClient(settings.deluge_addr, settings.deluge_port, settings.deluge_username.get_secret_value(), settings.deluge_password.get_secret_value()) as client:
    options = {'add_paused': False, 'auto_managed': True}
    for torrent_url in torrent_urls:
      torrent_name = torrent_url

      try:
        if torrent_url.startswith('magnet'):
          logging.info(f'Add magnet-link torrent: {torrent_url}')
          client.core.add_torrent_magnet(uri=torrent_url, options=options)
          torrent_name = __parse_magnet_link(torrent_url)['dn']
        else:
          logging.info(f'Add link torrent: {torrent_url}')
          client.core.add_torrent_url(url=torrent_url, options=options)
      except:
        await update.effective_message.reply_text(f'Hе удалось добавить торрент {torrent_name}')
        continue

      await update.effective_message.reply_text(f'Торрент {torrent_name} добавлен')



async def download_torrent_by_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
  '''Download torrent by file with deluge'''
  if update.effective_user is None:
    logging.error("Effective user doesn't exist")
    return
  
  if update.effective_message is None:
    logging.error("Effective message doesn't exist")
    return
  
  if update.effective_message.document is None:
    logging.error("Effective message document doesn't exist")
    return

  doc = update.effective_message.document
  if doc is None:
    await update.effective_message.reply_text(f'Прикреплённый торрент файл не найден')
    return

  logging.info(f'Got document: {doc.file_name}')

  try:
    file = await context.bot.get_file(doc)
    file_content = await file.download_as_bytearray()
    file_dump = b64encode(file_content)
  except:
    await update.effective_message.reply_text(f'Ошибка обработки торрент файла')
    return

  with DelugeRPCClient(settings.deluge_addr, settings.deluge_port, settings.deluge_username.get_secret_value(), settings.deluge_password.get_secret_value()) as client:
    options = {'add_paused': False, 'auto_managed': True}
    try:
      logging.info(f'Add torrent file: {doc.file_name}')
      client.core.add_torrent_file(doc.file_name, file_dump, options)
    except:
        await update.effective_message.reply_text(f'Hе удалось добавить торрент {doc.file_name}')

    await update.effective_message.reply_text(f'Торрент {doc.file_name} добавлен')

async def list_torrents(update: Update, context: ContextTypes.DEFAULT_TYPE):
  '''List torrents with statuses'''
  if update.effective_user is None:
    logging.error("Effective user doesn't exist")
    return
  
  if update.effective_message is None:
    logging.error("Effective message doesn't exist")
    return

  torrents = {}
  try:
    with DelugeRPCClient(settings.deluge_addr, settings.deluge_port, settings.deluge_username.get_secret_value(), settings.deluge_password.get_secret_value()) as client:
      torrents = client.core.get_torrents_status({}, [])
  except:
    await update.effective_message.reply_text(f'Не удалось получить список торрентов')

  reply = "\n".join([f'{torrent[b"name"].decode('utf-8')} -> {torrent[b"state"].decode('utf-8')} [{torrent[b"progress"]:.2f} %]' for torrent in torrents.values()])
  await update.effective_message.reply_text(reply)

def __parse_magnet_link(magnet_link):
    """
    Parses a magnet link and extracts its components.

    Args:
        magnet_link (str): The magnet link string.

    Returns:
        dict: A dictionary containing the parsed components.
              Keys include 'xt', 'dn', 'tr', etc.
    """
    parsed_url = urllib.parse.urlparse(magnet_link)
    query_params = urllib.parse.parse_qs(parsed_url.query)

    # Convert lists of single values to single values
    parsed_components = {key: value[0] if len(value) == 1 else value
                         for key, value in query_params.items()}
    return parsed_components

if __name__ == '__main__':
  settings = Settings()

  application = (
    ApplicationBuilder()
      .token(settings.bot_token.get_secret_value())
      .build()
  )

  download_torrent_by_link_handler = CommandHandler('download', donwload_torrent_by_link)
  list_torrents_handler = CommandHandler('list', list_torrents)
  download_torrent_by_file_handler = MessageHandler(filters.Document.MimeType('application/x-bittorrent'), download_torrent_by_file)

  application.add_handler(download_torrent_by_link_handler)
  application.add_handler(download_torrent_by_file_handler)
  application.add_handler(list_torrents_handler)

  application.run_polling()
