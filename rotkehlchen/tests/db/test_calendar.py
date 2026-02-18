import pytest

from rotkehlchen.db.calendar import CalendarEntry, DBCalendar
from rotkehlchen.errors.misc import InputError
from rotkehlchen.types import Timestamp


def test_update_nonexistent_calendar_entry_raises(database) -> None:
    db_calendar = DBCalendar(database)

    with pytest.raises(InputError, match='non existent calendar entry'):
        db_calendar.update_calendar_entry(CalendarEntry(
            name='test entry',
            timestamp=Timestamp(1700000000),
            description='test description',
            counterparty=None,
            address=None,
            blockchain=None,
            color=None,
            auto_delete=False,
            identifier=999999,
        ))
