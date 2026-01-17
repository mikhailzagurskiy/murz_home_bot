import logging

from telegram.ext import Application

from apscheduler.events import (
    JobExecutionEvent,
    SchedulerEvent,
    JobEvent,
    JobSubmissionEvent,
    EVENT_ALL,
    EVENT_JOB_ADDED,
    EVENT_JOB_REMOVED,
    EVENT_JOB_MODIFIED,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_ERROR,
    EVENT_JOB_MISSED,
    EVENT_JOB_SUBMITTED,
)

from .model import Event, EventStatus

UNABLE_CREATE_EVENT_MSG = "Unable to create event"
UNABLE_PARSE_EVENT_ID_MSG = "Unable to parse event id"
UNABLE_RESUME_EVENT_MSG = "Unable to resume event"
UNABLE_PAUSE_EVENT_MSG = "Unable to pause event"
UNABLE_DELETE_EVENT_MSG = "Unable to delete event"
UNABLE_SCHEDULE_REMINDER_MSG = "Unable to schedule reminder"


def generic_listener(event):
    logging.log(5, "Generic event: ", event)
    if isinstance(event, SchedulerEvent):
        logging.log(5, "alias: ", event.alias)
    if isinstance(event, JobEvent):
        logging.log(5, "code: ", event.code)
        logging.log(5, "job_id: ", event.job_id)
        logging.log(5, "jobstore: ", event.jobstore)
    if isinstance(event, JobSubmissionEvent):
        logging.log(5, "scheduled_run_times: ", event.scheduled_run_times)
    if isinstance(event, JobExecutionEvent):
        logging.log(5, "retval: ", event.retval)
        logging.log(5, "exception: ", event.exception)
        logging.log(5, "traceback: ", event.traceback)


def register_job(event: JobEvent):
    if not isinstance(event, JobEvent):
        raise TypeError("Incorrect event type")

    Event.objects(job_id=event.job_id).update_one(status=EventStatus.SCHEDULED)  # type: ignore

    logging.debug(f"Event for {event.job_id} was scheduled")


def schedule_job(event: JobSubmissionEvent):
    if not isinstance(event, JobSubmissionEvent):
        raise TypeError("Incorrect event type")

    logging.debug(f"SCHEDULE {event.job_id}")


def miss_job(event: JobExecutionEvent):
    if not isinstance(event, JobExecutionEvent):
        raise TypeError("Incorrect event type")


    logging.debug(f"MISS {event.job_id}")


def execute_job(event: JobExecutionEvent):
    if not isinstance(event, JobExecutionEvent):
        raise TypeError("Incorrect event type")


    logging.debug(f"EXECUTE {event.job_id}")


def fail_job(event: JobExecutionEvent):
    if not isinstance(event, JobExecutionEvent):
        raise TypeError("Incorrect event type")

    logging.debug(f"FAIL {event.job_id}")


def remove_job(event: JobExecutionEvent):
    if not isinstance(event, JobEvent):
        raise TypeError("Incorrect event type")

    Event.objects(job_id=event.job_id).update_one(status=EventStatus.EXPIRED)  # type: ignore

    logging.debug(f"Event for {event.job_id} was expired")


def subscribe_to_events(application: Application):
    if application.job_queue is None:
        logging.error("Job queue doesn't exist")
        return

    application.job_queue.scheduler.add_listener(register_job, EVENT_JOB_ADDED)
    application.job_queue.scheduler.add_listener(schedule_job, EVENT_JOB_SUBMITTED)
    application.job_queue.scheduler.add_listener(miss_job, EVENT_JOB_MISSED)
    application.job_queue.scheduler.add_listener(execute_job, EVENT_JOB_EXECUTED)
    application.job_queue.scheduler.add_listener(fail_job, EVENT_JOB_ERROR)
    application.job_queue.scheduler.add_listener(remove_job, EVENT_JOB_REMOVED)
    # application.job_queue.scheduler.add_listener(generic_listener, EVENT_ALL)
