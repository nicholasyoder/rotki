import random
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
import requests

from rotkehlchen.chain.ethereum.modules.makerdao.sai.constants import CPT_SAI
from rotkehlchen.chain.evm.constants import ZERO_ADDRESS
from rotkehlchen.chain.evm.types import string_to_evm_address
from rotkehlchen.constants import DAY_IN_SECONDS
from rotkehlchen.constants.assets import A_ETH
from rotkehlchen.constants.misc import ZERO
from rotkehlchen.db.evmtx import DBEvmTx
from rotkehlchen.db.filtering import EvmTransactionsFilterQuery, HistoryEventFilterQuery
from rotkehlchen.db.history_events import (
    EVM_EVENT_FIELDS,
    EVM_EVENT_JOIN,
    HISTORY_BASE_ENTRY_FIELDS, DBHistoryEvents,
)
from rotkehlchen.fval import FVal
from rotkehlchen.history.events.structures.evm_event import EvmEvent
from rotkehlchen.history.events.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.tests.utils.api import (
    api_url_for,
    assert_error_async_response,
    assert_error_response,
    assert_ok_async_response,
    assert_proper_response_with_result,
    assert_proper_sync_response_with_result,
    wait_for_async_task,
)
from rotkehlchen.tests.utils.factories import make_evm_address
from rotkehlchen.types import (
    ChainID,
    ChecksumEvmAddress,
    Location,
    Timestamp,
    TimestampMS,
    deserialize_evm_tx_hash,
)
from rotkehlchen.utils.hexbytes import hexstring_to_bytes
from rotkehlchen.utils.misc import ts_now

if TYPE_CHECKING:
    from rotkehlchen.api.server import APIServer
    from rotkehlchen.db.drivers.gevent import DBCursor


@pytest.mark.parametrize('ethereum_accounts', [[]])
@pytest.mark.parametrize('btc_accounts', [['bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3']])
def test_query_transactions(rotkehlchen_api_server: 'APIServer') -> None:
    """Test that bitcoin transactions are properly queried via the api."""
    async_query = random.choice([False, True])
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'blockchaintransactionsresource',
        ), json={'async_query': async_query},
    )
    if async_query:
        task_id = assert_ok_async_response(response)
        outcome = wait_for_async_task(rotkehlchen_api_server, task_id)
        assert outcome['message'] == ''
        result = outcome['result']
    else:
        result = assert_proper_sync_response_with_result(response)

    assert result is True

    with rotki.data.db.conn.read_ctx() as cursor:
        events = DBHistoryEvents(rotki.data.db).get_history_events(
            cursor=cursor,
            filter_query=HistoryEventFilterQuery.make(),
            has_premium=True,
        )

    print(len(events))

    last_id = events[0].event_identifier
    for event in events:
        if event.event_identifier != last_id:
            print('------')
        print([event])
        last_id = event.event_identifier