from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from rotkehlchen.api.websockets.typedefs import WSMessageType
from rotkehlchen.assets.asset import Asset
from rotkehlchen.chain.evm.decoding.monerium.constants import CPT_MONERIUM
from rotkehlchen.constants import HOUR_IN_SECONDS, ONE
from rotkehlchen.constants.assets import A_BTC, A_ETH, A_USD, A_USDC, A_WETH_OPT, A_WSOL
from rotkehlchen.db.cache import ASSET_MOVEMENT_NO_MATCH_CACHE_VALUE, DBCacheDynamic
from rotkehlchen.db.filtering import HistoryEventFilterQuery
from rotkehlchen.db.history_events import DBHistoryEvents
from rotkehlchen.db.settings import DEFAULT_ASSET_MOVEMENT_TIME_RANGE, ModifiableDBSettings
from rotkehlchen.fval import FVal
from rotkehlchen.history.events.structures.asset_movement import (
    AssetMovement,
    AssetMovementExtraData,
)
from rotkehlchen.history.events.structures.base import HistoryEvent
from rotkehlchen.history.events.structures.evm_event import EvmEvent
from rotkehlchen.history.events.structures.onchain_event import OnchainEvent
from rotkehlchen.history.events.structures.solana_event import SolanaEvent
from rotkehlchen.history.events.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.tasks.events import match_asset_movements
from rotkehlchen.tests.fixtures import MockedWsMessage
from rotkehlchen.tests.unit.test_eth2 import HOUR_IN_MILLISECONDS
from rotkehlchen.tests.utils.factories import (
    make_evm_address,
    make_evm_tx_hash,
    make_solana_address,
    make_solana_signature,
)
from rotkehlchen.types import ChecksumEvmAddress, Location, Timestamp, TimestampMS
from rotkehlchen.utils.misc import ts_sec_to_ms

if TYPE_CHECKING:
    from rotkehlchen.db.dbhandler import DBHandler


def _match_and_check(database: 'DBHandler', expected_matches: list[tuple[int, int]]) -> None:
    """Helper function for testing that the expected events are properly matched."""
    match_asset_movements(database=database)
    with database.conn.read_ctx() as cursor:
        assert set(cursor.execute(
            'SELECT * FROM key_value_cache WHERE name LIKE ?',
            ('matched_asset_movement_%',),
        ).fetchall()) == {
            (f'matched_asset_movement_{movement_id}', str(match_id))
            for movement_id, match_id in expected_matches
        }


