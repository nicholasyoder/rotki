import pytest

from rotkehlchen.chain.bitcoin.transactions import query_bitcoin_account_transactions, query_bitcoin_transactions
from rotkehlchen.db.dbhandler import DBHandler
from rotkehlchen.db.filtering import HistoryEventFilterQuery
from rotkehlchen.db.history_events import DBHistoryEvents
from rotkehlchen.types import BTCAddress, Timestamp
from rotkehlchen.utils.misc import ts_now


#@pytest.mark.parametrize('btc_accounts', [['bc1qvlvr4r8ttqk7t2agsa2ymggsadjtxcn6llxfa0']])
#@pytest.mark.parametrize('btc_accounts', [['bc1qwqdg6squsna38e46795at95yu9atm8azzmyvckulcc7kytlcckxswvvzej']])
@pytest.mark.parametrize('btc_accounts', [['bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0']])
def test_query_txs(database: 'DBHandler', btc_accounts: list[BTCAddress]):

    query_bitcoin_transactions(
        database=database,
        from_timestamp=Timestamp(0),
        to_timestamp=ts_now(),
        addresses=btc_accounts,
    )

    with database.conn.read_ctx() as cursor:
        events = DBHistoryEvents(database).get_history_events(
            cursor=cursor,
            filter_query=HistoryEventFilterQuery.make(
            ),
            has_premium=True,
        )

    last_id = events[0].event_identifier
    for event in events:
        if event.event_identifier != last_id:
            print('------')
        print([event])
        last_id = event.event_identifier