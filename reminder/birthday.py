import logging

import math
import re
from enum import Enum, auto

import pandas
from uuid import UUID
from shortuuid import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from typing import Dict, Optional, Tuple, cast

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    Application,
    CommandHandler,
    JobQueue,
    Job,
    ConversationHandler,
)

from user import UNKNOWN_USER_MSG
from user.model import User, UserStatus
from common import trim_str_by_len

from reminder.recurrence import EveryYear
from reminder.model import Birthday, EventState, EventStatus
from reminder import (
    UNABLE_CREATE_EVENT_MSG,
    UNABLE_DELETE_EVENT_MSG,
    UNABLE_PARSE_EVENT_ID_MSG,
    UNABLE_PAUSE_EVENT_MSG,
    UNABLE_RESUME_EVENT_MSG,
    UNABLE_SCHEDULE_REMINDER_MSG,
)

logger = logging.getLogger(__name__)

__birhtday_pattern = re.compile(
    r"""\s+(?P<day>\d{1,2}).(?P<month>\d{1,2})(.(?P<year>\d{4}|\d{2}))?\s+(?P<person>[\s\w]+)""",
    flags=re.IGNORECASE | re.VERBOSE,
)

__default_hour = 9
__default_zone = ZoneInfo("Europe/Kaliningrad")

__page_size = 5


class __JobDescriptor(object):
    def __init__(self, text: str, event_id):
        self.text = text
        self.event_id = event_id


class State(Enum):
    """
    States of the conversation
    """

    LISTING = auto()
    SUMMARY = auto()


class Action(Enum):
    """
    Actions, available during the conversation
    """

    CREATE = auto()
    DELETE = auto()
    ENABLE = auto()
    DISABLE = auto()
    SUMMARY = auto()
    LIST = auto()
    NEXT = auto()
    PREV = auto()
    FIRST = auto()
    LAST = auto()


def register_handlers(application: Application):
    create_handler = CommandHandler("create_birthday", create)

    conversation_handler = ConversationHandler(
        entry_points=[CommandHandler("birthday", start_conversation)],
        states={
            State.LISTING: [
                CallbackQueryHandler(
                    start_conversation, pattern="^" + str(Action.NEXT) + "$"
                ),
                CallbackQueryHandler(
                    start_conversation, pattern="^" + str(Action.PREV) + "$"
                ),
                CallbackQueryHandler(
                    start_conversation, pattern="^" + str(Action.FIRST) + "$"
                ),
                CallbackQueryHandler(
                    start_conversation, pattern="^" + str(Action.LAST) + "$"
                ),
                CallbackQueryHandler(
                    stop_conversation, pattern="^" + str(ConversationHandler.END) + "$"
                ),
                CallbackQueryHandler(
                    summary,
                    pattern=r"^" + str(Action.SUMMARY) + r"_(?P<id>[0-9A-Fa-f]{24})$",
                ),
            ],
            State.SUMMARY: [
                CallbackQueryHandler(
                    enable,
                    pattern="^" + str(Action.ENABLE) + r"_(?P<id>[0-9A-Fa-f]{24})$",
                ),
                CallbackQueryHandler(
                    disable,
                    pattern="^" + str(Action.DISABLE) + r"_(?P<id>[0-9A-Fa-f]{24})$",
                ),
                CallbackQueryHandler(
                    delete,
                    pattern="^" + str(Action.DELETE) + r"_(?P<id>[0-9A-Fa-f]{24})$",
                ),
                CallbackQueryHandler(
                    start_conversation, pattern="^" + str(Action.LIST) + "$"
                ),
                CallbackQueryHandler(
                    stop_conversation, pattern="^" + str(ConversationHandler.END) + "$"
                ),
            ],
        },
        fallbacks=[],
    )

    application.add_handler(create_handler)

    application.add_handler(conversation_handler)


def reconcile(application: Application):
    logger.debug("Reconcile birthdays")
    assert application.job_queue is not None

    birthdays = Birthday.objects()  # type: ignore
    for b in birthdays:
        logger.trace(f"Reconcile {b.title}")
        if b.state == EventState.DISABLED:
            job = application.job_queue.scheduler.get_job(b.job_id)
            if b.job_id != None and job != None:
                logger.debug(f"Pause job {b.job_id} for disabled {b.title}")
                application.job_queue.scheduler.pause_job(b.job_id)

        else:
            if b.status == EventStatus.EXPIRED:
                job = application.job_queue.scheduler.get_job(b.job_id)
                if b.job_id != None and job != None:
                    logger.debug(f"Remove job {b.job_id} for expired {b.title}")
                    application.job_queue.scheduler.remove_job(b.job_id)

            elif b.status == EventStatus.SCHEDULED or b.status == EventStatus.CREATED:
                if b.job_id == None:
                    b.job_id = uuid()
                    b.save()

                job = application.job_queue.scheduler.get_job(b.job_id)
                if job == None:
                    logger.debug(f"Schedule job {b.job_id} for {b.title}")
                    __schedule_new_job(application.job_queue, b)
                else:
                    logger.debug(f"Resume job {b.job_id} for {b.title}")
                    application.job_queue.scheduler.resume_job(b.job_id)


