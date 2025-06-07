import logging
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

import requests

from rotkehlchen.chain.bitcoin import query_apis_via_callbacks
from rotkehlchen.constants import ZERO
from rotkehlchen.constants.assets import A_BTC
from rotkehlchen.db.cache import DBCacheDynamic
from rotkehlchen.db.dbhandler import DBHandler
from rotkehlchen.db.filtering import HistoryEventFilterQuery
from rotkehlchen.db.history_events import DBHistoryEvents
from rotkehlchen.errors.misc import RemoteError, UnableToDecryptRemoteData
from rotkehlchen.errors.serialization import DeserializationError
from rotkehlchen.fval import FVal
from rotkehlchen.history.events.structures.base import HistoryEvent
from rotkehlchen.history.events.structures.types import HistoryEventType, HistoryEventSubType
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.serialization.deserialize import ensure_type, deserialize_timestamp
from rotkehlchen.types import BTCAddress, Location, Timestamp, TimestampMS, SupportedBlockchain
from rotkehlchen.utils.misc import satoshis_to_btc, ts_sec_to_ms
from rotkehlchen.utils.network import request_get_dict

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)

BLOCKSTREAM_BASE_URL: Final = 'https://blockstream.info/api'
MEMPOOL_SPACE_BASE_URL: Final = 'https://mempool.space/api'


@dataclass
class UTXO:
    address: BTCAddress
    value: FVal


@dataclass
class BitcoinTx:
    tx_id: str
    timestamp: TimestampMS
    fee: FVal
    in_utxos: list[UTXO]
    out_utxos: list[UTXO]


def deserialize_utxo_from_api(raw_utxo_data: dict[str, Any]) -> UTXO:
    try:
        return UTXO(
            address=raw_utxo_data['scriptpubkey_address'],
            value=satoshis_to_btc(raw_utxo_data['value']),
        )
    except KeyError as e:
        raise DeserializationError(f'Failed to deserialize utxo due to missing key {e!s}')


def deserialize_utxo_list(raw_utxo_list: list[dict[str, Any]]) -> list[UTXO]:
    return [deserialize_utxo_from_api(raw_utxo) for raw_utxo in raw_utxo_list]


def _query_blockstream_mempool_txs(base_url: str, url_suffix: str):
    log.debug(
        f'Querying bitcoin transactions.',
        request_url=(url := f'{base_url}/{url_suffix}')
    )
    return requests.get(url).json()


def _query_blockstream_txs(url_suffix: str):
    return _query_blockstream_mempool_txs(
        base_url=BLOCKSTREAM_BASE_URL,
        url_suffix=url_suffix,
    )


def _query_mempool_space_txs(url_suffix: str):
    return _query_blockstream_mempool_txs(
        base_url=MEMPOOL_SPACE_BASE_URL,
        url_suffix=url_suffix,
    )


def _query_tx_list_from_api(
        account: BTCAddress,
        from_timestamp: Timestamp,
        to_timestamp: Timestamp,
        existing_tx_id: str | None,
) -> list[BitcoinTx]:
    """Query raw bitcoin tx list from the block explorer apis and deserialize them.
    The txs are ordered with newest first from the api, with results automatically paginated.
    The next page is requested by specifying the last seen tx id from the previous result.

    If `existing_tx_id` is specified, querying will stop when that id is encountered, only
    returning new transactions since that id.

    Transactions newer than `to_timestamp` will be skipped, but `from_timestamp` is currently
    ignored - since the api returns newest to oldest, if we quit before querying to the end, the
    next query will stop at `existing_tx_id` and the skipped older txs will never be queried.

    Returns a list of deserialized BitcoinTxs.
    """
    tx_list = []
    tx_id = None
    tx_endpoint = f'address/{account}/txs/chain'
    while True:
        for entry in (raw_tx_list := query_apis_via_callbacks(
            api_callbacks={
                'blockstream.info': _query_blockstream_txs,
                'mempool.space': _query_mempool_space_txs,
            },
            url_suffix=f'{tx_endpoint}/{tx_id}' if tx_id is not None else tx_endpoint,  # Specify the last seen tx id to request the next page
        )):
            try:
                if (timestamp := deserialize_timestamp(entry['status']['block_time'])) > to_timestamp:
                    continue  # Haven't reached the requested range yet. Skip tx.

                if (tx_id := entry['txid']) == existing_tx_id:
                    return tx_list  # All new txs have been queried.

                tx_list.append(BitcoinTx(
                    tx_id=tx_id,
                    timestamp=ts_sec_to_ms(timestamp),
                    fee=satoshis_to_btc(entry['fee']),
                    in_utxos=deserialize_utxo_list([vin['prevout'] for vin in entry['vin']]),
                    out_utxos=deserialize_utxo_list(entry['vout']),
                ))
            except (DeserializationError, KeyError) as e:
                msg = f'missing key {e!s}' if isinstance(e, KeyError) else str(e)
                log.error(f'Failed to deserialize bitcoin transaction {entry} due to {msg}')

        if len(raw_tx_list) < 25:
            break

    return tx_list


