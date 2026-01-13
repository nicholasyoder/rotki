from typing import TYPE_CHECKING

import gevent

from rotkehlchen.assets.asset import Asset
from rotkehlchen.balances.historical import HistoricalBalancesManager
from rotkehlchen.constants.assets import A_ETH
from rotkehlchen.constants.misc import ONE
from rotkehlchen.db.cache import DBCacheStatic
from rotkehlchen.db.filtering import HistoricalBalancesFilterQuery
from rotkehlchen.db.history_events import DBHistoryEvents
from rotkehlchen.fval import FVal
from rotkehlchen.history.events.structures.evm_event import EvmEvent
from rotkehlchen.history.events.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.tasks.historical_balances import process_historical_balances
from rotkehlchen.tests.utils.ethereum import TEST_ADDR1
from rotkehlchen.tests.utils.factories import make_evm_tx_hash
from rotkehlchen.types import Location, Timestamp, TimestampMS
from rotkehlchen.utils.misc import ts_now

if TYPE_CHECKING:
    from rotkehlchen.db.dbhandler import DBHandler
    from rotkehlchen.user_messages import MessagesAggregator


def test_process_historical_balances_clears_stale_marker(
        database: 'DBHandler',
        messages_aggregator: 'MessagesAggregator',
) -> None:
    cache_key = DBCacheStatic.STALE_BALANCES_FROM_TS.value

    with database.user_write() as write_cursor:
        DBHistoryEvents(database).add_history_event(
            write_cursor=write_cursor,
            event=EvmEvent(
                tx_ref=make_evm_tx_hash(),
                group_identifier='grp1',
                sequence_index=0,
                timestamp=TimestampMS(1000),
                location=Location.ETHEREUM,
                event_type=HistoryEventType.RECEIVE,
                event_subtype=HistoryEventSubType.NONE,
                asset=A_ETH,
                amount=FVal('10'),
                location_label=TEST_ADDR1,
            ),
        )

    with database.conn.read_ctx() as cursor:
        assert cursor.execute(
            'SELECT value FROM key_value_cache WHERE name = ?',
            (cache_key,),
        ).fetchone() is not None

    gevent.sleep(0.01)
    process_historical_balances(database, messages_aggregator)

    with database.conn.read_ctx() as cursor:
        assert cursor.execute(
            'SELECT value FROM key_value_cache WHERE name = ?',
            (cache_key,),
        ).fetchone() is None