async def start_conversation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> State:
    assert update.effective_message is not None
    assert context.user_data is not None

    context.user_data.pop("changes")

    total_pages = math.ceil(Birthday.objects.count() / __page_size)

    page = context.user_data["page"]

    if update.callback_query == None:
        page = 1
    elif update.callback_query.data == str(Action.NEXT) and page < total_pages:
        page += 1
    elif update.callback_query.data == str(Action.PREV) and page > 1:
        page -= 1
    elif update.callback_query.data == str(Action.FIRST):
        page = 1
    elif update.callback_query.data == str(Action.LAST):
        page = total_pages
    else:
        pass

    birthdays = []
    while True:
        offset = (page - 1) * __page_size
        birthdays = Birthday.objects().order_by("created_at").skip(offset).limit(__page_size)  # type: ignore

        # Go to prev page, if all elements from current page was deleted
        if len(birthdays) == 0 and page > 1:
            page -= 1
        else:
            break

    rows = []
    for birthday in birthdays:
        bday = birthday.to_mongo().to_dict()
        row = {
            "idx": (offset + len(rows) + 1),
            "person": trim_str_by_len(bday["person"], 20),
            "date": bday["recurrence"]["date"].strftime("%d %B (%d.%m)"),
            "state": bday["state"],
            "status": bday["status"],
        }
        rows.append(row)

    df = pandas.DataFrame(rows)

    markdown_table = df.to_markdown(index=False)

    line_len = markdown_table.find("\n")
    horizontal_line = f"|{"-" * (line_len - 2)}|"
    page_str = f"Page {page} / {total_pages}"
    page_str = f"|{page_str:^{line_len - 2}}|"
    msg = f"```\n{markdown_table}\n{horizontal_line}\n{page_str}```"

    buttons = [
        [
            InlineKeyboardButton(
                text=str(row["idx"]),
                callback_data=f"{str(Action.SUMMARY)}_{birthday.id}",
            )
            for (row, birthday) in zip(rows, birthdays)
        ],
        [
            InlineKeyboardButton(text="◀️", callback_data=str(Action.PREV)),
            InlineKeyboardButton(text="▶️", callback_data=str(Action.NEXT)),
        ],
        [
            InlineKeyboardButton(text="⏪", callback_data=str(Action.FIRST)),
            InlineKeyboardButton(text="⏩", callback_data=str(Action.LAST)),
        ],
        [
            InlineKeyboardButton(text="⏹️", callback_data=str(ConversationHandler.END)),
        ],
    ]
    keyboard = InlineKeyboardMarkup(buttons)

    if update.callback_query == None:
        await update.effective_message.reply_text(
            text=msg, reply_markup=keyboard, parse_mode="MarkdownV2"
        )
    else:
        try:
            await update.effective_message.edit_text(
                text=msg, reply_markup=keyboard, parse_mode="MarkdownV2"
            )
        except Exception as e:
            logger.error(e)

    context.user_data["page"] = page

    return State.LISTING


async def stop_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """End conversation from InlineKeyboardButton."""
    assert update.callback_query is not None

    await update.callback_query.answer()

    await update.callback_query.delete_message()

    return ConversationHandler.END


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    assert context.matches is not None
    assert len(context.matches) > 0
    assert update.effective_message is not None
    assert context.user_data is not None

    id = context.matches[0].group("id")

    context.user_data["changes"] = context.user_data.get("changes", [])[-5:]

    try:
        birthday = Birthday.objects.get(id=id)  # type: ignore
    except Exception as e:
        logger.error(f"Unable to get birthday summary: {e}")
        return State.LISTING

    data = list(birthday.to_mongo().to_dict().items())
    df = pandas.DataFrame(data)
    markdown_table = df.to_markdown(index=False)
    line_len = markdown_table.find("\n")
    horizontal_line = f"|{"-" * (line_len - 2)}|"
    msg = f"```\n{markdown_table}\n{horizontal_line}\n{"\n".join(context.user_data["changes"])}```"

    buttons = [
        [
            InlineKeyboardButton(
                text="✔️", callback_data=f"{str(Action.ENABLE)}_{birthday.id}"
            ),
            InlineKeyboardButton(
                text="❌", callback_data=f"{str(Action.DISABLE)}_{birthday.id}"
            ),
            InlineKeyboardButton(
                text="🗑️", callback_data=f"{str(Action.DELETE)}_{birthday.id}"
            ),
        ],
        [
            InlineKeyboardButton(text="↩️", callback_data=str(Action.LIST)),
            InlineKeyboardButton(text="⏹️", callback_data=str(ConversationHandler.END)),
        ],
    ]
    keyboard = InlineKeyboardMarkup(buttons)

    try:
        await update.effective_message.edit_text(
            text=msg, reply_markup=keyboard, parse_mode="MarkdownV2"
        )
    except Exception as e:
        logger.error(e)

    return State.SUMMARY


