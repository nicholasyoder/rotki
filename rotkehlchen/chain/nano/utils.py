import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from Crypto.Protocol.HPKE import DeserializeError
from nanohakase import RPC, raw_to_whole

from rotkehlchen.constants.assets import A_NANO
from rotkehlchen.fval import FVal
from rotkehlchen.history.events.structures.base import HistoryEvent
from rotkehlchen.history.events.structures.types import HistoryEventType, HistoryEventSubType
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.serialization.deserialize import deserialize_timestamp
from rotkehlchen.types import SupportedBlockchain, Location
from rotkehlchen.utils.misc import ts_sec_to_ms

if TYPE_CHECKING:
    from rotkehlchen.types import NanoAddress

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


# TODO: add proper handling of RPCs


def get_nano_addresses_balances(
        accounts: Sequence['NanoAddress'],
) -> dict['NanoAddress', 'FVal']:
    log.debug(f'N_DEBUG: get_nano_addresses_balances: {accounts}')

    rpc = RPC('https://app.natrium.io/api')

    balances = {}
    for account in accounts:
        balance_data = rpc.get_account_balance(account)
        print(balance_data)
        balances[account] = FVal(raw_to_whole(int(balance_data['balance'])))

    log.debug(f'N_DEBUG: balances: {balances}')
    return balances


def deserialize_nano_event(account: 'NanoAddress', entry: dict[str, Any]) -> HistoryEvent | None:
    """
    """
    tx_hash = entry['hash']
    amount = FVal(raw_to_whole(int(entry['amount'])))
    other_account = entry['account']
    if (entry_type := entry['type']) == 'send':
        event_type = HistoryEventType.SPEND
        notes = f'Send {amount} XNO to {other_account}'
    elif entry_type:
        event_type = HistoryEventType.RECEIVE
        notes = f'Receive {amount} XNO from {other_account}'
    else:
        log.error(f'Encountered unknown event type: {entry_type} in Nano transaction: {tx_hash}')
        return None

    return HistoryEvent(
        event_identifier=f"{SupportedBlockchain.NANO.value}_{tx_hash}",
        sequence_index=0,
        timestamp=ts_sec_to_ms(deserialize_timestamp(entry['local_timestamp'])),
        location=Location.NANO,
        event_type=event_type,
        event_subtype=HistoryEventSubType.NONE,
        asset=A_NANO,
        amount=amount,
        location_label=account,
        notes=notes,
        extra_data={'hash': tx_hash}
    )

def get_nano_wallet_history(
        account: 'NanoAddress',
) -> list[HistoryEvent]:

    rpc = RPC('https://app.natrium.io/api')
    page_size = 100

    history_events, last_head = [], None
    while True:
        payload = {'action': 'account_history', 'account': account, 'reverse': True}
        payload.update(
            {'head': last_head, 'count': page_size + 1}
            if last_head is not None else
            {'count': page_size}
        )

        result: dict[str, list[dict[str, Any]]] = rpc.call(payload=payload)
        if (history := result.get('history', [])) is None:
            log.error('Missing history in response from Nano rpc.')
            break

        if last_head is not None:
            history = history[1:]  # Skip first entry since we already got it in last query.

        for entry in history:
            print(entry)
            try:
                if (event := deserialize_nano_event(account=account, entry=entry)) is not None:
                    history_events.append(event)
            except (DeserializeError, KeyError) as e:
                msg = f'missing key {e!s}' if isinstance(e, KeyError) else str(e)
                log.error(f'Failed to deserialize Nano transaction: {entry} due to {msg}')

        if len(history) < page_size:
            break
        else:
            last_head = history[-1].get('hash')

    return history_events