@pytest.mark.parametrize('function_scope_initialize_mock_rotki_notifier', [True])
def test_match_asset_movements(database: 'DBHandler') -> None:
    """Test that the asset movement matching logic works correctly.

    Adds some test events to the DB, runs the matching, and checks that events were properly
    matched and updated. Then runs the matching again to check that already matched events
    are properly excluded from subsequent matching.
    """
    events_db = DBHistoryEvents(database)
    with database.conn.write_ctx() as write_cursor:
        events_db.add_history_events(
            write_cursor=write_cursor,
            history=[AssetMovement(  # deposit1, Fiat, should be auto ignored
                location=Location.GEMINI,
                event_type=HistoryEventType.DEPOSIT,
                timestamp=TimestampMS(1500000000000),
                asset=A_USD,
                amount=FVal('100'),
                unique_id='1',
                location_label='Gemini 1',
            ), (deposit2 := AssetMovement(  # deposit2, two matches, one with tx ref
                location=Location.GEMINI,
                event_type=HistoryEventType.DEPOSIT,
                timestamp=TimestampMS(1510000000000),
                asset=A_ETH,
                amount=FVal('0.1'),
                unique_id='2',
                extra_data=AssetMovementExtraData(
                    blockchain='eth',
                    transaction_id=str(tx_ref := make_evm_tx_hash()),
                ),
                location_label='Gemini 2',
            )), EvmEvent(  # deposit2's matched event, same tx ref
                tx_ref=tx_ref,
                sequence_index=0,
                timestamp=TimestampMS(deposit2.timestamp - 60000),  # timestamp differs some but is < 1 hour different.  # noqa: E501
                location=Location.ETHEREUM,
                event_type=HistoryEventType.SPEND,
                event_subtype=HistoryEventSubType.NONE,
                asset=A_ETH,
                amount=FVal('0.1'),
                counterparty=CPT_MONERIUM,
                notes='Important info',
                location_label=make_evm_address(),
            ), (deposit_2_wrong_ref_event := EvmEvent(  # deposit2 similar event, wrong tx ref
                tx_ref=make_evm_tx_hash(),
                sequence_index=0,
                timestamp=deposit2.timestamp,
                location=Location.ETHEREUM,
                event_type=HistoryEventType.SPEND,
                event_subtype=HistoryEventSubType.NONE,
                asset=A_ETH,
                amount=FVal('0.1'),
            )), (withdrawal1 := AssetMovement(  # withdrawal1, with matched event
                location=Location.COINBASE,
                event_type=HistoryEventType.WITHDRAWAL,
                timestamp=TimestampMS(1520000000000),
                asset=A_USDC,
                amount=FVal('0.2'),
                unique_id='3',
                location_label='Coinbase 1',
            )), EvmEvent(  # withdrawal1's matched event, already a deposit/deposit_asset, but notes and counterparty will be updated.  # noqa: E501
                tx_ref=make_evm_tx_hash(),
                sequence_index=0,
                timestamp=withdrawal1.timestamp,
                location=Location.ARBITRUM_ONE,
                event_type=HistoryEventType.DEPOSIT,
                event_subtype=HistoryEventSubType.DEPOSIT_ASSET,
                asset=A_USDC,
                amount=FVal('0.2'),
                location_label=(withdrawal1_user_address := make_evm_address()),
            ), (withdrawal1_wrong_amount_event := EvmEvent(  # similar to withdrawal1's matched event, but the amount is wrong  # noqa: E501
                tx_ref=make_evm_tx_hash(),
                sequence_index=0,
                timestamp=withdrawal1.timestamp,
                location=Location.ARBITRUM_ONE,
                event_type=HistoryEventType.DEPOSIT,
                event_subtype=HistoryEventSubType.DEPOSIT_ASSET,
                asset=A_USDC,
                amount=FVal('0.21'),
            )), (withdrawal1_wrong_ts_event := EvmEvent(  # similar to withdrawal1's matched event, but timestamp is farther away than the time range  # noqa: E501
                tx_ref=make_evm_tx_hash(),
                sequence_index=0,
                timestamp=TimestampMS(withdrawal1.timestamp + ts_sec_to_ms(Timestamp(DEFAULT_ASSET_MOVEMENT_TIME_RANGE + 1))),  # noqa: E501
                location=Location.ARBITRUM_ONE,
                event_type=HistoryEventType.DEPOSIT,
                event_subtype=HistoryEventSubType.DEPOSIT_ASSET,
                asset=A_USDC,
                amount=FVal('0.2'),
            )), (withdrawal2 := AssetMovement(  # withdrawal2, with two similar events
                location=Location.KRAKEN,
                event_type=HistoryEventType.WITHDRAWAL,
                timestamp=TimestampMS(1530000000000),
                asset=A_USDC,
                amount=FVal('0.3'),
                unique_id='4',
                location_label='Kraken 1',
            )), EvmEvent(  # withdrawal2 possible event 1
                tx_ref=make_evm_tx_hash(),
                sequence_index=0,
                timestamp=withdrawal2.timestamp,
                location=Location.BASE,
                event_type=HistoryEventType.RECEIVE,
                event_subtype=HistoryEventSubType.NONE,
                asset=A_USDC,
                amount=FVal('0.3'),
            ), EvmEvent(  # withdrawal2 possible event 2
                tx_ref=make_evm_tx_hash(),
                sequence_index=0,
                timestamp=withdrawal2.timestamp,
                location=Location.BASE,
                event_type=HistoryEventType.RECEIVE,
                event_subtype=HistoryEventSubType.NONE,
                asset=A_USDC,
                amount=FVal('0.3'),
            ), (withdrawal3 := AssetMovement(  # withdrawal3, no matched events
                location=Location.BYBIT,
                event_type=HistoryEventType.WITHDRAWAL,
                timestamp=TimestampMS(1540000000000),
                asset=A_BTC,
                amount=FVal('0.4'),
                unique_id='5',
                location_label='Bybit 1',
            )), (deposit3 := AssetMovement(  # deposit3, with fee
                location=Location.BYBIT,
                event_type=HistoryEventType.DEPOSIT,
                timestamp=TimestampMS(1550000000000),
                asset=A_USDC,
                amount=FVal('99'),
                unique_id='6',
                location_label='Bybit 1',
            )), AssetMovement(  # deposit3 fee
                location=Location.BYBIT,
                event_type=HistoryEventType.DEPOSIT,
                timestamp=TimestampMS(1550000000000),
                asset=A_USDC,
                amount=FVal('1'),
                unique_id='6',
                location_label='Bybit 1',
                is_fee=True,
            ), SolanaEvent(  # deposit3 match, amount includes fee
                tx_ref=make_solana_signature(),
                sequence_index=0,
                timestamp=TimestampMS(1550000000000),
                location=Location.SOLANA,
                event_type=HistoryEventType.SPEND,
                event_subtype=HistoryEventSubType.NONE,
                asset=A_USDC,
                amount=FVal('100'),
                location_label=(deposit3_user_address := make_solana_address()),
            ), AssetMovement(  # deposit5, for blockchain that will have no transactions
                location=Location.GEMINI,
                event_type=HistoryEventType.DEPOSIT,
                timestamp=TimestampMS(1555000000000),
                asset=A_ETH,
                amount=FVal('0.6'),
                unique_id='9',
                location_label='Gemini 1',
                extra_data=AssetMovementExtraData(blockchain='monero'),
            ), (withdrawal4 := AssetMovement(  # withdrawal4, with another asset movement for the matched event  # noqa: E501
                location=Location.BITSTAMP,
                event_type=HistoryEventType.WITHDRAWAL,
                timestamp=TimestampMS(1560000000000),
                asset=A_USDC,
                amount=FVal('5.5'),
                unique_id='7',
                location_label='Bitstamp 1',
            )), (withdrawal4_matched_event := AssetMovement(  # withdrawal4's matched event
                location=Location.KRAKEN,
                event_type=HistoryEventType.DEPOSIT,
                timestamp=TimestampMS(1560000000001),
                asset=A_USDC,
                amount=FVal('5.49'),  # Slightly different amount but within the tolerance so will still auto match and add a fee event to cover the difference. # noqa: E501
                unique_id='8',
                location_label='Kraken 1',
            ))],
        )

    match_asset_movements(database=database)

    with database.conn.read_ctx() as cursor:
        all_events = events_db.get_history_events_internal(
            cursor=cursor,
            filter_query=HistoryEventFilterQuery.make(order_by_rules=[
                ('timestamp', True),
                ('sequence_index', True),
                ('history_events_identifier', True),
            ]),
        )

    asset_movements = [
        event for event in all_events
        if isinstance(event, (AssetMovement, HistoryEvent))
    ]  # include plain history events so the adjustments are included.
    events = [event for event in all_events if isinstance(event, OnchainEvent)]

    assert len(events) == 8
    # Corresponding event for deposit2
    assert (deposit2_matched_event := events[0]).event_type == HistoryEventType.WITHDRAWAL
    assert deposit2_matched_event.event_subtype == HistoryEventSubType.REMOVE_ASSET
    assert deposit2_matched_event.notes == 'Important info'  # Notes shouldn't be updated on monerium events.  # noqa: E501
    assert deposit2_matched_event.counterparty == Location.GEMINI.name.lower()
    # Second event matching deposit2 but with the wrong ref. Unmodified.
    # (except for identifier since its from the db here)
    deposit_2_wrong_ref_event.identifier = events[1].identifier
    assert events[1] == deposit_2_wrong_ref_event
    # Corresponding event for withdrawal1
    assert (withdrawal1_matched_event := events[2]).event_type == HistoryEventType.DEPOSIT
    assert withdrawal1_matched_event.event_subtype == HistoryEventSubType.DEPOSIT_ASSET
    assert withdrawal1_matched_event.notes == f'Deposit 0.2 USDC to {withdrawal1_user_address} from Coinbase 1'  # noqa: E501
    assert withdrawal1_matched_event.counterparty == Location.COINBASE.name.lower()
    # Second event matching withdrawal1 but with the wrong amount. Unmodified.
    withdrawal1_wrong_amount_event.identifier = events[3].identifier
    assert events[3] == withdrawal1_wrong_amount_event
    # Third event matching withdrawal1 but with the wrong timestamp. Unmodified.
    withdrawal1_wrong_ts_event.identifier = events[4].identifier
    assert events[4] == withdrawal1_wrong_ts_event
    # The next two events are related to withdrawal2,
    # but neither are modified since both match.
    assert all(event.notes is None for event in events[5:7])
    # Corresponding event for deposit3
    assert (deposit3_matched_event := events[7]).event_type == HistoryEventType.WITHDRAWAL
    assert deposit3_matched_event.event_subtype == HistoryEventSubType.REMOVE_ASSET
    assert deposit3_matched_event.notes == f'Withdraw 100 USDC from {deposit3_user_address} to Bybit 1'  # noqa: E501
    assert deposit3_matched_event.counterparty == Location.BYBIT.name.lower()

    # Last two events should be withdrawal4's matched event and a new adjustment event to cover the
    # difference between withdrawal4 and its matched event. Note that since the matched event is
    # also an asset movement in this case, the adjustment is actually added to the group of the
    # matched event since it gets processed first.
    withdrawal4_matched_event.identifier = asset_movements[-2].identifier
    withdrawal4_matched_event.extra_data = AssetMovementExtraData(matched_asset_movement={
        'group_identifier': withdrawal4.group_identifier,
        'exchange': 'bitstamp',
        'exchange_name': 'Bitstamp 1',
    })
    assert asset_movements[-2] == withdrawal4_matched_event
    assert (withdrawal4_adjustment := asset_movements[-1]).event_type == HistoryEventType.EXCHANGE_ADJUSTMENT  # noqa: E501
    assert withdrawal4_adjustment.event_subtype == HistoryEventSubType.RECEIVE
    assert withdrawal4_adjustment.group_identifier == withdrawal4_matched_event.group_identifier
    assert withdrawal4_adjustment.amount == withdrawal4.amount - withdrawal4_matched_event.amount

    # Check that matches have been cached and that the cached identifiers
    # refer to the correct asset movements (ordered by timestamp descending)
    deposit_1_identifier = 1
    deposit_2_identifier = 2
    withdrawal_1_identifier = 5
    deposit_3_identifier = 13
    deposit_4_identifier = 16
    withdrawal4_identifier = 17
    with database.conn.read_ctx() as cursor:
        assert cursor.execute(
            'SELECT * FROM key_value_cache WHERE name LIKE ?',
            ('matched_asset_movement_%',),
        ).fetchall() == [
            (f'matched_asset_movement_{withdrawal4_matched_event.identifier}', str(withdrawal4_identifier)),  # noqa: E501
            (f'matched_asset_movement_{withdrawal4_identifier}', str(withdrawal4_matched_event.identifier)),  # noqa: E501
            (f'matched_asset_movement_{deposit_3_identifier}', str(deposit3_matched_event.identifier)),  # noqa: E501
            (f'matched_asset_movement_{withdrawal_1_identifier}', str(withdrawal1_matched_event.identifier)),  # noqa: E501
            (f'matched_asset_movement_{deposit_2_identifier}', str(deposit2_matched_event.identifier)),  # noqa: E501
            (f'matched_asset_movement_{deposit_4_identifier}', str(ASSET_MOVEMENT_NO_MATCH_CACHE_VALUE)),  # noqa: E501
            (f'matched_asset_movement_{deposit_1_identifier}', str(ASSET_MOVEMENT_NO_MATCH_CACHE_VALUE)),  # noqa: E501
        ]
        matched_asset_movements = events_db.get_history_events_internal(
            cursor=cursor,
            filter_query=HistoryEventFilterQuery.make(identifiers=[
                deposit_2_identifier,
                withdrawal_1_identifier,
                deposit_3_identifier,
                withdrawal4_identifier,
            ], order_by_rules=[('history_events_identifier', True)]),
        )
    assert len(matched_asset_movements) == 4
    deposit2.identifier = deposit_2_identifier
    assert matched_asset_movements[0] == deposit2
    withdrawal1.identifier = withdrawal_1_identifier
    assert matched_asset_movements[1] == withdrawal1
    deposit3.identifier = deposit_3_identifier
    assert matched_asset_movements[2] == deposit3
    # since withdrawal4 is part of an exchange to exchange movement, it is also the matched event
    # for the other asset movement and has the matched_asset_movement extra data.
    withdrawal4.extra_data = AssetMovementExtraData(matched_asset_movement={
        'group_identifier': withdrawal4_matched_event.group_identifier,
        'exchange': 'kraken',
        'exchange_name': 'Kraken 1',
    })
    withdrawal4.identifier = withdrawal4_identifier
    assert matched_asset_movements[3] == withdrawal4

    # Check that the unmatched movements ws message was sent
    assert database.msg_aggregator.rotki_notifier.pop_message() == MockedWsMessage(  # type: ignore  # pop_message will be present since it's a MockRotkiNotifier
        message_type=WSMessageType.UNMATCHED_ASSET_MOVEMENTS,
        data={'count': (unmatched_count := 2)},
    )

    # Check that the matching logic is now only run for unmatched asset movements
    with patch('rotkehlchen.tasks.events.find_asset_movement_matches', return_value=[]) as find_match_mock:  # noqa: E501
        match_asset_movements(database=database)

    assert find_match_mock.call_count == unmatched_count
    # Processed in order of descending timestamp: withdrawal3, withdrawal2, deposit1
    withdrawal3.identifier = 12
    assert find_match_mock.call_args_list[0].kwargs['asset_movement'] == withdrawal3
    withdrawal2.identifier = 9
    assert find_match_mock.call_args_list[1].kwargs['asset_movement'] == withdrawal2

    # Check that the modified matched events are not removed when resetting for redecode
    assert deposit2_matched_event.identifier is not None
    with database.conn.write_ctx() as write_cursor:
        events_db.reset_events_for_redecode(write_cursor, Location.ETHEREUM)
        assert len(events_db.get_history_events_internal(
            cursor=write_cursor,
            filter_query=HistoryEventFilterQuery.make(
                identifiers=[deposit2_matched_event.identifier],
            ),
        )) == 1


