from enum import Enum
from mongoengine import *  # type: ignore


class UserStatus(Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    DELETED = "deleted"


class User(Document):
    user_id = IntField(required=True, unique=True)
    username = StringField(required=True, unique=True)
    chat_id = IntField(required=True)
    status = EnumField(UserStatus, default=UserStatus.ACTIVE)
    meta = {"collection": "users"}