def test_has_unprocessed_events(
        database: 'DBHandler',
        messages_aggregator: 'MessagesAggregator',
) -> None:
    """Test _has_unprocessed_events correctly uses stale marker to determine processing state.

    Conditions tested:
    - stale_value=None: False (all events evaluated, including negative balance skips)
    - stale_value exists + last_processing=None: query result (never processed)
    - stale_value exists + last_processing exists: filtered query (>= stale_event_ts)
    """
    manager = HistoricalBalancesManager(database)
    stale_cache_key = DBCacheStatic.STALE_BALANCES_FROM_TS.value
    modification_cache_key = DBCacheStatic.STALE_BALANCES_MODIFICATION_TS.value

    def add_event(ts: int, asset: Asset = A_ETH) -> None:
        with database.user_write() as write_cursor:
            DBHistoryEvents(database).add_history_event(
                write_cursor=write_cursor,
                event=EvmEvent(
                    tx_ref=make_evm_tx_hash(),
                    group_identifier=f'grp_{ts}',
                    sequence_index=0,
                    timestamp=TimestampMS(ts),
                    location=Location.ETHEREUM,
                    event_type=HistoryEventType.RECEIVE,
                    event_subtype=HistoryEventSubType.NONE,
                    asset=asset,
                    amount=FVal('10'),
                    location_label=TEST_ADDR1,
                ),
            )

    def clear_stale_marker() -> None:
        with database.user_write() as write_cursor:
            write_cursor.execute(
                'DELETE FROM key_value_cache WHERE name IN (?, ?)',
                (stale_cache_key, modification_cache_key),
            )

    def set_stale_marker(event_ts: int, modification_ts: int) -> None:
        with database.user_write() as write_cursor:
            database.set_static_cache(
                write_cursor=write_cursor,
                name=DBCacheStatic.STALE_BALANCES_FROM_TS,
                value=str(event_ts),
            )
            database.set_static_cache(
                write_cursor=write_cursor,
                name=DBCacheStatic.STALE_BALANCES_MODIFICATION_TS,
                value=str(modification_ts),
            )

    def set_last_processing(ts: int) -> None:
        with database.user_write() as write_cursor:
            database.set_static_cache(
                write_cursor=write_cursor,
                name=DBCacheStatic.LAST_HISTORICAL_BALANCE_PROCESSING_TS,
                value=Timestamp(ts),
            )

    # 1. No events, no stale marker -> False
    clear_stale_marker()
    assert manager._has_unprocessed_events('timestamp <= ?', [TimestampMS(9999)]) is False

    # 2. All processed, no modifications (stale=None) -> False
    add_event(1000)
    gevent.sleep(0.01)
    process_historical_balances(database, messages_aggregator)
    assert manager._has_unprocessed_events('timestamp <= ?', [TimestampMS(9999)]) is False

    # 3. All processed including negative balance skip (stale=None) -> False (the fix!)
    clear_stale_marker()
    set_last_processing(ts_now())
    assert manager._has_unprocessed_events('timestamp <= ?', [TimestampMS(9999)]) is False

    # 4. Events added, never processed (stale exists, last_processing=None) -> True
    with database.user_write() as write_cursor:
        write_cursor.execute('DELETE FROM key_value_cache')
        write_cursor.execute('DELETE FROM event_metrics')
    add_event(2000)
    assert manager._has_unprocessed_events('timestamp <= ?', [TimestampMS(9999)]) is True

    # 5. Events added, never processed, no match (wrong asset) -> False
    assert manager._has_unprocessed_events('asset = ?', ['BTC']) is False

    # 6. New events after processing, matches new events -> True
    gevent.sleep(0.01)
    process_historical_balances(database, messages_aggregator)
    add_event(5000)
    assert manager._has_unprocessed_events('timestamp <= ?', [TimestampMS(9999)]) is True

    # 7. New events after processing, query only old events -> False
    assert manager._has_unprocessed_events('timestamp <= ?', [TimestampMS(3000)]) is False

    # 8. New ETH events, query BTC -> False
    assert manager._has_unprocessed_events('asset = ?', ['BTC']) is False

    # 9. New events at ts=5000, query ts <= 3000 (before stale_event_ts) -> False
    set_stale_marker(5000, ts_now() * 1000)
    set_last_processing(ts_now() - 1)
    assert manager._has_unprocessed_events('timestamp <= ?', [TimestampMS(3000)]) is False

    # 10. Events modified during processing -> True
    with database.user_write() as write_cursor:
        write_cursor.execute('DELETE FROM event_metrics WHERE event_identifier IN (SELECT identifier FROM history_events WHERE timestamp >= 5000)')  # noqa: E501
    assert manager._has_unprocessed_events('timestamp <= ?', [TimestampMS(9999)]) is True


def test_get_balances_with_unprocessed_events_and_timestamp_filter(
        database: 'DBHandler',
        messages_aggregator: 'MessagesAggregator',
) -> None:
    """Regression test ensuring FVal timestamp scaling results are int-converted for SQL binding.

    When querying historical balances with a timestamp filter, the timestamp is multiplied by
    scaling_factor, producing an FVal that must be explicitly converted to int before passing
    to SQL to avoid type binding errors.
    """
    with database.user_write() as write_cursor:
        DBHistoryEvents(database).add_history_event(
            write_cursor=write_cursor,
            event=EvmEvent(
                tx_ref=make_evm_tx_hash(),
                group_identifier='grp_test',
                sequence_index=0,
                timestamp=TimestampMS(1729787659000),
                location=Location.ETHEREUM,
                event_type=HistoryEventType.RECEIVE,
                event_subtype=HistoryEventSubType.NONE,
                asset=A_ETH,
                amount=ONE,
                location_label=TEST_ADDR1,
            ),
        )

    filter_query = HistoricalBalancesFilterQuery.make(
        timestamp=Timestamp(1729787659),
        location=Location.ETHEREUM,
    )
    manager = HistoricalBalancesManager(database)
    processing_required, balances = manager.get_balances(filter_query=filter_query)

    assert processing_required is True
    assert balances is None