def test_match_asset_movements_settings(database: 'DBHandler') -> None:
    """Test that the amount tolerance and time range settings works correctly, with the match
    failing when tolerance or time range is too small but succeeding with higher values.
    """
    events_db = DBHistoryEvents(database)
    with database.conn.write_ctx() as write_cursor:
        events_db.add_history_events(
            write_cursor=write_cursor,
            history=[(movement_event := AssetMovement(
                identifier=(movement_id := 1),
                location=Location.KRAKEN,
                event_type=HistoryEventType.WITHDRAWAL,
                timestamp=TimestampMS(1520000000000),
                asset=A_USDC,
                amount=FVal('0.2'),
                unique_id='xyz',
                location_label='Kraken 1',
            )), (matched_event := EvmEvent(
                identifier=(match_id := 2),
                tx_ref=make_evm_tx_hash(),
                sequence_index=0,
                timestamp=TimestampMS(movement_event.timestamp + HOUR_IN_MILLISECONDS * 2),
                location=Location.ARBITRUM_ONE,
                event_type=HistoryEventType.RECEIVE,
                event_subtype=HistoryEventSubType.NONE,
                asset=A_USDC,
                amount=FVal('0.199'),  # Differs by 0.001 (0.5%)
                location_label=make_evm_address(),
            ))],
        )

    for tolerance, time_range, expected_value in (
        (FVal('0.01'), HOUR_IN_SECONDS, None),  # ok tolerance, but range too small - Fail
        (FVal('0.0001'), HOUR_IN_SECONDS * 3, None),  # ok range, but tolerance too low - Fail
        (FVal('0.01'), HOUR_IN_SECONDS * 3, match_id),  # ok tolerance, and ok range - Success
    ):
        with database.user_write() as write_cursor:
            database.set_settings(
                write_cursor=write_cursor,
                settings=ModifiableDBSettings(
                    asset_movement_amount_tolerance=tolerance,
                    asset_movement_time_range=time_range,
                ),
            )

        match_asset_movements(database=database)
        with database.conn.read_ctx() as cursor:
            assert database.get_dynamic_cache(
                cursor=cursor,
                name=DBCacheDynamic.MATCHED_ASSET_MOVEMENT,
                identifier=movement_id,
            ) == expected_value

    # Verify the adjustment event was created properly
    with database.conn.read_ctx() as cursor:
        all_events = events_db.get_history_events_internal(
            cursor=cursor,
            filter_query=HistoryEventFilterQuery.make(),
        )

    assert len(all_events) == 3
    assert all_events[0].group_identifier == movement_event.group_identifier
    assert all_events[1].group_identifier == movement_event.group_identifier
    assert all_events[1].event_type == HistoryEventType.EXCHANGE_ADJUSTMENT
    assert all_events[1].event_subtype == HistoryEventSubType.RECEIVE
    assert all_events[1].amount == movement_event.amount - matched_event.amount
    assert all_events[2].group_identifier == matched_event.group_identifier


