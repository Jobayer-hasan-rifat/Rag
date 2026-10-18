"""Who is the client? The peer address, unless a trusted reverse proxy vouches for another.

`X-Forwarded-For` is a plain header: anybody can send it. It is believed only when the
connection itself comes from a configured proxy network, and then only the part of the chain that
the proxies added is trusted: the list is read from the right, skipping the proxies' own
addresses, and the first other address is the client. Anything a client put on the left of that
(a forged value) is never reached.
"""

import ipaddress
from collections.abc import Sequence

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
UNKNOWN = "unknown"


def _parse(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _trusted(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address, networks: Sequence[Network]
) -> bool:
    return any(address in network for network in networks if network.version == address.version)


def resolve_client_ip(
    peer: str | None, forwarded_for: str | None, trusted_networks: Sequence[Network]
) -> str:
    """The address to rate-limit and log for a request. Never raises; never trusts a forgery."""
    if not peer:
        return UNKNOWN
    peer_address = _parse(peer)
    if peer_address is None or not trusted_networks or not forwarded_for:
        return peer
    if not _trusted(peer_address, trusted_networks):
        return peer
    for candidate in reversed(forwarded_for.split(",")):
        address = _parse(candidate)
        if address is None:
            return peer  # a malformed chain is not worth guessing about
        if not _trusted(address, trusted_networks):
            return str(address)
    return peer
