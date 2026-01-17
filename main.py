import sys
from base64 import b64encode
import urllib.parse
import signal
import platform

import logging

import asyncio
from shortuuid import uuid
from deluge_client import DelugeRPCClient
from mongoengine import *  # type: ignore
from datetime import datetime, timedelta

from typing import Callable, Any, Optional, Tuple
from collections.abc import Coroutine

from telegram import MessageEntity, Update
from telegram.ext import (
    ContextTypes,
    ApplicationBuilder,
    CommandHandler,
    filters,
    MessageHandler,
    Application,
    Job,
)
from telegram.error import TelegramError

from ptbcontrib.ptb_jobstores.mongodb import PTBMongoDBJobStore
from mongopersistence import MongoPersistence

from reminder import subscribe_to_events
from reminder.birthday import (
    register_handlers as register_birthday_handlers,
    reconcile as reconcile_birthdays,
)
from settings import Settings

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)


async def donwload_torrent_by_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download torrent by link with deluge"""
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
    link = text[command_entity.length + 1 :]

    torrent_urls = [link]
    # TODO:
    # for entity in update.effective_message.entities:
    #   print(entity)
    #   if entity.type == MessageEntity.URL or entity.type == MessageEntity.TEXT_LINK:
    #     entity_text = update.message.text[entity.offset : entity.offset + entity.length]
    #     print(entity_text)
    #     torrent_urls.append(entity_text)

    logging.info(f"Add {len(torrent_urls)} torrents")

    with DelugeRPCClient(
        settings.deluge_addr,
        settings.deluge_port,
        settings.deluge_username.get_secret_value(),
        settings.deluge_password.get_secret_value(),
    ) as client:
        options = {"add_paused": False, "auto_managed": True}
        for torrent_url in torrent_urls:
            torrent_name = torrent_url

            try:
                if torrent_url.startswith("magnet"):
                    logging.info(f"Add magnet-link torrent: {torrent_url}")
                    client.core.add_torrent_magnet(uri=torrent_url, options=options)
                    torrent_name = __parse_magnet_link(torrent_url)["dn"]
                else:
                    logging.info(f"Add link torrent: {torrent_url}")
                    client.core.add_torrent_url(url=torrent_url, options=options)
            except:
                await update.effective_message.reply_text(
                    f"Hе удалось добавить торрент {torrent_name}"
                )
                continue

            await update.effective_message.reply_text(
                f"Торрент {torrent_name} добавлен"
            )


async def download_torrent_by_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download torrent by file with deluge"""
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
        await update.effective_message.reply_text(
            f"Прикреплённый торрент файл не найден"
        )
        return

    logging.info(f"Got document: {doc.file_name}")

    try:
        file = await context.bot.get_file(doc)
        file_content = await file.download_as_bytearray()
        file_dump = b64encode(file_content)
    except:
        await update.effective_message.reply_text(f"Ошибка обработки торрент файла")
        return

    with DelugeRPCClient(
        settings.deluge_addr,
        settings.deluge_port,
        settings.deluge_username.get_secret_value(),
        settings.deluge_password.get_secret_value(),
    ) as client:
        options = {"add_paused": False, "auto_managed": True}
        try:
            logging.info(f"Add torrent file: {doc.file_name}")
            client.core.add_torrent_file(doc.file_name, file_dump, options)
        except:
            await update.effective_message.reply_text(
                f"Hе удалось добавить торрент {doc.file_name}"
            )

        await update.effective_message.reply_text(f"Торрент {doc.file_name} добавлен")