def test_auto_ignore_by_asset(database: 'DBHandler') -> None:
    """Test that movements are auto-ignored if their asset is for an unsupported chain."""
    events_db = DBHistoryEvents(database)
    with database.conn.write_ctx() as write_cursor:
        events_db.add_history_events(
            write_cursor=write_cursor,
            history=[AssetMovement(
                identifier=idx + 1,
                location=Location.KRAKEN,
                event_type=HistoryEventType.WITHDRAWAL,
                timestamp=TimestampMS(1520000000000),
                asset=asset,
                amount=ONE,
                unique_id=f'xyz{idx}',
                location_label='Kraken 1',
            ) for idx, asset in enumerate([
                A_BTC,  # Native token for supported chain
                A_WETH_OPT,  # EVM token from a supported chain
                A_WSOL,  # Solana token
                Asset('ICP'),  # Native token for unsupported chain
                Asset('eip155:250/erc20:0xc60D7067dfBc6f2caf30523a064f416A5Af52963'),  # Unsupported EVM chain.  # noqa: E501
                Asset('STRK'),  # STRK is an unsupported chain but another token in the collection
                # is from a supported chain, so don't ignore since it may be either token.
            ])],
        )

    match_asset_movements(database=database)
    with database.conn.read_ctx() as cursor:
        assert cursor.execute(
            'SELECT * FROM key_value_cache WHERE name LIKE ?',
            ('matched_asset_movement_%',),
        ).fetchall() == [  # only 4 & 5 (ICP, and unsupported EVM chain token) are ignored
            ('matched_asset_movement_4', str(ASSET_MOVEMENT_NO_MATCH_CACHE_VALUE)),
            ('matched_asset_movement_5', str(ASSET_MOVEMENT_NO_MATCH_CACHE_VALUE)),
        ]


