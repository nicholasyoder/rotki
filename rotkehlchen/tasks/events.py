import logging
from typing import TYPE_CHECKING, Final

from rotkehlchen.api.websockets.typedefs import WSMessageType
from rotkehlchen.chain.evm.decoding.monerium.constants import CPT_MONERIUM
from rotkehlchen.constants import HOUR_IN_SECONDS
from rotkehlchen.db.cache import ASSET_MOVEMENT_NO_MATCH_CACHE_VALUE, DBCacheDynamic, DBCacheStatic
from rotkehlchen.db.constants import CHAIN_EVENT_FIELDS, HISTORY_BASE_ENTRY_FIELDS
from rotkehlchen.db.filtering import HistoryEventFilterQuery
from rotkehlchen.db.history_events import DBHistoryEvents
from rotkehlchen.errors.serialization import DeserializationError
from rotkehlchen.globaldb.handler import GlobalDBHandler
from rotkehlchen.history.events.structures.asset_movement import AssetMovement
from rotkehlchen.history.events.structures.base import HistoryBaseEntry, HistoryBaseEntryType
from rotkehlchen.history.events.structures.onchain_event import OnchainEvent
from rotkehlchen.history.events.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.types import CHAINS_WITH_TRANSACTIONS, SupportedBlockchain, Timestamp
from rotkehlchen.utils.misc import ts_ms_to_sec, ts_now

if TYPE_CHECKING:
    from rotkehlchen.chain.aggregator import ChainsAggregator
    from rotkehlchen.db.dbhandler import DBHandler

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)

ASSET_MOVEMENT_MATCH_WINDOW: Final = HOUR_IN_SECONDS


def process_events(
        chains_aggregator: 'ChainsAggregator',
        database: 'DBHandler',
) -> None:
    """Processes all events and modifies/combines them or aggregates processing results

    This is supposed to be a generic processing task that can be requested or run periodically
    """
    if (eth2 := chains_aggregator.get_module('eth2')) is not None:
        eth2.combine_block_with_tx_events()
        eth2.refresh_activated_validators_deposits()

    with database.match_asset_movements_lock:
        match_asset_movements(database)

    with database.user_write() as write_cursor:
        database.set_static_cache(  # update last event processing timestamp
            write_cursor=write_cursor,
            name=DBCacheStatic.LAST_EVENTS_PROCESSING_TASK_TS,
            value=ts_now(),
        )


def _should_auto_ignore_movement(asset_movement: AssetMovement) -> bool:
    """Check if the given asset movement should be auto ignored.
    Returns True if the asset movement has a fiat asset, or if it is a movement to/from a
    blockchain that we will not have txs for. Otherwise returns False.
    """
    if asset_movement.asset.is_fiat():
        return True

    if (
        (extra_data := asset_movement.extra_data) is not None and
        (blockchain_str := extra_data.get('blockchain')) is not None
    ):
        try:
            return SupportedBlockchain.deserialize(blockchain_str) not in CHAINS_WITH_TRANSACTIONS
        except DeserializationError:
            return True  # not even a valid SupportedBlockchain

    return False


def match_asset_movements(database: 'DBHandler') -> None:
    """Analyze asset movements and find corresponding onchain events, then update those onchain
    events with proper event_type, counterparty, etc and cache the matched identifiers.
    """
    log.debug('Analyzing asset movements for corresponding onchain events...')
    events_db = DBHistoryEvents(database=database)
    asset_movements, fee_events = get_unmatched_asset_movements(database)
    unmatched_asset_movements, movement_ids_to_ignore = [], []
    for asset_movement in asset_movements:
        if _should_auto_ignore_movement(asset_movement=asset_movement):
            movement_ids_to_ignore.append(asset_movement.identifier)
            continue

        if len(matched_events := find_asset_movement_matches(
            events_db=events_db,
            asset_movement=asset_movement,
            is_deposit=(is_deposit := asset_movement.event_type == HistoryEventType.DEPOSIT),
            fee_event=fee_events.get(asset_movement.group_identifier),
        )) == 1:
            success, error_msg = update_asset_movement_matched_event(
                events_db=events_db,
                asset_movement=asset_movement,
                matched_event=matched_events[0],
                is_deposit=is_deposit,
            )
            if success:
                continue
            else:
                log.error(
                    f'Failed to match asset movement {asset_movement.group_identifier} '
                    f'due to: {error_msg}',
                )

        unmatched_asset_movements.append(asset_movement)

    if len(movement_ids_to_ignore) > 0:
        with events_db.db.conn.write_ctx() as write_cursor:
            write_cursor.executemany(
                'INSERT OR REPLACE INTO key_value_cache(name, value) VALUES(?, ?)',
                [(
                    DBCacheDynamic.MATCHED_ASSET_MOVEMENT.get_db_key(identifier=x),  # type: ignore[arg-type]  # identifiers will not be None since the events are from the db.
                    ASSET_MOVEMENT_NO_MATCH_CACHE_VALUE,
                ) for x in movement_ids_to_ignore],
            )

    if (unmatched_count := len(unmatched_asset_movements)) > 0:
        log.warning(f'Failed to match {unmatched_count} asset movements')
        database.msg_aggregator.add_message(
            message_type=WSMessageType.UNMATCHED_ASSET_MOVEMENTS,
            data={'count': unmatched_count},
        )


