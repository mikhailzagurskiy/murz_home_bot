import logging

import re
from shortuuid import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from typing import Dict, Optional, Tuple, cast

from telegram import Update
from telegram.ext import ContextTypes, Application, CommandHandler, Job

from user import UNKNOWN_USER_MSG
from user.model import User, UserStatus

from reminder.model import Event, EventState, EventStatus, EventType
from reminder import (
    UNABLE_CREATE_EVENT_MSG,
    UNABLE_DELETE_EVENT_MSG,
    UNABLE_PARSE_EVENT_ID_MSG,
    UNABLE_PAUSE_EVENT_MSG,
    UNABLE_RESUME_EVENT_MSG,
    UNABLE_SCHEDULE_REMINDER_MSG,
)

__birhtday_pattern = re.compile(
    r"""\s+(?P<day>\d{1,2}).(?P<month>\d{1,2})(.(?P<year>\d{4}|\d{2}))?\s+(?P<name>[\s\w]+)""",
    flags=re.IGNORECASE | re.VERBOSE,
)

__default_hour = 9
__default_zone = ZoneInfo("Europe/Kaliningrad")


class __JobDescriptor(object):
    def __init__(self, text: str, event_id):
        self.text = text
        self.event_id = event_id


def register_handlers(application: Application):
    create_handler = CommandHandler("create_birthday", create)
    list_handler = CommandHandler("list_birthdays", list)
    delete_handler = CommandHandler("delete_birthday", delete)
    enable_handler = CommandHandler("enable_birthday", enable)
    disable_handler = CommandHandler("disable_birthday", disable)

    application.add_handler(create_handler)
    application.add_handler(list_handler)
    application.add_handler(delete_handler)
    application.add_handler(enable_handler)
    application.add_handler(disable_handler)


def reconcile(application: Application):
    assert application.job_queue is not None

    birthdays = Event.objects(typ=EventType.BIRTHDAY)  # type: ignore
    for b in birthdays:
        if b.state == EventState.DISABLED:
            job = application.job_queue.scheduler.get_job(b.job_id)
            if job != None:
                application.job_queue.scheduler.pause_job(b.job_id)

        else:
            if b.status == EventStatus.EXPIRED:
                job = application.job_queue.scheduler.get_job(b.job_id)
                if job != None:
                    application.job_queue.scheduler.remove_job(b.job_id)

            elif b.status == EventStatus.SCHEDULED or b.status == EventStatus.CREATED:
                job = application.job_queue.scheduler.get_job(b.job_id)
                if job == None:
                    # TODO: Create job!
                    pass


async def create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.effective_user is not None
    assert update.effective_message is not None
    assert context.job_queue is not None
    assert update.effective_message.text is not None

    try:
        chunks = __parse(update.effective_message.text)
    except Exception as e:
        logging.error(f"Unable to parse birthday: {e}")
        return

    try:
        year = __schedule_in_year(chunks["day"], chunks["month"])
    except Exception as e:
        logging.error(f"Unable to get birthday year: {e}")
        return

    day = chunks["day"]
    month = chunks["month"]

    date = datetime(year, month, day, hour=__default_hour, tzinfo=__default_zone)

    job_id = uuid()
    chat_id = update.effective_message.chat_id
    user_id = update.effective_user.id
    name = f"Birthday of {chunks["name"]}"
    text = f"День рождения у {chunks["name"]}"

    try:
        user = User.objects.get(user_id=user_id)  # type: ignore
    except Exception:
        logging.error("Unknown user")
        await update.effective_message.reply_text(UNKNOWN_USER_MSG)
        return

    try:
        event = Event(
            name=name,
            text=text,
            created_by=user,
            addressed_to=user,
            state=EventState.ENABLED,
            status=EventStatus.CREATED,
            typ=EventType.BIRTHDAY,
            scheduled_to=date,
            job_id=job_id,
        ).save()
    except Exception as e:
        logging.error(f"Unable to create event: {e}")
        await update.effective_message.reply_text(UNABLE_CREATE_EVENT_MSG)
        return

    job_data = __JobDescriptor(text, event.id)

    try:
        job = context.job_queue.run_once(
            __cb,
            date,
            chat_id=chat_id,
            name=name,
            data=job_data,
            job_kwargs={"id": job_id},
        )
    except Exception as e:
        logging.error(f"Unable to schedule reminder: {e}")
        await update.effective_message.reply_text(UNABLE_SCHEDULE_REMINDER_MSG)
        event.delete()
        return

    await update.effective_message.reply_text(
        f"Напоминание про {event.name} для {user.username} создано на {job.job.next_run_time}"
    )


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.effective_user is not None
    assert update.effective_message is not None
    assert context.job_queue is not None
    assert update.effective_message.text is not None

    try:
        _cmd, id, *_remains = update.effective_message.text.split(" ")
    except Exception as e:
        logging.error(f"Unable to parse event id: {e}")
        await update.effective_message.reply_text(UNABLE_PARSE_EVENT_ID_MSG)
        return

    birthday_repr = ""
    job_id = None
    try:
        birthday = Event.objects.get(id=id)  # type: ignore
        birthday_repr = f"{birthday.name} for {birthday.addressed_to.username} by {birthday.created_by.username} at {birthday.scheduled_to} ({birthday.id})"
        job_id = birthday.job_id
        birthday.delete()
    except Exception as e:
        logging.error(f"Unable to delete event: {e}")
        await update.effective_message.reply_text(UNABLE_DELETE_EVENT_MSG)
        return
    finally:
        try:
            if job_id != None and context.job_queue.scheduler.get_job(job_id) != None:
                context.job_queue.scheduler.remove_job(job_id)
        except Exception as e:
            logging.error(f"Unable to delete job: {e}")
            await update.effective_message.reply_text(UNABLE_DELETE_EVENT_MSG)
            return

    await update.effective_message.reply_text(f"Удалено напоминание {birthday_repr}")


