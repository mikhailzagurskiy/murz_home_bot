import logging

import re
from uuid import UUID
from shortuuid import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from typing import Dict, Optional, Tuple, cast

from telegram import Update
from telegram.ext import ContextTypes, Application, CommandHandler, JobQueue, Job

from user import UNKNOWN_USER_MSG
from user.model import User, UserStatus

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


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.effective_user is not None
    assert update.effective_message is not None
    assert context.job_queue is not None
    assert update.effective_message.text is not None

    try:
        _cmd, id, *_remains = update.effective_message.text.split(" ")
    except Exception as e:
        logger.error(f"Unable to parse event id: {e}")
        await update.effective_message.reply_text(UNABLE_PARSE_EVENT_ID_MSG)
        return

    birthday_repr = ""
    job_id = None
    try:
        birthday = Birthday.objects.get(id=id)  # type: ignore
        birthday_repr = f"{birthday.title} for {birthday.addressed_to.username} by {birthday.created_by.username} at {birthday.recurrence.date} ({birthday.id})"
        job_id = birthday.job_id
        birthday.delete()
    except Exception as e:
        logger.error(f"Unable to delete event: {e}")
        await update.effective_message.reply_text(UNABLE_DELETE_EVENT_MSG)
        return
    finally:
        try:
            if job_id != None and context.job_queue.scheduler.get_job(job_id) != None:
                context.job_queue.scheduler.remove_job(job_id)
        except Exception as e:
            logger.error(f"Unable to delete job: {e}")
            await update.effective_message.reply_text(UNABLE_DELETE_EVENT_MSG)
            return

    await update.effective_message.reply_text(f"Удалено напоминание {birthday_repr}")


async def list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.effective_user is not None
    assert update.effective_message is not None
    assert context.job_queue is not None
    assert update.effective_message.text is not None

    birthdays = Birthday.objects()  # type: ignore
    birthdays = [
        f"{idx}. {b.title} for {b.addressed_to.username} by {b.created_by.username} at {b.recurrence.date} ({b.id})"
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
        logger.error(f"Unable to parse event id: {e}")
        await update.effective_message.reply_text(UNABLE_PARSE_EVENT_ID_MSG)
        return

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
        logger.error(f"Unable to parse event id: {e}")
        await update.effective_message.reply_text(UNABLE_PARSE_EVENT_ID_MSG)
        return

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
        return

    await update.effective_message.reply_text(f"Напоминания о дне рождения выключены")


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

    next_year = job.job.next_run_time.year + 1
    scheduled_date = job.job.next_run_time.replace(year=next_year)

    await context.bot.send_message(job.chat_id, text=f"Напоминаю! {data.text} !")

    try:
        next_job = __schedule_next_job(context.job_queue, job, scheduled_date)

        birthday.job_id = next_job.job_id
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
