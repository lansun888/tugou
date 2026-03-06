"""
Lightweight Multicall3 helper for AsyncWeb3 v7.

Batches multiple on-chain view calls into a single eth_call, reducing
network round-trips from N to 1.

Does NOT use contract.encodeABI (not available on AsyncContract in web3.py v7).
Instead, uses eth_utils.keccak + eth_abi.encode to build calldata directly.

Usage:
    from bsc_bot.utils.multicall_helper import multicall3_batch

    results = await multicall3_batch(w3, [
        (token_addr, "name()",        [],             [],          ["string"]),
        (token_addr, "symbol()",      [],             [],          ["string"]),
        (token_addr, "decimals()",    [],             [],          ["uint8"]),
        (token_addr, "totalSupply()", [],             [],          ["uint256"]),
        (token_addr, "balanceOf(address)", [wallet], ["address"], ["uint256"]),
    ])
    name, symbol, decimals, total_supply, balance = results

Each call tuple:
    (target_address, fn_signature, fn_args, arg_types, return_types)

    target_address : str  — checksum or lowercase address
    fn_signature   : str  — e.g. "name()" or "balanceOf(address)"
    fn_args        : list — positional args matching arg_types
    arg_types      : list — ABI type strings for encoding args, e.g. ["address"]
    return_types   : list — ABI type strings for decoding result, e.g. ["uint256"]

Returns a list where each element is the decoded value (unwrapped when single),
or None when that individual call failed.
"""

from typing import Any, List, Optional, Tuple

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "target",       "type": "address"},
                    {"internalType": "bool",    "name": "allowFailure", "type": "bool"},
                    {"internalType": "bytes",   "name": "callData",     "type": "bytes"},
                ],
                "internalType": "struct Multicall3.Call3[]",
                "name": "calls",
                "type": "tuple[]",
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"internalType": "bool",  "name": "success",    "type": "bool"},
                    {"internalType": "bytes", "name": "returnData", "type": "bytes"},
                ],
                "internalType": "struct Multicall3.Result[]",
                "name": "returnData",
                "type": "tuple[]",
            }
        ],
        "stateMutability": "payable",
        "type": "function",
    }
]


def _build_calldata(fn_signature: str, fn_args: list, arg_types: list) -> bytes:
    """Build raw calldata: 4-byte selector + ABI-encoded args."""
    selector = keccak(text=fn_signature)[:4]
    if arg_types:
        return selector + abi_encode(arg_types, fn_args)
    return selector


async def multicall3_batch(
    w3,
    calls: List[Tuple],  # (target_addr, fn_sig, fn_args, arg_types, return_types)
) -> List[Optional[Any]]:
    """
    Execute multiple view calls in a single RPC request via Multicall3.

    Returns a list of decoded values (or None on per-call failure).
    Single-return functions are unwrapped from the tuple automatically.
    """
    mc = w3.eth.contract(
        address=w3.to_checksum_address(MULTICALL3_ADDRESS),
        abi=MULTICALL3_ABI,
    )

    encoded_calls = []
    decoders: List[List[str]] = []

    for target, fn_sig, fn_args, arg_types, return_types in calls:
        calldata = _build_calldata(fn_sig, fn_args, arg_types)
        encoded_calls.append((w3.to_checksum_address(target), True, calldata))
        decoders.append(return_types)

    results = await mc.functions.aggregate3(encoded_calls).call()

    outputs: List[Optional[Any]] = []
    for (success, return_data), types in zip(results, decoders):
        if success and return_data:
            try:
                decoded = abi_decode(types, bytes(return_data))
                outputs.append(decoded[0] if len(decoded) == 1 else decoded)
            except Exception:
                outputs.append(None)
        else:
            outputs.append(None)

    return outputs