async def create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.effective_user is not None
    assert update.effective_message is not None
    assert context.job_queue is not None
    assert update.effective_message.text is not None

    try:
        chunks = __parse(update.effective_message.text)
    except Exception as e:
        logger.error(f"Unable to parse birthday: {e}")
        return

    day = chunks["day"]
    month = chunks["month"]
    year = chunks["year"]

    birthday_date = datetime(
        year, month, day, hour=__default_hour, tzinfo=__default_zone
    )

    job_id = uuid()
    chat_id = update.effective_message.chat_id
    user_id = update.effective_user.id
    title = f"Birthday of {chunks["person"]}"
    text = f"День рождения у {chunks["person"]}"

    try:
        user = User.objects.get(user_id=user_id)  # type: ignore
    except Exception:
        logger.error("Unknown user")
        await update.effective_message.reply_text(UNKNOWN_USER_MSG)
        return

    try:
        birthday = Birthday(
            title=title,
            text=text,
            created_by=user,
            addressed_to=user,
            state=EventState.ENABLED,
            status=EventStatus.CREATED,
            job_id=job_id,
            recurrence=EveryYear(date=birthday_date),
            person=chunks["person"],
        ).save()
    except Exception as e:
        logger.error(f"Unable to create event: {e}")
        await update.effective_message.reply_text(UNABLE_CREATE_EVENT_MSG)
        return

    try:
        job = __schedule_new_job(context.job_queue, birthday)
    except Exception as e:
        logger.error(f"Unable to schedule reminder: {e}")
        await update.effective_message.reply_text(UNABLE_SCHEDULE_REMINDER_MSG)
        birthday.delete()
        return

    await update.effective_message.reply_text(
        f"Напоминание про {birthday.title} для {user.username} создано на {job.job.next_run_time}"
    )


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    assert update.effective_user is not None
    assert update.effective_message is not None
    assert context.job_queue is not None
    assert update.effective_message.text is not None
    assert context.matches is not None
    assert len(context.matches) > 0
    assert context.user_data is not None

    id = context.matches[0].group("id")

    job_id = None
    try:
        birthday = Birthday.objects.get(id=id)  # type: ignore
        job_id = birthday.job_id
        birthday.delete()
    except Exception as e:
        logger.error(f"Unable to delete event: {e}")
        await update.effective_message.reply_text(UNABLE_DELETE_EVENT_MSG)
        return State.SUMMARY
    finally:
        try:
            if job_id != None and context.job_queue.scheduler.get_job(job_id) != None:
                context.job_queue.scheduler.remove_job(job_id)
        except Exception as e:
            logger.error(f"Unable to delete job: {e}")
            await update.effective_message.reply_text(UNABLE_DELETE_EVENT_MSG)
            return State.SUMMARY

    context.user_data["changes"].append(f"Delete {birthday.title}")

    return await start_conversation(update, context)


async def enable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    assert update.effective_user is not None
    assert update.effective_message is not None
    assert context.job_queue is not None
    assert update.effective_message.text is not None
    assert context.matches is not None
    assert len(context.matches) > 0
    assert context.user_data is not None

    id = context.matches[0].group("id")

    Birthday.objects(id=id).update_one(state=EventState.ENABLED)  # type: ignore
    birthday = Birthday.objects.get(id=id)  # type: ignore

    try:
        if (
            birthday.job_id != None
            and context.job_queue.scheduler.get_job(birthday.job_id) != None
        ):
            context.job_queue.scheduler.resume_job(birthday.job_id)
    except Exception as e:
        logger.error(f"Unable to resume job: {e}")
        await update.effective_message.reply_text(UNABLE_RESUME_EVENT_MSG)
        return State.SUMMARY

    context.user_data["changes"].append(f"Enable {birthday.title}")

    return await summary(update, context)


