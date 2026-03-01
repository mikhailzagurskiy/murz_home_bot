from enum import Enum
from mongoengine import *  # type: ignore
from datetime import datetime, timezone

from user.model import User
from .recurrence import *


class EventState(Enum):
    """Base state of the event"""

    ENABLED = "enabled"
    DISABLED = "disabled"


class EventStatus(Enum):
    """Status of the event"""

    CREATED = "created"
    SCHEDULED = "scheduled"
    FIRED = "fired"
    MISSED = "missed"
    EXPIRED = "expired"
    DELETED = "deleted"


class Event(Document):
    """Base class for scheduled event"""

    title = StringField(max_length=256, unique=True)
    text = StringField(min_length=2, max_length=1024, required=True)
    created_at = DateTimeField(default=datetime.now(timezone.utc), required=True)
    created_by = ReferenceField(User, required=True, reverse_delete_rule=DENY)
    addressed_to = ReferenceField(User, required=True, reverse_delete_rule=DENY)
    state = EnumField(EventState, default=EventState.ENABLED, required=True)
    status = EnumField(EventStatus, default=EventStatus.CREATED, required=True)
    need_confirmation = BooleanField(default=False)
    job_id = StringField(required=True, unique=True)

    meta = {
        "collection": "events",
        "ordering": ["-created_at"],
        "allow_inheritance": True,
    }


class RecurringEvent(Event):
    """Base class for recurring events"""

    recurrence = EmbeddedDocumentField(Recurrence, required=True)


class EveryYearEvent(Event):
    """Base class for events that happen every year on specific date"""

    recurrence = EmbeddedDocumentField(EveryYear, required=True)


class Birthday(EveryYearEvent):
    """Birthday event

    Fire every year at specific date
    """

    person = StringField(max_length=256, required=True)
    contacts = MapField(StringField(), required=False)
    relation = StringField(max_length=256, required=False)
    groups = ListField(StringField(), required=False)


class Holiday(RecurringEvent):
    """Holiday event

    Uses specific rules for firing
    """

    categories = ListField(StringField(), required=False)
