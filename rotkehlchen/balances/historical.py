import logging
from collections import defaultdict
from collections.abc import Sequence
from typing import TYPE_CHECKING, TypedDict

import polars as pl

from rotkehlchen.assets.asset import Asset
from rotkehlchen.constants import DAY_IN_SECONDS, ONE, ZERO
from rotkehlchen.constants.prices import ZERO_PRICE
from rotkehlchen.db.filtering import (
    HistoryEventFilterQuery,
)
from rotkehlchen.db.settings import CachedSettings
from rotkehlchen.db.utils import get_query_chunks
from rotkehlchen.errors.misc import NotFoundError, RemoteError
from rotkehlchen.errors.price import NoPriceForGivenTimestamp
from rotkehlchen.errors.serialization import DeserializationError
from rotkehlchen.fval import FVal
from rotkehlchen.globaldb.handler import GlobalDBHandler
from rotkehlchen.history.events.structures.base import HistoryEvent
from rotkehlchen.history.events.structures.types import (
    EventDirection,
    HistoryEventSubType,
)
from rotkehlchen.history.price import PriceHistorian
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.types import EventMetricKey, Timestamp, TimestampMS
from rotkehlchen.utils.misc import timestamp_to_daystart_timestamp, ts_ms_to_sec, ts_sec_to_ms

if TYPE_CHECKING:
    from rotkehlchen.assets.asset import EvmToken
    from rotkehlchen.db.dbhandler import DBHandler
    from rotkehlchen.types import ChecksumEvmAddress

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


class HistoricalBalance(TypedDict):
    amount: FVal
    price: FVal