def _decode_history_events_for_bitcoin_tx(tx: BitcoinTx) -> list[HistoryEvent]:
    """Decode a BitcoinTx into HistoryEvents

    If there are multiple input/output UTXOs for the same address, events will only be created
    with the total in amount and total out amount for that address.

    If there are input UTXOs from multiple addresses a fee event will be created for each address
    with the fee amounts proportional to the amounts spent by each address.

    Returns a list of HistoryEvents
    """
    events, sequence_index = [], -1
    for utxo_list, event_type in (
            (tx.in_utxos, HistoryEventType.SPEND),
            (tx.out_utxos, HistoryEventType.RECEIVE),
    ):
        per_address_totals = defaultdict(lambda: ZERO)
        for utxo in utxo_list:
            per_address_totals[utxo.address] += utxo.value

        event_identifier = f'btc_{tx.tx_id}'
        if event_type == HistoryEventType.SPEND:  # Create fee events
            total_input = sum(per_address_totals.values())
            for address, amount in per_address_totals.items():
                events.append(HistoryEvent(
                    event_identifier=event_identifier,
                    sequence_index=(sequence_index := sequence_index + 1),
                    timestamp=tx.timestamp,
                    location=Location.BITCOIN,
                    event_type=HistoryEventType.SPEND,
                    event_subtype=HistoryEventSubType.FEE,
                    asset=A_BTC,
                    amount=(share := (amount / total_input) * tx.fee),
                    notes=f'Burn {share} BTC for gas',
                    location_label=address,
                ))
                # Subtract fee share from the address's total input amount
                per_address_totals[address] -= share

        for address, total_amount in per_address_totals.items():
            events.append(HistoryEvent(
                event_identifier=event_identifier,
                sequence_index=(sequence_index := sequence_index + 1),
                timestamp=tx.timestamp,
                location=Location.BITCOIN,
                event_type=event_type,
                event_subtype=HistoryEventSubType.NONE,
                asset=A_BTC,
                amount=FVal(total_amount),
                location_label=address,
            ))

    return events


def query_bitcoin_account_transactions(
        database: 'DBHandler',
        from_timestamp: Timestamp,
        to_timestamp: Timestamp,
        account: BTCAddress,
) -> None:
    """Query bitcoin account transactions for the specified time range,
    decode them into HistoryEvents, and save the results to the db.
    """
    log.debug(f'Querying transactions for bitcoin account {account}')
    events_db = DBHistoryEvents(database)
    with database.conn.read_ctx() as cursor:
        last_tx_id = database.get_dynamic_cache(
            cursor=cursor,
            name=DBCacheDynamic.LAST_BITCOIN_TX_ID,
            address=account,
        )

    if len(tx_list := _query_tx_list_from_api(
        account=account,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
        existing_tx_id=last_tx_id,
    )) == 0:
        log.debug(f'No new transactions found for bitcoin account {account}')
        return

    events = []
    for tx in tx_list:
        events.extend(_decode_history_events_for_bitcoin_tx(tx))

    with database.conn.write_ctx() as write_cursor:
        events_db.add_history_events(
            write_cursor=write_cursor,
            history=events,
        )
        database.set_dynamic_cache(
            write_cursor=write_cursor,
            name=DBCacheDynamic.LAST_BITCOIN_TX_ID,
            value=tx_list[0].tx_id,  # safe to use index zero since we checked tx_list length above
            address=account,
        )


def query_bitcoin_transactions(
        database: 'DBHandler',
        from_timestamp: Timestamp,
        to_timestamp: Timestamp,
        addresses: list[BTCAddress],
):
    """Query, decode, and save bitcoin transactions in the specified time range for the specified addresses."""
    for address in addresses:
        query_bitcoin_account_transactions(
            database=database,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
            account=address,
        )