async def list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.effective_user is not None
    assert update.effective_message is not None
    assert context.job_queue is not None
    assert update.effective_message.text is not None

    birthdays = Event.objects(typ=EventType.BIRTHDAY)  # type: ignore
    birthdays = [
        f"{idx}. {b.name} for {b.addressed_to.username} by {b.created_by.username} at {b.scheduled_to} ({b.id})"
        for idx, b in enumerate(birthdays)
    ]

    msg = "\n".join(birthdays) if len(birthdays) else "No birthdays"

    await update.effective_message.reply_text(msg)


async def enable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.effective_user is not None
    assert update.effective_message is not None
    assert context.job_queue is not None
    assert update.effective_message.text is not None

    try:
        _cmd, id, *_remains = update.effective_message.text.split(" ")
    except Exception as e:
        logging.error(f"Unable to parse event id: {e}")
        await update.effective_message.reply_text(UNABLE_PARSE_EVENT_ID_MSG)
        return

    Event.objects(id=id).update_one(state=EventState.ENABLED)  # type: ignore
    birthday = Event.objects.get(id=id)  # type: ignore

    try:
        if (
            birthday.job_id != None
            and context.job_queue.scheduler.get_job(birthday.job_id) != None
        ):
            context.job_queue.scheduler.resume_job(birthday.job_id)
    except Exception as e:
        logging.error(f"Unable to resume job: {e}")
        await update.effective_message.reply_text(UNABLE_RESUME_EVENT_MSG)
        return

    await update.effective_message.reply_text(f"Напоминания о дне рождения включены")


async def disable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.effective_user is not None
    assert update.effective_message is not None
    assert context.job_queue is not None
    assert update.effective_message.text is not None

    try:
        _cmd, id, *_remains = update.effective_message.text.split(" ")
    except Exception as e:
        logging.error(f"Unable to parse event id: {e}")
        await update.effective_message.reply_text(UNABLE_PARSE_EVENT_ID_MSG)
        return

    Event.objects(id=id).update_one(state=EventState.DISABLED)  # type: ignore
    birthday = Event.objects.get(id=id)  # type: ignore

    try:
        if (
            birthday.job_id != None
            and context.job_queue.scheduler.get_job(birthday.job_id) != None
        ):
            context.job_queue.scheduler.pause_job(birthday.job_id)
    except Exception as e:
        logging.error(f"Unable to pause job: {e}")
        await update.effective_message.reply_text(UNABLE_PAUSE_EVENT_MSG)
        return

    await update.effective_message.reply_text(f"Напоминания о дне рождения выключены")


async def __cb(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job

    assert job is not None
    assert job.chat_id is not None
    assert job.data is not None
    assert context.job_queue is not None

    data = cast(__JobDescriptor, job.data)

    birthday = Event.objects.get(id=data.event_id)  # type: ignore

    if birthday.state == EventState.DISABLED:
        logging.warn(f"Unable to execute disabled Event with id {data.event_id}")
        return

    job_id = uuid()
    date = birthday.scheduled_to.replace(year=birthday.scheduled_to.year + 1)
    chat_id = job.chat_id
    name = job.name
    text = data.text

    await context.bot.send_message(job.chat_id, text=f"Напоминаю! {data.text} !")

    try:
        job = context.job_queue.run_once(
            __cb,
            date,
            chat_id=chat_id,
            name=name,
            data=data,
            job_kwargs={"id": job_id},
        )
    except Exception as e:
        logging.error(f"Unable to schedule reminder: {e}")
        await context.bot.send_message(chat_id, UNABLE_SCHEDULE_REMINDER_MSG)
        return

    birthday.scheduled_to = date
    birthday.job_id = job_id
    birthday.status = EventStatus.SCHEDULED
    birthday.save()


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

    if chunks["year"] != None:
        chunks["year"] = __convert_year(int(chunks["year"]))

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