def get_unmatched_asset_movements(
        database: 'DBHandler',
) -> tuple[list[AssetMovement], dict[str, AssetMovement]]:
    """Get all asset movements that have not been matched yet. Returns a tuple containing the list
    of asset movements and a dict of the corresponding fee events keyed by their group_identifier.
    """
    asset_movements: list[AssetMovement] = []
    fee_events: dict[str, AssetMovement] = {}
    with database.conn.read_ctx() as cursor:
        for entry in cursor.execute(
                f'SELECT {HISTORY_BASE_ENTRY_FIELDS}, {CHAIN_EVENT_FIELDS} FROM history_events '
                'LEFT JOIN chain_events_info ON history_events.identifier=chain_events_info.identifier '  # noqa: E501
                'WHERE history_events.entry_type=? AND CAST(history_events.identifier AS TEXT) NOT IN '  # noqa: E501
                '(SELECT SUBSTR(name, ?) FROM key_value_cache WHERE name LIKE ?) '
                'ORDER BY timestamp DESC, sequence_index',
                (
                    HistoryBaseEntryType.ASSET_MOVEMENT_EVENT.serialize_for_db(),
                    len(DBCacheDynamic.MATCHED_ASSET_MOVEMENT.name) + 2,
                    'matched_asset_movement_%',
                ),
        ):
            if (asset_movement := AssetMovement.deserialize_from_db(entry[1:])).event_subtype == HistoryEventSubType.FEE:  # noqa: E501
                fee_events[asset_movement.group_identifier] = asset_movement
            else:
                asset_movements.append(asset_movement)

    return asset_movements, fee_events


def update_asset_movement_matched_event(
        events_db: DBHistoryEvents,
        asset_movement: AssetMovement,
        matched_event: HistoryBaseEntry,
        is_deposit: bool,
) -> tuple[bool, str]:
    """Update the given matched event with proper event_type, counterparty, etc and cache the
    event identifiers. Returns a tuple containing a boolean indicating success and a string
    containing any error message.
    """
    should_edit_notes = True
    if isinstance(matched_event, OnchainEvent):
        # This could also be a plain history event (i.e. a btc event, or custom event)
        # so only check/update the counterparty if the event supports it.
        if matched_event.counterparty == CPT_MONERIUM:
            should_edit_notes = False  # Monerium event notes contain important info.

        matched_event.counterparty = asset_movement.location.name.lower()
    elif isinstance(matched_event, AssetMovement):
        should_edit_notes = False  # Asset movement notes are autogenerated.

    # Modify the matched event
    if is_deposit:
        matched_event.event_type = HistoryEventType.WITHDRAWAL
        matched_event.event_subtype = HistoryEventSubType.REMOVE_ASSET
        notes = 'Withdraw {amount} {asset} from {location_label} to {exchange}'
    else:
        matched_event.event_type = HistoryEventType.DEPOSIT
        matched_event.event_subtype = HistoryEventSubType.DEPOSIT_ASSET
        notes = 'Deposit {amount} {asset} to {location_label} from {exchange}'

    if should_edit_notes:
        matched_event.notes = notes.format(
            amount=matched_event.amount,
            asset=matched_event.asset.resolve_to_asset_with_symbol().symbol,
            location_label=matched_event.location_label,
            exchange=asset_movement.location_label,
        )

    if matched_event.extra_data is None:
        matched_event.extra_data = {}

    matched_event.extra_data['matched_asset_movement'] = {
        'group_identifier': asset_movement.group_identifier,
        'exchange': asset_movement.location.serialize(),
        'exchange_name': asset_movement.location_label,
    }

    # Save the event and cache the matched identifiers
    with events_db.db.conn.write_ctx() as write_cursor:
        events_db.edit_history_event(
            write_cursor=write_cursor,
            event=matched_event,
        )
        events_db.db.set_dynamic_cache(  # type: ignore[call-overload]  # identifiers will not be None here since the events are from the db
            write_cursor=write_cursor,
            name=DBCacheDynamic.MATCHED_ASSET_MOVEMENT,
            value=matched_event.identifier,
            identifier=asset_movement.identifier,
        )

    return True, ''