async def list_torrents(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List torrents with statuses"""
    if update.effective_user is None:
        logging.error("Effective user doesn't exist")
        return

    if update.effective_message is None:
        logging.error("Effective message doesn't exist")
        return

    torrents = {}
    try:
        with DelugeRPCClient(
            settings.deluge_addr,
            settings.deluge_port,
            settings.deluge_username.get_secret_value(),
            settings.deluge_password.get_secret_value(),
        ) as client:
            torrents = client.core.get_torrents_status({}, [])
    except:
        await update.effective_message.reply_text(
            f"Не удалось получить список торрентов"
        )

    reply = "\n".join(
        [
            f'{torrent[b"name"].decode('utf-8')} -> {torrent[b"state"].decode(
      'utf-8')} [{torrent[b"progress"]:.2f} %]'
            for torrent in torrents.values()
        ]
    )
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
    parsed_components = {
        key: value[0] if len(value) == 1 else value
        for key, value in query_params.items()
    }
    return parsed_components


def run_polling(
    application: Application,
    post_start: Optional[Callable[..., Coroutine[Any, Any, None]]] = None,
):
    if not application.updater:
        raise RuntimeError(
            "Application.run_polling is only available if the application has an Updater."
        )

    def error_callback(exc: TelegramError) -> None:
        application.create_task(application.process_error(error=exc, update=None))

    bootstrap_retries: int = 0
    updater_coroutine = application.updater.start_polling(
        poll_interval=0.0,
        timeout=timedelta(seconds=10),
        bootstrap_retries=bootstrap_retries,
        allowed_updates=None,
        drop_pending_updates=None,
        error_callback=error_callback,  # if there is an error in fetching updates
    )
    stop_signals = None
    close_loop: bool = True

    # Calling get_event_loop() should still be okay even in py3.10+ as long as there is a
    # running event loop, or we are in the main thread, which are the intended use cases.
    # See the docs of get_event_loop() and get_running_loop() for more info
    loop = asyncio.get_event_loop()

    if stop_signals is None and platform.system() != "Windows":
        stop_signals = (signal.SIGINT, signal.SIGTERM, signal.SIGABRT)

    try:
        if stop_signals != None:
            for sig in stop_signals or []:
                loop.add_signal_handler(sig, application._raise_system_exit)
    except NotImplementedError as exc:
        logging.warn(
            f"Could not add signal handlers for the stop signals {
            stop_signals} due to "
            f"exception `{
            exc!r}`. If your event loop does not implement `add_signal_handler`,"
            " please pass `stop_signals=None`.",
            stacklevel=3,
        )

    try:
        loop.run_until_complete(
            application._bootstrap_initialize(max_retries=bootstrap_retries)
        )
        if application.post_init:
            loop.run_until_complete(application.post_init(application))

        # one of updater.start_webhook/polling
        loop.run_until_complete(updater_coroutine)
        loop.run_until_complete(application.start())
        if post_start:
            loop.run_until_complete(post_start(application))

        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        logging.debug("Application received stop signal. Shutting down.")
    finally:
        # We arrive here either by catching the exceptions above or if the loop gets stopped
        # In case the coroutine wasn't awaited, we don't need to bother the user with a warning
        updater_coroutine.close()

        try:
            # Mypy doesn't know that we already check if updater is None
            if application.updater.running:  # type: ignore[union-attr]
                # type: ignore[union-attr]
                loop.run_until_complete(application.updater.stop())
            if application.running:
                loop.run_until_complete(application.stop())
                # post_stop should be called only if stop was called!
                if application.post_stop:
                    loop.run_until_complete(application.post_stop(application))
            loop.run_until_complete(application.shutdown())
            if application.post_shutdown:
                loop.run_until_complete(application.post_shutdown(application))
        finally:
            if close_loop:
                loop.close()


if __name__ == "__main__":
    settings = Settings()  # type: ignore [call-arg]

    connect(host=settings.mongo_url.get_secret_value())

    persistence = MongoPersistence(
        mongo_url=settings.mongo_url.get_secret_value(),
        db_name="bot_persistence",
        create_col_if_not_exist=True,  # optional
        name_col_user_data="user-data",  # optional
        name_col_chat_data="chat-data",  # optional
        name_col_bot_data="bot-data",  # optional
        name_col_conversations_data="conversations",  # optional
        ignore_general_data=["cache"],
        ignore_user_data=["foo", "bar"],
        load_on_flush=False,
        update_interval=60,
    )

    application = (
        ApplicationBuilder()
        .token(settings.bot_token.get_secret_value())
        .persistence(persistence=persistence)
        .build()
    )

    if application.job_queue is None:
        logging.error("Job queue doesn't exist")
        sys.exit(1)

    application.job_queue.scheduler.add_jobstore(
        PTBMongoDBJobStore(
            application=application, host=settings.mongo_url.get_secret_value()
        )
    )

    subscribe_to_events(application)

    download_torrent_by_file_handler = MessageHandler(
        filters.Document.MimeType("application/x-bittorrent"), download_torrent_by_file
    )

    download_torrent_by_link_handler = CommandHandler(
        "download", donwload_torrent_by_link
    )

    list_torrents_handler = CommandHandler("list", list_torrents)

    application.add_handler(download_torrent_by_file_handler)
    application.add_handler(download_torrent_by_link_handler)
    application.add_handler(list_torrents_handler)

    application.add_handler(annual_event_handler)

    register_birthday_handlers(application)

    async def post_start(application: Application):
        print("Run post start")

        if application.job_queue is None:
            return

        reconcile_birthdays(application)

    run_polling(application, post_start)