class HistoricalBalancesManager:
    """Processes historical events and calculates balances"""

    def __init__(self, db: 'DBHandler') -> None:
        self.db = db

    def get_balances(
            self,
            timestamp: Timestamp,
    ) -> tuple[bool, dict[Asset, HistoricalBalance] | None]:
        """Get historical balances for all assets at a given timestamp.

        The inner query gets the latest balance per bucket via MAX(timestamp + sequence_index),
        relying on SQLite's bare column behavior to return non-aggregated columns from that row.
        See https://www.sqlite.org/lang_select.html#bareagg

        Returns a tuple of (processing_required, balances):
        - processing_required: True if events exist but haven't been processed yet
        - balances: Dict of asset to balance info, or None if no data available
        """
        asset_balances_by_id: dict[str, FVal] = {}
        timestamp_ms = ts_sec_to_ms(timestamp)
        with self.db.conn.read_ctx() as cursor:
            cursor.execute(
                """SELECT asset, SUM(metric_value) FROM (
                    SELECT he.asset, em.metric_value, MAX(he.timestamp + he.sequence_index)
                    FROM event_metrics em
                    INNER JOIN history_events he ON em.event_identifier = he.identifier
                    WHERE em.metric_key = ? AND he.timestamp <= ?
                    GROUP BY he.location, he.location_label, em.protocol, he.asset
                ) GROUP BY asset
                """,
                (EventMetricKey.BALANCE.serialize(), timestamp_ms),
            )
            for asset_id, total in cursor:
                asset_balances_by_id[asset_id] = FVal(total)

        if len(asset_balances_by_id) == 0:
            needs_processing = self._has_unprocessed_events(
                where_clause='timestamp <= ?',
                bindings=[timestamp_ms],
            )
            return needs_processing, None

        result: dict[Asset, HistoricalBalance] = {}
        main_currency = CachedSettings().main_currency
        for asset_id, amount in asset_balances_by_id.items():
            asset = Asset(asset_id)
            try:
                price = PriceHistorian.query_historical_price(
                    from_asset=asset,
                    to_asset=main_currency,
                    timestamp=timestamp,
                )
            except (RemoteError, NoPriceForGivenTimestamp):
                price = ZERO_PRICE

            result[asset] = {'amount': amount, 'price': price}

        return False, result

    def get_erc721_tokens_balances(
            self,
            address: 'ChecksumEvmAddress',
            assets: tuple[Asset, ...],
    ) -> dict['EvmToken', FVal]:
        """Get current balances for the given erc721 assets of a specific address by processing historical events.

        May raise:
            - NotFoundError if no events exist for the assets/address
            - DeserializationError if there is a problem deserializing an event from DB
        """  # noqa: E501
        events, _ = self._get_events_and_currency(
            assets=assets,
            address=address,
        )
        if len(events) == 0:
            raise NotFoundError(f'No historical data found for {assets} for user address {address}')  # noqa: E501

        current_balances: dict[Asset, FVal] = defaultdict(FVal)
        for event in events:
            self._update_balances(event=event, current_balances=current_balances)

        return {asset.resolve_to_evm_token(): balance for asset, balance in current_balances.items() if balance > ZERO}  # noqa: E501

    def get_asset_balance(
            self,
            asset: Asset,
            timestamp: Timestamp,
    ) -> tuple[bool, HistoricalBalance | None]:
        """Get historical balance for a single asset at a given timestamp.

        The inner query gets the latest balance per bucket via MAX(timestamp + sequence_index),
        relying on SQLite's bare column behavior to return non-aggregated columns from that row.
        See https://www.sqlite.org/lang_select.html#bareagg

        Returns a tuple of (processing_required, balance):
        - processing_required: True if events exist but haven't been processed yet
        - balance: Balance info dict, or None if no data available
        """
        timestamp_ms = ts_sec_to_ms(timestamp)
        with self.db.conn.read_ctx() as cursor:
            if (total_amount := cursor.execute(
                """SELECT SUM(metric_value) FROM (SELECT em.metric_value, MAX(he.timestamp + he.sequence_index)
                    FROM event_metrics em
                    INNER JOIN history_events he ON em.event_identifier = he.identifier
                    WHERE em.metric_key = ? AND he.timestamp <= ? AND he.asset = ?
                    GROUP BY he.location, he.location_label, em.protocol
                )""",  # noqa: E501
                (EventMetricKey.BALANCE.serialize(), timestamp_ms, asset.identifier),
            ).fetchone()[0]) is None:
                needs_processing = self._has_unprocessed_events(
                    where_clause='asset = ? AND timestamp <= ?',
                    bindings=[asset.identifier, timestamp_ms],
                )
                return needs_processing, None

        try:
            price = PriceHistorian.query_historical_price(
                from_asset=asset,
                to_asset=CachedSettings().main_currency,
                timestamp=timestamp,
            )
        except (RemoteError, NoPriceForGivenTimestamp):
            price = ZERO_PRICE

        return False, {'amount': FVal(total_amount), 'price': price}

    def get_assets_amounts(
            self,
            assets: tuple[Asset, ...],
            from_ts: Timestamp,
            to_ts: Timestamp,
    ) -> tuple[bool, dict[Timestamp, FVal] | None]:
        """Get historical balance amounts for the given assets within the given time range.

        Returns a tuple of (processing_required, amounts):
        - processing_required: True if events exist but haven't been processed yet
        - amounts: Cumulative balance at each event timestamp, relative to the start of the
          time range (starting from 0). Uses pre-computed balances from event_metrics table.
          Uses SQL LAG() window function to compute per-bucket balance deltas in the database.
          None if no data is available.
        """
        from_ts_ms, to_ts_ms = ts_sec_to_ms(from_ts), ts_sec_to_ms(to_ts)
        metric_key, asset_ids = EventMetricKey.BALANCE.serialize(), [asset.identifier for asset in assets]  # noqa: E501
        schema = {'timestamp': pl.Int64, 'sort_key': pl.Int64, 'delta': pl.Float64}
        df = pl.DataFrame(schema=schema)
        with self.db.conn.read_ctx() as cursor:
            for chunk, placeholders in get_query_chunks(data=asset_ids):
                if (chunk_df := pl.DataFrame(
                    cursor.execute(
                        f"""
                        WITH all_events AS (
                            SELECT
                                he.timestamp,
                                he.location || COALESCE(he.location_label, '') || COALESCE(em.protocol, '') || he.asset as bucket,
                                CAST(em.metric_value AS REAL) as balance,
                                he.timestamp + he.sequence_index as sort_key
                            FROM event_metrics em
                            INNER JOIN history_events he ON em.event_identifier = he.identifier
                            WHERE em.metric_key = ? AND he.asset IN ({placeholders})
                        ),
                        with_delta AS (
                            SELECT
                                timestamp,
                                sort_key,
                                balance - COALESCE(LAG(balance) OVER (PARTITION BY bucket ORDER BY sort_key), 0) as delta
                            FROM all_events
                        )
                        SELECT timestamp, sort_key, delta
                        FROM with_delta
                        WHERE timestamp >= ? AND timestamp <= ?
                        """,  # noqa: E501
                        (metric_key, *chunk, from_ts_ms, to_ts_ms),
                    ),
                    schema=schema,
                    orient='row',
                )).height > 0:
                    df.vstack(chunk_df, in_place=True)

        if df.height == 0:
            for chunk, placeholders in get_query_chunks(data=asset_ids):
                if self._has_unprocessed_events(
                    where_clause=f'asset IN ({placeholders}) AND timestamp >= ? AND timestamp <= ?',  # noqa: E501
                    bindings=[*chunk, from_ts_ms, to_ts_ms],
                ):
                    return True, None
            return False, None

        result_df = (
            df.rechunk().sort('sort_key')
            .with_columns(pl.col('delta').cum_sum().alias('amount'))
            .select(['timestamp', 'amount'])
        )

        timestamps = result_df['timestamp'].to_list()
        amounts = result_df['amount'].to_list()
        return False, {
            ts_ms_to_sec(ts): FVal(amt)
            for ts, amt in zip(timestamps, amounts, strict=True)
        }

    def get_historical_netvalue(
            self,
            from_ts: Timestamp,
            to_ts: Timestamp,
    ) -> tuple[dict[Timestamp, FVal], list[tuple[str, Timestamp]], tuple[str | int | None, str | None] | None]:  # noqa: E501
        """Calculates historical net worth per day within the given time range.

        Uses asset balances per day combined with historical prices to calculate
        the total net worth in the user's profit currency for each day. Stops calculation
        if negative balance would be created for any asset.

        May raise:
            - NotFoundError if no events exist in the specified time period.
            - DeserializationError if there is a problem deserializing an event from DB.

        Returns:
            - A mapping of timestamps to net worth in main currency for each day
            - A list of (asset id, timestamp) tuples where price data was missing
            - A tuple of (identifier, group_identifier) for the event that caused a negative
              balance if any, else None.
        """
        events, main_currency = self._get_events_and_currency(from_ts=from_ts, to_ts=to_ts)
        if len(events) == 0:
            raise NotFoundError(f'No historical data found within {from_ts=} and {to_ts=}.')

        negative_balance_data = None
        current_balances: dict[Asset, FVal] = defaultdict(FVal)
        daily_balances: dict[Timestamp, dict[Asset, FVal]] = {}
        current_day = timestamp_to_daystart_timestamp(ts_ms_to_sec(events[0].timestamp))
        daily_balances[current_day] = current_balances.copy()
        for event in events:
            if (day_ts := timestamp_to_daystart_timestamp(ts_ms_to_sec(event.timestamp))) > current_day:  # noqa: E501
                daily_balances[current_day] = current_balances.copy()
                current_day = day_ts

            if (negative_balance_data := self._update_balances(event=event, current_balances=current_balances)) is not None:  # noqa: E501
                break
        else:  # no negative balance happened so update the last day's balance.
            daily_balances[current_day] = current_balances.copy()

        # For each day, calculate net worth by multiplying non-zero asset
        # balances with their prices. Store any missing price data in missing_price_points.
        net_worth_per_day: dict[Timestamp, FVal] = {}
        missing_price_points: list[tuple[str, Timestamp]] = []
        for day_ts, asset_balances in daily_balances.items():
            assets = [
                asset for asset, balance in asset_balances.items()
                if balance != ZERO
            ]
            if len(assets) == 0:
                continue

            prices, missing = self._get_prices(
                timestamp=day_ts,
                assets=assets,
                main_currency=main_currency,
            )
            missing_price_points.extend(missing)

            day_total = sum(
                (
                    balance * prices[asset]
                    for asset, balance in asset_balances.items()
                    if asset in prices and balance != ZERO
                 ),
                ZERO,
            )
            net_worth_per_day[day_ts] = day_total

        return net_worth_per_day, missing_price_points, negative_balance_data

    def _has_unprocessed_events(
            self,
            where_clause: str,
            bindings: Sequence[str | TimestampMS],
    ) -> bool:
        """Check if unprocessed history events exist matching the given conditions.

        Used to distinguish between "events exist but need processing"
        vs "no events in this time range at all" when no metrics are found.
        """
        query = f'SELECT 1 FROM history_events WHERE {where_clause} LIMIT 1'
        with self.db.conn.read_ctx() as cursor:
            return cursor.execute(query, bindings).fetchone() is not None

    def _get_events_and_currency(
            self,
            from_ts: Timestamp | None = None,
            to_ts: Timestamp | None = None,
            assets: tuple[Asset, ...] | None = None,
            address: 'ChecksumEvmAddress | None' = None,
    ) -> tuple[list[HistoryEvent], Asset]:
        """Helper method to get events and main currency from DB.

        If `assets` is provided, returns only events involving these specific
        assets. For history events, only the affected asset is checked.
        Otherwise, no asset filtering is applied.

        May raise:
        - DeserializationError if there is a problem deserializing an event from DB.
        """
        with self.db.conn.read_ctx() as cursor:
            main_currency = self.db.get_setting(cursor=cursor, name='main_currency')
            filter_query = HistoryEventFilterQuery.make(
                from_ts=from_ts,
                to_ts=to_ts,
                order_by_rules=[('timestamp', True)],
                exclude_ignored_assets=True,
                assets=assets,
                exclude_subtypes=[
                    HistoryEventSubType.DEPOSIT_ASSET,
                    HistoryEventSubType.REMOVE_ASSET,
                ],
                location_labels=[address] if address else None,
            )
            events = []
            where_clauses, bindings = filter_query.prepare(
                with_order=True,
                with_group_by=False,
                with_pagination=False,
                without_ignored_asset_filter=False,
            )
            for entry in cursor.execute(f'SELECT {filter_query.get_columns()} FROM history_events {where_clauses}', bindings):  # noqa: E501
                try:
                    events.append(HistoryEvent.deserialize_from_db(entry[1:]))
                except DeserializationError as e:
                    raise DeserializationError(
                        f'Failed to deserialize event {entry} while '
                        f'processing historical balances due to {e!s}',
                    ) from e

        return events, main_currency

    @staticmethod
    def _get_prices(
            assets: list[Asset],
            main_currency: Asset,
            timestamp: Timestamp,
    ) -> tuple[dict[Asset, FVal], list[tuple[str, Timestamp]]]:
        """Gets cached historical prices for multiple assets at once.

        Returns:
            - A mapping of asset to price in main currency
            - A list of (asset_id, timestamp) tuples for missing prices
        """
        prices: dict[Asset, FVal] = {}
        missing_prices: list[tuple[str, Timestamp]] = []
        querystr = """
        SELECT ph1.from_asset, ph1.price
        FROM price_history AS ph1
        LEFT JOIN price_history AS ph2
            ON ph1.from_asset = ph2.from_asset
            AND ABS(ph1.timestamp - ?) > ABS(ph2.timestamp - ?)
        WHERE ph1.from_asset IN ({})
            AND ph1.to_asset = ?
            AND ph1.timestamp BETWEEN ? AND ?
            AND ph2.from_asset IS NULL;
        """.format(','.join('?' * len(assets)))
        bindings = [
            timestamp,
            timestamp,
            *(asset.identifier for asset in assets),
            main_currency.identifier,
            timestamp - DAY_IN_SECONDS,
            timestamp + DAY_IN_SECONDS,
        ]

        with GlobalDBHandler().conn.read_ctx() as cursor:
            found_prices = {row[0]: FVal(row[1]) for row in cursor.execute(querystr, bindings)}
            for asset in assets:
                if asset.identifier in found_prices:
                    prices[asset] = found_prices[asset.identifier]
                elif asset == main_currency:
                    prices[asset] = ONE
                else:
                    missing_prices.append((asset.identifier, timestamp))

        return prices, missing_prices

    @staticmethod
    def _update_balances(
            event: HistoryEvent,
            current_balances: dict[Asset, FVal],
    ) -> tuple[str | int | None, str | None] | None:
        """Updates current balances for a history event, checking for negative balances.
        Zero balance assets are removed to avoid accumulating empty entries.

        Returns a tuple of identifier & group_identifier if a negative balance would occur,
        otherwise None.
        """
        if (
            (direction := event.maybe_get_direction()) is None or
            direction == EventDirection.NEUTRAL
        ):
            return None

        if direction == EventDirection.IN:
            current_balances[event.asset] += event.amount
        else:
            if current_balances[event.asset] - event.amount < ZERO:
                return event.identifier, event.group_identifier

            current_balances[event.asset] -= event.amount
            if current_balances[event.asset] == ZERO:
                del current_balances[event.asset]

        return None
