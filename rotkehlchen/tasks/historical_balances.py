import logging
from typing import TYPE_CHECKING, Final, NamedTuple

from rotkehlchen.api.websockets.typedefs import ProgressUpdateSubType, WSMessageType
from rotkehlchen.constants import ZERO
from rotkehlchen.db.cache import DBCacheStatic
from rotkehlchen.db.filtering import HistoryEventFilterQuery
from rotkehlchen.db.history_events import DBHistoryEvents
from rotkehlchen.fval import FVal
from rotkehlchen.history.events.structures.types import EventDirection, HistoryEventSubType
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.types import EventMetricKey, TimestampMS
from rotkehlchen.utils.misc import ts_ms_to_sec, ts_now

if TYPE_CHECKING:
    from rotkehlchen.db.dbhandler import DBHandler
    from rotkehlchen.history.events.structures.base import HistoryBaseEntry
    from rotkehlchen.user_messages import MessagesAggregator

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)

# Subtypes that affect the protocol bucket rather than the wallet bucket
PROTOCOL_BUCKET_SUBTYPES: Final = {
    HistoryEventSubType.RECEIVE_WRAPPED,
    HistoryEventSubType.GENERATE_DEBT,
    HistoryEventSubType.RETURN_WRAPPED,
    HistoryEventSubType.PAYBACK_DEBT,
}
METRICS_BATCH_SIZE: Final = 500


class Bucket(NamedTuple):
    """Represents a unique bucket for tracking historical balances.

    A bucket uniquely identifies where an asset balance is held:
    - location: The blockchain/exchange location (e.g., 'ethereum', 'kraken')
    - location_label: The specific address or account label
    - protocol: The DeFi protocol if funds are deposited there (e.g., 'aave'), or None for wallet
    - asset: The asset identifier
    """
    location: str
    location_label: str | None
    protocol: str | None
    asset: str

    @classmethod
    def from_db(cls, row: tuple[str, str | None, str | None, str]) -> 'Bucket':
        return cls(location=row[0], location_label=row[1], protocol=row[2], asset=row[3])

    @classmethod
    def from_event(cls, event: 'HistoryBaseEntry') -> 'Bucket':
        """Returns the Bucket where this event's asset balance is tracked.

        For example, depositing DAI into Compound removes DAI from your wallet bucket,
        while the cDAI you receive is tracked in the Compound protocol bucket.
        """
        location = event.location.serialize_for_db()
        asset = event.asset.identifier
        if (
            (counterparty := getattr(event, 'counterparty', None)) is not None and
            event.maybe_get_direction() != EventDirection.NEUTRAL and
            event.event_subtype in PROTOCOL_BUCKET_SUBTYPES
        ):
            return cls(
                location=location,
                location_label=event.location_label,
                protocol=counterparty,
                asset=asset,
            )

        return cls(
            location=location,
            location_label=event.location_label,
            protocol=None,
            asset=asset,
        )


def _load_bucket_balances_before_ts(
        database: 'DBHandler',
        from_ts: TimestampMS,
) -> dict[Bucket, FVal]:
    """Load the latest balance per bucket before from_ts.

    We use MAX(timestamp + sequence_index) to identify the most recent row per bucket,
    relying on SQLite's bare column behavior to return non-aggregated columns from
    that row. See https://www.sqlite.org/lang_select.html#bareagg
    """
    bucket_balances: dict[Bucket, FVal] = {}
    with database.conn.read_ctx() as cursor:
        cursor.execute(
            """
            SELECT he.location, he.location_label, em.protocol, he.asset,
                   em.metric_value, MAX(he.timestamp + he.sequence_index)
            FROM event_metrics em
            INNER JOIN history_events he ON em.event_identifier = he.identifier
            WHERE em.metric_key = ? AND he.timestamp < ?
            GROUP BY he.location, he.location_label, em.protocol, he.asset
            """,
            (EventMetricKey.BALANCE.serialize(), from_ts),
        )
        for row in cursor:
            bucket_balances[Bucket.from_db(row[:4])] = FVal(row[4])

    log.debug(f'Loaded {len(bucket_balances)} bucket balances before ts={from_ts}')
    return bucket_balances