@pytest.mark.parametrize('number_of_arbitrum_one_accounts', [2])
def test_ignore_transfers_between_tracked_accounts(
        database: 'DBHandler',
        arbitrum_one_accounts: list[ChecksumEvmAddress],
) -> None:
    """Test that transfers between tracked accounts are not included as possible matches."""
    events_db = DBHistoryEvents(database)
    with database.conn.write_ctx() as write_cursor:
        events_db.add_history_events(
            write_cursor=write_cursor,
            history=[(movement_event := AssetMovement(
                identifier=(movement_id := 1),
                location=Location.KRAKEN,
                event_type=HistoryEventType.WITHDRAWAL,
                timestamp=TimestampMS(1520000000000),
                asset=A_USDC,
                amount=FVal('25'),
                unique_id='xyz',
                location_label='Kraken 1',
            )), EvmEvent(  # Matched event
                identifier=(match_id := 2),
                tx_ref=make_evm_tx_hash(),
                sequence_index=0,
                timestamp=TimestampMS(movement_event.timestamp + 1),
                location=Location.ARBITRUM_ONE,
                event_type=HistoryEventType.RECEIVE,
                event_subtype=HistoryEventSubType.NONE,
                asset=movement_event.asset,
                amount=movement_event.amount,
                location_label=arbitrum_one_accounts[0],
            ), EvmEvent(  # Ignored transfer between tracked addresses
                identifier=3,
                tx_ref=make_evm_tx_hash(),
                sequence_index=0,
                timestamp=TimestampMS(movement_event.timestamp + 2),
                location=Location.ARBITRUM_ONE,
                event_type=HistoryEventType.TRANSFER,
                event_subtype=HistoryEventSubType.NONE,
                asset=movement_event.asset,
                amount=movement_event.amount,
                location_label=arbitrum_one_accounts[0],
                address=arbitrum_one_accounts[1],
            )],
        )

    # Run matching and check that it matched properly with the receive event instead of seeing
    # the transfer event as a second close match.
    _match_and_check(database=database, expected_matches=[(movement_id, match_id)])


