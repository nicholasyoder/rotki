from typing import Final

from rotkehlchen.chain.evm.types import string_to_evm_address
from rotkehlchen.types import ChainID

CPT_WOO_FI: Final = 'woo-fi'
CPT_WOO_FI_LABEL: Final = 'Woo.fi'

WOO_FI_SUPPORTED_CHAINS: Final = {
    ChainID.ARBITRUM_ONE,
    ChainID.BASE,
    ChainID.BINANCE_SC,
    ChainID.ETHEREUM,
    ChainID.OPTIMISM,
    ChainID.POLYGON_POS,
}

WOO_ROUTER_V2: Final = string_to_evm_address('0x4c4AF8DBc524681930a27b2F1Af5bcC8062E6fB7')
WOO_ROUTER_SWAP_TOPIC: Final = b"'\xc9\x8e\x91\x1e\xfd\xd2$\xf4\x00/l\xd81\xc3\xad\r'Y\xee\x17o\x9e\xe8Fm\x95\x82j\xf2*\x1c"  # noqa: E501
