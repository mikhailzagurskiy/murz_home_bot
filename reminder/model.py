from enum import Enum
from mongoengine import *  # type: ignore
from datetime import datetime, timezone

from user.model import User


class EventState(Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class EventStatus(Enum):
    CREATED = "created"
    SCHEDULED = "scheduled"
    EXPIRED = "expired"
    DELETED = "deleted"


class EventType(Enum):
    BIRTHDAY = "birthday"
    CUSTOM = "custom"


class Event(Document):
    name = StringField(max_length=256, unique=True)
    text = StringField(min_length=2, max_length=1024, required=True)
    created_at = DateTimeField(default=datetime.now(timezone.utc), required=True)
    created_by = ReferenceField(User, required=True, reverse_delete_rule=DENY)
    addressed_to = ReferenceField(User, required=True, reverse_delete_rule=DENY)
    state = EnumField(EventState, default=EventState.ENABLED, required=True)
    status = EnumField(EventStatus, default=EventStatus.CREATED, required=True)
    typ = EnumField(EventType, required=False, default=EventType.CUSTOM)
    scheduled_to = DateTimeField(required=False)
    need_confirmation = BooleanField(default=False)
    # TODO: Maybe inheritance?!
    since = DateTimeField(required=False)
    until = DateTimeField(required=False)
    interval = IntField(required=False)
    job_id = StringField(required=True, unique=True)

    meta = {"collection": "events", "ordering": ["-created_at"]}