def test_timestamp_tolerance(database: 'DBHandler') -> None:
    """Test that events that are not on the expected side of the asset movement can still be
    auto matched as long as they are within the 1 hour tolerance.
    """
    events_db = DBHistoryEvents(database)
    with database.conn.write_ctx() as write_cursor:
        events_db.add_history_events(
            write_cursor=write_cursor,
            history=[(movement_event := AssetMovement(
                identifier=(movement_id := 1),
                location=Location.KRAKEN,
                event_type=HistoryEventType.WITHDRAWAL,
                timestamp=TimestampMS(1520000000000),
                asset=A_USDC,
                amount=FVal('25'),
                unique_id='xyz',
                location_label='Kraken 1',
            )), EvmEvent(  # Matched event. Timestamp is before movement but within tolerance
                identifier=(match_id := 2),
                tx_ref=make_evm_tx_hash(),
                sequence_index=0,
                timestamp=TimestampMS(movement_event.timestamp - 10),
                location=Location.ETHEREUM,
                event_type=HistoryEventType.RECEIVE,
                event_subtype=HistoryEventSubType.NONE,
                asset=movement_event.asset,
                amount=movement_event.amount,
            ), EvmEvent(  # Ignored event. Timestamp is before movement outside tolerance
                identifier=3,
                tx_ref=make_evm_tx_hash(),
                sequence_index=0,
                timestamp=TimestampMS(movement_event.timestamp - HOUR_IN_MILLISECONDS * 2),
                location=Location.ETHEREUM,
                event_type=HistoryEventType.RECEIVE,
                event_subtype=HistoryEventSubType.NONE,
                asset=movement_event.asset,
                amount=movement_event.amount,
            )],
        )

    # Run matching and check that it matched properly with the event inside the tolerance.
    _match_and_check(database=database, expected_matches=[(movement_id, match_id)])


