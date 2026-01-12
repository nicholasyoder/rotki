import logging
from typing import TYPE_CHECKING, Final

from rotkehlchen.api.v1.types import IncludeExcludeFilterData
from rotkehlchen.api.websockets.typedefs import WSMessageType
from rotkehlchen.chain.evm.decoding.monerium.constants import CPT_MONERIUM
from rotkehlchen.constants import HOUR_IN_SECONDS
from rotkehlchen.db.cache import ASSET_MOVEMENT_NO_MATCH_CACHE_VALUE, DBCacheDynamic, DBCacheStatic
from rotkehlchen.db.constants import (
    CHAIN_EVENT_FIELDS,
    HISTORY_BASE_ENTRY_FIELDS,
    HISTORY_MAPPING_KEY_STATE,
    HISTORY_MAPPING_STATE_CUSTOMIZED,
)
from rotkehlchen.db.filtering import HistoryEventFilterQuery
from rotkehlchen.db.history_events import DBHistoryEvents
from rotkehlchen.db.settings import CachedSettings
from rotkehlchen.errors.serialization import DeserializationError
from rotkehlchen.fval import FVal
from rotkehlchen.globaldb.handler import GlobalDBHandler
from rotkehlchen.history.events.structures.asset_movement import AssetMovement
from rotkehlchen.history.events.structures.base import HistoryBaseEntry, HistoryBaseEntryType
from rotkehlchen.history.events.structures.onchain_event import OnchainEvent
from rotkehlchen.history.events.structures.types import (
    EventDirection,
    HistoryEventSubType,
    HistoryEventType,
)
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.types import CHAINS_WITH_TRANSACTIONS, SupportedBlockchain, Timestamp
from rotkehlchen.utils.misc import ts_ms_to_sec, ts_now

if TYPE_CHECKING:
    from rotkehlchen.chain.aggregator import ChainsAggregator
    from rotkehlchen.db.dbhandler import DBHandler

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)

ASSET_MOVEMENT_MATCH_WINDOW: Final = HOUR_IN_SECONDS


def process_eth2_events(
        chains_aggregator: 'ChainsAggregator',
        database: 'DBHandler',
) -> None:
    """Process ETH2 events and maybe modify or combine them with corresponding tx events."""
    if (eth2 := chains_aggregator.get_module('eth2')) is not None:
        eth2.combine_block_with_tx_events()
        eth2.refresh_activated_validators_deposits()

    with database.user_write() as write_cursor:
        database.set_static_cache(  # update last eth2 event processing timestamp
            write_cursor=write_cursor,
            name=DBCacheStatic.LAST_ETH2_EVENTS_PROCESSING_TS,
            value=ts_now(),
        )


