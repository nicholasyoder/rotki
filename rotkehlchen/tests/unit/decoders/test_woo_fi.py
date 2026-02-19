from typing import TYPE_CHECKING

import pytest

from rotkehlchen.assets.asset import Asset
from rotkehlchen.chain.decoding.constants import CPT_GAS
from rotkehlchen.chain.evm.decoding.woo_fi.constants import CPT_WOO_FI
from rotkehlchen.chain.evm.types import string_to_evm_address
from rotkehlchen.constants.assets import A_ETH
from rotkehlchen.history.events.structures.evm_event import EvmEvent
from rotkehlchen.history.events.structures.evm_swap import EvmSwapEvent
from rotkehlchen.history.events.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.tests.utils.ethereum import get_decoded_events_of_transaction
from rotkehlchen.types import (
    FVal,
    Location,
    TimestampMS,
    deserialize_evm_tx_hash,
)

if TYPE_CHECKING:
    from rotkehlchen.chain.arbitrum_one.node_inquirer import ArbitrumOneInquirer
    from rotkehlchen.chain.optimism.node_inquirer import OptimismInquirer
    from rotkehlchen.types import ChecksumEvmAddress


@pytest.mark.vcr(filter_query_parameters=['apikey'])
@pytest.mark.parametrize('arbitrum_one_accounts', [['0x706A70067BE19BdadBea3600Db0626859Ff25D74']])
def test_swap_token_to_token(
        arbitrum_one_inquirer: 'ArbitrumOneInquirer',
        arbitrum_one_accounts: list['ChecksumEvmAddress'],
) -> None:
    events, _ = get_decoded_events_of_transaction(
        evm_inquirer=arbitrum_one_inquirer,
        tx_hash=(tx_hash := deserialize_evm_tx_hash('0xf68aaf2b718ce8cc1b16a3961885cf61a1538bcacf593127e64501e2af42242d')),  # noqa: E501,
    )
    assert events == [EvmEvent(
        tx_ref=tx_hash,
        sequence_index=0,
        timestamp=(timestamp := TimestampMS(1771520287000)),
        location=Location.ARBITRUM_ONE,
        event_type=HistoryEventType.SPEND,
        event_subtype=HistoryEventSubType.FEE,
        asset=A_ETH,
        amount=FVal(gas_amount := '0.00000466714548'),
        location_label=(user_address := arbitrum_one_accounts[0]),
        notes=f'Burn {gas_amount} ETH for gas',
        counterparty=CPT_GAS,
    ), EvmSwapEvent(
        tx_ref=tx_hash,
        sequence_index=1,
        timestamp=timestamp,
        location=Location.ARBITRUM_ONE,
        event_subtype=HistoryEventSubType.SPEND,
        asset=Asset('eip155:42161/erc20:0xaf88d065e77c8cC2239327C5EDb3A432268e5831'),
        amount=FVal(spend_amount := '10'),
        location_label=user_address,
        notes=f'Swap {spend_amount} USDC in Woo.fi',
        counterparty=CPT_WOO_FI,
        address=string_to_evm_address('0x5520385bFcf07Ec87C4c53A7d8d65595Dff69FA4'),
    ), EvmSwapEvent(
        tx_ref=tx_hash,
        sequence_index=2,
        timestamp=timestamp,
        location=Location.ARBITRUM_ONE,
        event_subtype=HistoryEventSubType.RECEIVE,
        asset=Asset('eip155:42161/erc20:0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f'),
        amount=FVal(receive_amount := '0.00015134'),
        location_label=user_address,
        notes=f'Receive {receive_amount} WBTC after Woo.fi swap',
        counterparty=CPT_WOO_FI,
        address=string_to_evm_address('0x5520385bFcf07Ec87C4c53A7d8d65595Dff69FA4'),
    )]


@pytest.mark.vcr(filter_query_parameters=['apikey'])
@pytest.mark.parametrize('optimism_accounts', [['0x7a27075aCcBbC212b703fafbdC82146214Ba0469']])
def test_swap_token_to_native(
        optimism_inquirer: 'OptimismInquirer',
        optimism_accounts: list['ChecksumEvmAddress'],
) -> None:
    events, _ = get_decoded_events_of_transaction(
        evm_inquirer=optimism_inquirer,
        tx_hash=(tx_hash := deserialize_evm_tx_hash('0x39db7fc22c237e649949443d596e065259d527bd1c093413cfcc14c2b9faf4a9')),  # noqa: E501,
    )
    assert events == [EvmEvent(
        tx_ref=tx_hash,
        sequence_index=0,
        timestamp=(timestamp := TimestampMS(1759058093000)),
        location=Location.OPTIMISM,
        event_type=HistoryEventType.SPEND,
        event_subtype=HistoryEventSubType.FEE,
        asset=A_ETH,
        amount=FVal(gas_amount := '0.000000018437422976'),
        location_label=(user_address := optimism_accounts[0]),
        notes=f'Burn {gas_amount} ETH for gas',
        counterparty=CPT_GAS,
    ), EvmEvent(
        tx_ref=tx_hash,
        sequence_index=2,
        timestamp=timestamp,
        location=Location.OPTIMISM,
        event_type=HistoryEventType.INFORMATIONAL,
        event_subtype=HistoryEventSubType.APPROVE,
        asset=Asset('eip155:10/erc20:0x9Bcef72be871e61ED4fBbc7630889beE758eb81D'),
        amount=FVal('0'),
        location_label=user_address,
        notes=f'Revoke rETH spending approval of {user_address} by 0x4c4AF8DBc524681930a27b2F1Af5bcC8062E6fB7',  # noqa: E501
        address=string_to_evm_address('0x4c4AF8DBc524681930a27b2F1Af5bcC8062E6fB7'),
    ), EvmSwapEvent(
        tx_ref=tx_hash,
        sequence_index=3,
        timestamp=timestamp,
        location=Location.OPTIMISM,
        event_subtype=HistoryEventSubType.SPEND,
        asset=Asset('eip155:10/erc20:0x9Bcef72be871e61ED4fBbc7630889beE758eb81D'),
        amount=FVal(spend_amount := '0.1'),
        location_label=user_address,
        notes=f'Swap {spend_amount} rETH in Woo.fi',
        counterparty=CPT_WOO_FI,
        address=string_to_evm_address('0x4c4AF8DBc524681930a27b2F1Af5bcC8062E6fB7'),
    ), EvmSwapEvent(
        tx_ref=tx_hash,
        sequence_index=4,
        timestamp=timestamp,
        location=Location.OPTIMISM,
        event_subtype=HistoryEventSubType.RECEIVE,
        asset=A_ETH,
        amount=FVal(receive_amount := '0.114269398031850807'),
        location_label=user_address,
        notes=f'Receive {receive_amount} ETH after Woo.fi swap',
        counterparty=CPT_WOO_FI,
        address=string_to_evm_address('0x4c4AF8DBc524681930a27b2F1Af5bcC8062E6fB7'),
    )]