def test_adjustments(database: 'DBHandler') -> None:
    """Test that we properly create adjustment events during matching if amounts differ."""
    events_db = DBHistoryEvents(database)

    events_to_add = []
    for idx, (asset, movement_type, movement_amount, match_amount) in enumerate([
        (A_ETH, HistoryEventType.DEPOSIT, 5.49, 5.5),
        (A_BTC, HistoryEventType.DEPOSIT, 5.5, 5.49),
        (A_USDC, HistoryEventType.WITHDRAWAL, 5.49, 5.5),
        (A_WSOL, HistoryEventType.WITHDRAWAL, 5.5, 5.49),
    ]):
        events_to_add.extend([(movement_event := AssetMovement(
            location=Location.KRAKEN,
            event_type=movement_type,  # type: ignore[arg-type]  # will be deposit or withdrawal
            timestamp=TimestampMS(1600000000000 + idx),
            asset=asset,
            amount=FVal(movement_amount),
            location_label='kraken',
        )), HistoryEvent(  # Existing adjustment event should be replaced
            group_identifier=movement_event.group_identifier,
            sequence_index=1,
            timestamp=movement_event.timestamp,
            location=movement_event.location,
            event_type=HistoryEventType.EXCHANGE_ADJUSTMENT,
            event_subtype=HistoryEventSubType.SPEND,
            asset=movement_event.asset,
            amount=FVal('0.1234'),
        ), EvmEvent(
            tx_ref=make_evm_tx_hash(),
            sequence_index=1,
            timestamp=movement_event.timestamp,
            location=Location.OPTIMISM,
            event_type=(
                HistoryEventType.SPEND if movement_type == HistoryEventType.DEPOSIT
                else HistoryEventType.RECEIVE
            ),
            event_subtype=HistoryEventSubType.NONE,
            asset=asset,
            amount=FVal(match_amount),
        )])

    with database.conn.write_ctx() as write_cursor:
        events_db.add_history_events(
            write_cursor=write_cursor,
            history=events_to_add,
        )

    # Run matching and check that the adjustment events were created with proper subtypes
    match_asset_movements(database=database)
    with database.conn.read_ctx() as cursor:
        for asset, expected_adjustment_subtype in (
            (A_ETH, HistoryEventSubType.RECEIVE),
            (A_BTC, HistoryEventSubType.SPEND),
            (A_USDC, HistoryEventSubType.SPEND),
            (A_WSOL, HistoryEventSubType.RECEIVE),
        ):
            assert len(events := events_db.get_history_events_internal(
                cursor=cursor,
                filter_query=HistoryEventFilterQuery.make(
                    assets=(asset,),
                    event_types=[HistoryEventType.EXCHANGE_ADJUSTMENT],
                ),
            )) == 1
            assert events[0].event_subtype == expected_adjustment_subtype
            assert events[0].amount == FVal('0.01')


