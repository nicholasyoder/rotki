import requests

from rotkehlchen.api.server import APIServer
from rotkehlchen.chain.nano.utils import get_nano_addresses_balances, get_nano_wallet_history
from rotkehlchen.tests.utils.api import api_url_for, assert_proper_sync_response_with_result, assert_proper_response
from rotkehlchen.types import NanoAddress, ChainID, SupportedBlockchain


def test_get_nano_addresses_balances():
    print(get_nano_addresses_balances(
        accounts=[NanoAddress('nano_35dtgo74wo3tnfa8co9ruq57bww9knfq4kti8x5spdtxt45zcjaayrxqe93y')]
    ))



def test_nano_get_balances(rotkehlchen_api_server: 'APIServer') -> None:
    """Test that all chains in CHAIN_TO_BALANCE_PROTOCOLS get their protocol balances queried.
    Regression test for https://github.com/rotki/rotki/pull/9173
    """
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen

    response = requests.put(
        url=api_url_for(
            rotkehlchen_api_server,
            'blockchainsaccountsresource',
            blockchain='NANO',
        ),
        json={'accounts': [
            {'address': 'nano_35dtgo74wo3tnfa8co9ruq57bww9knfq4kti8x5spdtxt45zcjaayrxqe93y'}
        ]}
    )
    assert_proper_response(response)

    result = rotki.chains_aggregator.query_balances()

    print('=======')
    print(result)


def test_get_wallet_history():
    events = get_nano_wallet_history(
        account=NanoAddress('nano_35dtgo74wo3tnfa8co9ruq57bww9knfq4kti8x5spdtxt45zcjaayrxqe93y'),
    )

    for event in events:
        print('-----')
        print([event])