def find_asset_movement_matches(
        events_db: DBHistoryEvents,
        asset_movement: AssetMovement,
        is_deposit: bool,
        fee_event: AssetMovement | None,
        match_window: int = ASSET_MOVEMENT_MATCH_WINDOW,
) -> list[HistoryBaseEntry]:
    """Find events that closely match what the corresponding event for the given asset movement
    should look like. Returns a list of events that match.
    """
    asset_movement_timestamp = ts_ms_to_sec(asset_movement.timestamp)
    type_and_subtype_combinations: list[tuple[HistoryEventType, HistoryEventSubType]] = [
        (HistoryEventType.WITHDRAWAL, HistoryEventSubType.REMOVE_ASSET),
        (HistoryEventType.DEPOSIT, HistoryEventSubType.DEPOSIT_ASSET),
    ]
    if is_deposit:
        type_and_subtype_combinations.append((HistoryEventType.SPEND, HistoryEventSubType.NONE))
        from_ts = Timestamp(asset_movement_timestamp - match_window)
        to_ts = Timestamp(asset_movement_timestamp)
    else:
        type_and_subtype_combinations.append((HistoryEventType.RECEIVE, HistoryEventSubType.NONE))
        from_ts = Timestamp(asset_movement_timestamp)
        to_ts = Timestamp(asset_movement_timestamp + match_window)

    with events_db.db.conn.read_ctx() as cursor:
        possible_matches = events_db.get_history_events_internal(
            cursor=cursor,
            filter_query=HistoryEventFilterQuery.make(
                assets=GlobalDBHandler.get_assets_in_same_collection(
                    identifier=asset_movement.asset.identifier,
                ),
                type_and_subtype_combinations=type_and_subtype_combinations,
                from_ts=from_ts,
                to_ts=to_ts,
            ),
        )

    close_matches: list[HistoryBaseEntry] = []
    for event in possible_matches:
        if (
            event.location == asset_movement.location and
            event.location_label == asset_movement.location_label
        ):  # skip events from the same exchange
            continue

        # Check for matching amount, or matching amount + fee for deposits. The fee doesn't need
        # to be included for withdrawals since the onchain event will happen after the fee is
        # already deducted and the amount should always match the main asset movement amount.
        if not (event.amount == asset_movement.amount or (
            is_deposit and
            fee_event is not None and
            fee_event.asset == asset_movement.asset and
            event.amount == (asset_movement.amount + fee_event.amount)
        )):
            log.debug(
                f'Excluding possible match for asset movement {asset_movement.group_identifier} '
                f'due to differing amount. Expected {asset_movement.amount} got {event.amount}',
            )
            continue

        close_matches.append(event)

    if len(close_matches) == 0:
        log.debug(f'No close matches found for asset movement {asset_movement.group_identifier}')
        return close_matches

    if len(close_matches) > 1:  # Multiple close matches. Check various other heuristics.
        asset_matches: list[HistoryBaseEntry] = []
        tx_ref_matches: list[HistoryBaseEntry] = []
        counterparty_matches: list[HistoryBaseEntry] = []
        for match in close_matches:
            # Maybe match by exact asset match (matched events can have any asset in the collection)  # noqa: E501
            if match.asset == asset_movement.asset:
                asset_matches.append(match)

            if isinstance(match, OnchainEvent):
                if (  # Maybe match by tx ref
                    asset_movement.extra_data is not None and
                    (tx_ref := asset_movement.extra_data.get('transaction_id')) is not None and
                    str(match.tx_ref) == tx_ref
                ):
                    tx_ref_matches.append(match)

                if match.counterparty is None or match.counterparty == CPT_MONERIUM:
                    # Events with a counterparty are usually not the correct match since they are
                    # part of a properly decoded onchain operation. Monerium is an exception.
                    counterparty_matches.append(match)

        for match_list in (tx_ref_matches, asset_matches, counterparty_matches):
            if len(match_list) == 1:
                return match_list

        log.debug(
            f'Multiple close matches found for '
            f'asset movement {asset_movement.group_identifier}.',
        )

    return close_matches
