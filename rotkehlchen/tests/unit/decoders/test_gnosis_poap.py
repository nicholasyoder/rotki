import pytest

from rotkehlchen.accounting.structures.balance import Balance
from rotkehlchen.chain.evm.constants import ZERO_ADDRESS
from rotkehlchen.chain.evm.decoding.constants import CPT_GAS, CPT_SDAI
from rotkehlchen.chain.gnosis.modules.sdai.constants import GNOSIS_SDAI_ADDRESS
from rotkehlchen.constants.assets import A_WXDAI, A_XDAI
from rotkehlchen.constants.resolver import evm_address_to_identifier
from rotkehlchen.fval import FVal
from rotkehlchen.history.events.structures.evm_event import EvmEvent
from rotkehlchen.history.events.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.tests.utils.ethereum import get_decoded_events_of_transaction
from rotkehlchen.types import ChainID, EvmTokenKind, Location, TimestampMS, deserialize_evm_tx_hash


#@pytest.mark.vcr
@pytest.mark.parametrize('gnosis_accounts', [['0x9531C059098e3d194fF87FebB587aB07B30B1306']])
def test_gnosis_approve_poap(gnosis_inquirer, gnosis_accounts):
    user_address = gnosis_accounts[0]
    tx_hash = deserialize_evm_tx_hash('0xf45a2d40ba2160863d81e05f978e70144f543080d05df4fb12b98888b112647c')  # noqa: E501
    events, _ = get_decoded_events_of_transaction(
        evm_inquirer=gnosis_inquirer,
        tx_hash=tx_hash,
    )
    for event in events:
        print([event])
    timestamp = TimestampMS(1707169525000)
    gas_amount, deposit_amount, receive_amount = '0.000367251244452481', '315', '303.052244055946806232'  # noqa: E501
    expected_events = [
        EvmEvent(
            sequence_index=0,
            timestamp=timestamp,
            location=Location.GNOSIS,
            event_type=HistoryEventType.SPEND,
            event_subtype=HistoryEventSubType.FEE,
            asset=A_XDAI,
            balance=Balance(amount=FVal(gas_amount)),
            location_label=user_address,
            notes=f'Burned {gas_amount} XDAI for gas',
            tx_hash=tx_hash,
            counterparty=CPT_GAS,
        ), EvmEvent(
            sequence_index=1,
            timestamp=timestamp,
            location=Location.GNOSIS,
            event_type=HistoryEventType.DEPOSIT,
            event_subtype=HistoryEventSubType.DEPOSIT_ASSET,
            asset=A_XDAI,
            balance=Balance(amount=FVal(deposit_amount)),
            location_label=user_address,
            notes=f'Deposit {deposit_amount} XDAI into the Savings xDAI contract',
            tx_hash=tx_hash,
            counterparty=CPT_SDAI,
            address='0xD499b51fcFc66bd31248ef4b28d656d67E591A94',
        ),
    ]

    assert events == expected_events