async def disable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    assert update.effective_user is not None
    assert update.effective_message is not None
    assert context.job_queue is not None
    assert update.effective_message.text is not None
    assert context.matches is not None
    assert len(context.matches) > 0
    assert context.user_data is not None

    id = context.matches[0].group("id")

    Birthday.objects(id=id).update_one(state=EventState.DISABLED)  # type: ignore
    birthday = Birthday.objects.get(id=id)  # type: ignore

    try:
        if (
            birthday.job_id != None
            and context.job_queue.scheduler.get_job(birthday.job_id) != None
        ):
            context.job_queue.scheduler.pause_job(birthday.job_id)
    except Exception as e:
        logger.error(f"Unable to pause job: {e}")
        await update.effective_message.reply_text(UNABLE_PAUSE_EVENT_MSG)
        return State.SUMMARY

    context.user_data["changes"].append(f"Disable {birthday.title}")

    return await summary(update, context)


async def __cb(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job

    assert job is not None
    assert job.chat_id is not None
    assert job.data is not None
    assert context.job_queue is not None

    data = cast(__JobDescriptor, job.data)

    birthday = Birthday.objects.get(id=data.event_id)  # type: ignore

    if birthday.state == EventState.DISABLED:
        logger.warn(f"Unable to execute disabled Event with id {data.event_id}")
        return

    date = birthday.recurrence.date
    schedule_year = datetime.now(tz=__default_zone).year + 1
    scheduled_date = datetime(
        schedule_year,
        date.month,
        date.day,
        hour=__default_hour,
        tzinfo=__default_zone,
    )

    await context.bot.send_message(job.chat_id, text=f"Напоминаю! {data.text} !")

    try:
        next_job = __schedule_next_job(context.job_queue, job, scheduled_date)

        birthday.job_id = next_job.id
        birthday.status = EventStatus.SCHEDULED
        birthday.save()
    except Exception as e:
        logger.error(f"Unable to schedule reminder: {e}")
        await context.bot.send_message(job.chat_id, UNABLE_SCHEDULE_REMINDER_MSG)
        return


def __schedule_new_job(job_queue: JobQueue, birthday: Birthday) -> Job:

    job_name = f"Job for {birthday.title}"
    data = __JobDescriptor(birthday.text, birthday.id)
    date = birthday.recurrence.date
    chat_id = birthday.addressed_to.chat_id
    job_id = birthday.job_id

    try:
        schedule_year = __schedule_in_year(date.day, date.month)
    except Exception as e:
        logger.error(f"Unable to get birthday year: {e}")
        raise e

    scheduled_date = datetime(
        schedule_year,
        date.month,
        date.day,
        hour=__default_hour,
        tzinfo=__default_zone,
    )

    job = job_queue.run_once(
        __cb,
        scheduled_date,
        chat_id=chat_id,
        name=job_name,
        data=data,
        job_kwargs={"id": job_id},
    )

    return job


def __schedule_next_job(job_queue: JobQueue, job: Job, scheduled_date: datetime) -> Job:

    job_id = uuid()
    chat_id = job.chat_id
    job_name = job.name
    data = cast(__JobDescriptor, job.data)

    job = job_queue.run_once(
        __cb,
        scheduled_date,
        chat_id=chat_id,
        name=job_name,
        data=data,
        job_kwargs={"id": job_id},
    )

    return job


def __schedule_in_year(day: int, month: int) -> int:
    today = datetime.now(tz=__default_zone)

    next_date = datetime(
        today.year, month, day, hour=__default_hour, tzinfo=__default_zone
    )

    year = (
        datetime.now(tz=__default_zone).year
        if (next_date - today).total_seconds() > 0
        else datetime.now(tz=__default_zone).year + 1
    )

    return year


def __parse(text: str) -> Dict:
    match = __birhtday_pattern.search(text)
    if match == None:
        raise SyntaxError("Unable to parse birthday")

    chunks = match.groupdict()
    chunks["day"] = int(chunks["day"])
    chunks["month"] = int(chunks["month"])

    year = int(chunks["year"]) if chunks["year"] != None else None
    chunks["year"] = __convert_year(year)

    return chunks


def __convert_year(year: Optional[int]) -> int:
    cur_year = datetime.now(tz=__default_zone).year

    if year == None:
        return cur_year
    elif year < 100:
        if cur_year - 2000 < year:
            return year + 1900
        else:
            return year + 2000
    else:
        return year