def test_match_by_balance_tracking_event_direction(database: 'DBHandler') -> None:
    """Test that when there are multiple close matches due to the accounting direction being
    neutral that we narrow the match based on is balance tracking direction.
    """
    with database.conn.write_ctx() as write_cursor:
        DBHistoryEvents(database).add_history_events(
            write_cursor=write_cursor,
            history=[(movement_event := AssetMovement(
                identifier=(movement_id := 1),
                location=Location.KRAKEN,
                event_type=HistoryEventType.WITHDRAWAL,
                timestamp=TimestampMS(1520000000000),
                asset=A_USDC,
                amount=FVal('25'),
                unique_id='xyz',
                location_label='Kraken 1',
            )), EvmEvent(  # Ignored event. Balance tracking direction is OUT (wrong direction).
                identifier=2,
                tx_ref=make_evm_tx_hash(),
                sequence_index=0,
                timestamp=movement_event.timestamp,
                location=Location.ETHEREUM,
                event_type=HistoryEventType.WITHDRAWAL,
                event_subtype=HistoryEventSubType.REMOVE_ASSET,
                asset=movement_event.asset,
                amount=movement_event.amount,
            ), EvmEvent(  # Matched event. Balance tracking direction is IN (expected direction).
                identifier=(match_id := 3),
                tx_ref=make_evm_tx_hash(),
                sequence_index=0,
                timestamp=movement_event.timestamp,
                location=Location.ETHEREUM,
                event_type=HistoryEventType.DEPOSIT,
                event_subtype=HistoryEventSubType.DEPOSIT_ASSET,
                asset=movement_event.asset,
                amount=movement_event.amount,
            )],
        )

    _match_and_check(database=database, expected_matches=[(movement_id, match_id)])
