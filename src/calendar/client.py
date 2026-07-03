from googleapiclient.discovery import build
from googleapiclient.discovery import Resource

from .auth import get_credentials


def get_calendar_service() -> Resource:
    return build("calendar", "v3", credentials=get_credentials())
