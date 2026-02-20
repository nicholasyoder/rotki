import logging
from typing import TYPE_CHECKING, Any

from rotkehlchen.assets.utils import asset_normalized_value
from rotkehlchen.chain.decoding.types import CounterpartyDetails
from rotkehlchen.chain.decoding.utils import maybe_reshuffle_events
from rotkehlchen.chain.evm.decoding.interfaces import EvmDecoderInterface
from rotkehlchen.chain.evm.decoding.structures import (
    DEFAULT_EVM_DECODING_OUTPUT,
    DecoderContext,
    EvmDecodingOutput,
)
from rotkehlchen.chain.evm.decoding.woo_fi.constants import (
    CPT_WOO_FI,
    CPT_WOO_FI_LABEL,
    WOO_ROUTER_SWAP_TOPIC,
    WOO_ROUTER_V2,
)
from rotkehlchen.history.events.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.utils.misc import bytes_to_address

if TYPE_CHECKING:
    from rotkehlchen.types import ChecksumEvmAddress

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


class WooFiCommonDecoder(EvmDecoderInterface):

    def _decode_swap(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode swaps made via the WooFi router."""
        if context.tx_log.topics[0] != WOO_ROUTER_SWAP_TOPIC or not self.base.any_tracked([
            (from_address := bytes_to_address(context.tx_log.data[96:128])),
            (to_address := bytes_to_address(context.tx_log.topics[3])),
        ]):
            return DEFAULT_EVM_DECODING_OUTPUT

        from_amount = asset_normalized_value(
            amount=int.from_bytes(context.tx_log.data[32:64]),
            asset=(from_asset := self.base.get_or_create_evm_asset(
                address=bytes_to_address(context.tx_log.topics[1]),
            )),
        )
        to_amount = asset_normalized_value(
            amount=int.from_bytes(context.tx_log.data[64:96]),
            asset=(to_asset := self.base.get_or_create_evm_asset(
                address=bytes_to_address(context.tx_log.topics[2]),
            )),
        )
        out_event = in_event = None
        for event in context.decoded_events:
            if (((
                    event.event_type == HistoryEventType.SPEND and
                    event.event_subtype == HistoryEventSubType.NONE
                ) or (
                    event.event_type == HistoryEventType.TRADE and
                    event.event_subtype == HistoryEventSubType.SPEND
                )) and
                event.asset == from_asset and
                event.amount == from_amount and
                event.location_label == from_address
            ):
                event.event_type = HistoryEventType.TRADE
                event.event_subtype = HistoryEventSubType.SPEND
                event.counterparty = CPT_WOO_FI
                event.notes = f'Swap {from_amount} {from_asset.symbol} in {CPT_WOO_FI_LABEL}'
                out_event = event
            elif (((
                    event.event_type == HistoryEventType.RECEIVE and
                    event.event_subtype == HistoryEventSubType.NONE
                ) or (
                    event.event_type == HistoryEventType.TRADE and
                    event.event_subtype == HistoryEventSubType.RECEIVE
                )) and
                event.asset == to_asset and
                event.amount == to_amount and
                event.location_label == to_address
            ):
                event.event_type = HistoryEventType.TRADE
                event.event_subtype = HistoryEventSubType.RECEIVE
                event.counterparty = CPT_WOO_FI
                event.notes = f'Receive {to_amount} {to_asset.symbol} after {CPT_WOO_FI_LABEL} swap'  # noqa: E501
                in_event = event

        if out_event is None and in_event is None:
            log.error(f'Failed to find both sides of WooFi swap in {context.transaction}')
            return DEFAULT_EVM_DECODING_OUTPUT

        maybe_reshuffle_events(
            events_list=context.decoded_events,
            ordered_events=[out_event, in_event],
        )
        return EvmDecodingOutput(process_swaps=True)

    # -- DecoderInterface methods

    def addresses_to_decoders(self) -> dict['ChecksumEvmAddress', tuple[Any, ...]]:
        return {WOO_ROUTER_V2: (self._decode_swap,)}

    @staticmethod
    def counterparties() -> tuple[CounterpartyDetails, ...]:
        return (CounterpartyDetails(
            identifier=CPT_WOO_FI,
            label=CPT_WOO_FI_LABEL,
            image='woo_fi.svg',
        ),)
