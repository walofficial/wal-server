from enum import Enum


class MessageState(str, Enum):
    SENT = 'SENT'
    RECEIVED = 'RECEIVED'
    READ = 'READ'
    FAILED = 'FAILED'
    DELETED = 'DELETED'