def process_historical_balances(
        database: 'DBHandler',
        msg_aggregator: 'MessagesAggregator',
        from_ts: TimestampMS | None = None,
) -> None:
    """Process events and compute balance metrics. Stops on negative balance."""
    log.debug(f'Starting historical balance processing from_ts={from_ts}')
    bucket_balances = {}
    if from_ts is not None:
        bucket_balances = _load_bucket_balances_before_ts(database, from_ts)

    with database.conn.read_ctx() as cursor:
        events = DBHistoryEvents(database).get_history_events_internal(
            cursor=cursor,
            filter_query=HistoryEventFilterQuery.make(
                from_ts=ts_ms_to_sec(from_ts) if from_ts is not None else None,
                order_by_rules=[('timestamp', True), ('sequence_index', True)],
                exclude_ignored_assets=True,
            ),
        )

    if (total_events := len(events)) == 0:
        log.debug('No events to process for historical balances')
        _finalize_processing(database)
        return

    metrics_batch: list[tuple[int | None, str | None, str, str]] = []
    first_batch_written = False
    send_ws_every = msg_aggregator.how_many_events_per_ws(total_events)
    for idx, event in enumerate(events):
        bucket = Bucket.from_event(event)
        if (current_balance_in_bucket := bucket_balances.get(bucket, ZERO)) < ZERO:
            continue

        if (direction := event.maybe_get_direction()) == EventDirection.IN:
            new_balance = current_balance_in_bucket + event.amount
            bucket_balances[bucket] = new_balance
        elif direction == EventDirection.OUT:
            if (new_balance := current_balance_in_bucket - event.amount) < ZERO:
                msg_aggregator.add_message(
                    message_type=WSMessageType.NEGATIVE_BALANCE_DETECTED,
                    data={
                        'event_identifier': event.identifier,
                        'group_identifier': event.group_identifier,
                        'asset': event.asset.identifier,
                        'balance_before': str(current_balance_in_bucket),
                    },
                )
                log.warning(
                    f'Negative balance detected for {event.asset.identifier} '
                    f'at event {event.identifier}. Skipping {bucket}.',
                )
                bucket_balances[bucket] = FVal(-1)
                continue
            bucket_balances[bucket] = new_balance
        else:  # neutral events don't affect balance
            continue

        metrics_batch.append((
            event.identifier,
            bucket.protocol,
            EventMetricKey.BALANCE.serialize(),
            str(new_balance),
        ))

        if idx % send_ws_every == 0:
            msg_aggregator.add_message(
                message_type=WSMessageType.PROGRESS_UPDATES,
                data={
                    'subtype': str(ProgressUpdateSubType.HISTORICAL_BALANCE_PROCESSING),
                    'total': total_events,
                    'processed': idx,
                },
            )

        if len(metrics_batch) >= METRICS_BATCH_SIZE:
            with database.user_write() as write_cursor:
                if not first_batch_written and from_ts is not None:
                    write_cursor.execute(
                        'DELETE FROM event_metrics WHERE event_identifier IN '
                        '(SELECT identifier FROM history_events WHERE timestamp >= ?)',
                        (from_ts,),
                    )
                    first_batch_written = True
                write_cursor.executemany(
                    'INSERT OR REPLACE INTO event_metrics '
                    '(event_identifier, protocol, metric_key, metric_value) '
                    'VALUES (?, ?, ?, ?)',
                    metrics_batch,
                )
            metrics_batch = []

    if len(metrics_batch) != 0:  # last batch
        with database.user_write() as write_cursor:
            if not first_batch_written and from_ts is not None:
                write_cursor.execute(
                    'DELETE FROM event_metrics WHERE event_identifier IN '
                    '(SELECT identifier FROM history_events WHERE timestamp >= ?)',
                    (from_ts,),
                )
            write_cursor.executemany(
                'INSERT OR REPLACE INTO event_metrics '
                '(event_identifier, protocol, metric_key, metric_value) '
                'VALUES (?, ?, ?, ?)',
                metrics_batch,
            )

    msg_aggregator.add_message(
        message_type=WSMessageType.PROGRESS_UPDATES,
        data={
            'subtype': str(ProgressUpdateSubType.HISTORICAL_BALANCE_PROCESSING),
            'total': total_events,
            'processed': total_events,
        },
    )
    _finalize_processing(database)
    log.debug(f'Completed historical balance processing for {total_events} events')


def _finalize_processing(database: 'DBHandler') -> None:
    """Update cache timestamps and clear stale marker."""
    with database.user_write() as write_cursor:
        database.set_static_cache(
            write_cursor=write_cursor,
            name=DBCacheStatic.LAST_HISTORICAL_BALANCE_PROCESSING_TS,
            value=ts_now(),
        )
        write_cursor.execute(
            'DELETE FROM key_value_cache WHERE name = ?',
            (DBCacheStatic.STALE_BALANCES_FROM_TS.value,),
        )