def process_asset_movements(database: 'DBHandler') -> None:
    with database.match_asset_movements_lock:
        match_asset_movements(database)

    with database.user_write() as write_cursor:
        database.set_static_cache(  # update last asset movement processing timestamp
            write_cursor=write_cursor,
            name=DBCacheStatic.LAST_ASSET_MOVEMENT_PROCESSING_TS,
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


def _maybe_adjust_fee(
        events_db: DBHistoryEvents,
        asset_movement: AssetMovement,
        matched_event: HistoryBaseEntry,
        is_deposit: bool,
) -> None:
    """Add/edit the fee to cover the difference between the amounts of the movement and its match.
    Takes no action if the amounts match, if existing fees already cover the difference, or if
    the amounts are off in the wrong direction where more is received than was sent (can only
    happen in a manual match).
    """
    if asset_movement.amount == matched_event.amount or (
        asset_movement.amount > matched_event.amount and is_deposit
    ) or (
        asset_movement.amount < matched_event.amount and not is_deposit
    ):
        return  # Don't add a fee if the amount is the same or is off in the wrong direction

    # Get any existing fees
    with events_db.db.conn.read_ctx() as cursor:
        fee_events = events_db.get_history_events_internal(
            cursor=cursor,
            filter_query=HistoryEventFilterQuery.make(
                entry_types=IncludeExcludeFilterData([HistoryBaseEntryType.ASSET_MOVEMENT_EVENT]),
                group_identifiers=[
                    asset_movement.group_identifier,
                    matched_event.group_identifier,  # include the matched event since it may also be an asset movement.  # noqa: E501
                ],
                assets=(asset_movement.asset,),
                event_subtypes=[HistoryEventSubType.FEE],
            ),
        )

    amount_diff = abs(asset_movement.amount - matched_event.amount)
    movement_fee = None
    for fee_event in fee_events:
        if fee_event.amount == amount_diff:
            return  # An existing fee already covers the amount difference. May happen in several
            # cases: A deposit where the fee is taken from the onchain amount, when a user manually
            # unlinks a match but then re-matches them, or when processing the second movement
            # when matching an asset movement with another asset movement.

        if fee_event.group_identifier == asset_movement.group_identifier:
            movement_fee = fee_event  # There can only be one fee per movement
            # Don't break since there may also be a fee present from the matched event.

    # Create or edit the movement's fee event
    with events_db.db.conn.write_ctx() as write_cursor:
        if movement_fee is None:
            events_db.add_history_event(
                write_cursor=write_cursor,
                event=AssetMovement(
                    timestamp=asset_movement.timestamp,
                    location=asset_movement.location,
                    event_type=asset_movement.event_type,  # type: ignore[arg-type]  # asset movement will have type of either deposit or withdraw
                    asset=asset_movement.asset,
                    amount=amount_diff,
                    group_identifier=asset_movement.group_identifier,
                    is_fee=True,
                    location_label=asset_movement.location_label,
                ),
                mapping_values={HISTORY_MAPPING_KEY_STATE: HISTORY_MAPPING_STATE_CUSTOMIZED},
            )
        else:
            movement_fee.amount += amount_diff
            events_db.edit_history_event(
                write_cursor=write_cursor,
                event=movement_fee,
            )


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

    _maybe_adjust_fee(
        events_db=events_db,
        asset_movement=asset_movement,
        matched_event=matched_event,
        is_deposit=is_deposit,
    )

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


def should_exclude_possible_match(
        asset_movement: AssetMovement,
        event: HistoryBaseEntry,
        exclude_unexpected_direction: bool = False,
) -> bool:
    """Check if the given event should be excluded from being a possible match.
    Returns True in the following cases:
    - Event is from the same exchange as the asset movement
    - Event is an INFORMATIONAL/APPROVE event
    - exclude_unexpected_direction is True and the event has the opposite direction of what would
       be expected based on the asset movement's type. This is used during automatic matching but
       is not used when getting possible matches for manual matching since there may be edge cases
       where an event was customized with the wrong event types.
    """
    return (
        event.location == asset_movement.location and
        event.location_label == asset_movement.location_label
    ) or (
        event.event_type == HistoryEventType.INFORMATIONAL and
        event.event_subtype == HistoryEventSubType.APPROVE
    ) or (
        exclude_unexpected_direction and
        (direction := event.maybe_get_direction()) != EventDirection.NEUTRAL and
        direction != (EventDirection.OUT if asset_movement.event_type == HistoryEventType.DEPOSIT else EventDirection.IN)  # noqa: E501
    )


def _match_amount(
        movement_amount: FVal,
        event_amount: FVal,
        tolerance: FVal,
        is_deposit: bool,
) -> bool:
    """Check for matching amounts with the given tolerance as long as there
    was not more received than was sent (as determined by is_deposit).
    """
    return movement_amount == event_amount or (
        (
            (movement_amount < event_amount and is_deposit) or
            (movement_amount > event_amount and not is_deposit)
        ) and
        abs(movement_amount - event_amount) <= movement_amount * tolerance
    )


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
    if is_deposit:
        from_ts = Timestamp(asset_movement_timestamp - match_window)
        to_ts = Timestamp(asset_movement_timestamp)
    else:
        from_ts = Timestamp(asset_movement_timestamp)
        to_ts = Timestamp(asset_movement_timestamp + match_window)

    with events_db.db.conn.read_ctx() as cursor:
        possible_matches = events_db.get_history_events_internal(
            cursor=cursor,
            filter_query=HistoryEventFilterQuery.make(
                assets=GlobalDBHandler.get_assets_in_same_collection(
                    identifier=asset_movement.asset.identifier,
                ),
                from_ts=from_ts,
                to_ts=to_ts,
                entry_types=IncludeExcludeFilterData(
                    values=[  # Don't include eth staking events
                        HistoryBaseEntryType.ETH_BLOCK_EVENT,
                        HistoryBaseEntryType.ETH_DEPOSIT_EVENT,
                        HistoryBaseEntryType.ETH_WITHDRAWAL_EVENT,
                    ],
                    operator='NOT IN',
                ),
            ),
        )

    close_matches: list[HistoryBaseEntry] = []
    tolerance = CachedSettings().get_settings().asset_movement_amount_tolerance
    for event in possible_matches:
        if should_exclude_possible_match(
            asset_movement=asset_movement,
            event=event,
            exclude_unexpected_direction=True,
        ):
            continue

        # Check for matching amount, or matching amount + fee for deposits. The fee doesn't need
        # to be included for withdrawals since the onchain event will happen after the fee is
        # already deducted and the amount should always match the main asset movement amount.
        # Also allow a small tolerance as long as the received amount is less
        # than the sent amount. A fee event will be added later to account for the difference.
        if not (_match_amount(
            movement_amount=asset_movement.amount,
            event_amount=event.amount,
            tolerance=tolerance,
            is_deposit=is_deposit,
        ) or (
            is_deposit and
            fee_event is not None and
            fee_event.asset == asset_movement.asset and
            _match_amount(
                movement_amount=asset_movement.amount + fee_event.amount,
                event_amount=event.amount,
                tolerance=tolerance,
                is_deposit=True,
            )
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
