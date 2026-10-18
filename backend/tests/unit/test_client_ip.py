import ipaddress

import pytest

from app.security.client_ip import resolve_client_ip

PROXIES = (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("fd00::/8"))


def test_the_peer_is_used_when_no_proxy_is_trusted() -> None:
    assert resolve_client_ip("203.0.113.9", "198.51.100.1", ()) == "203.0.113.9"


def test_a_forged_header_from_an_untrusted_peer_is_ignored() -> None:
    # the API is reachable directly: anyone can send any X-Forwarded-For
    assert resolve_client_ip("203.0.113.9", "198.51.100.1", PROXIES) == "203.0.113.9"


def test_a_trusted_proxy_names_the_client() -> None:
    assert resolve_client_ip("10.0.0.2", "198.51.100.1", PROXIES) == "198.51.100.1"


def test_values_the_client_prepended_cannot_override_what_the_proxy_saw() -> None:
    chain = "6.6.6.6, 198.51.100.1"  # the client claimed to be 6.6.6.6; the proxy saw 198.51.100.1
    assert resolve_client_ip("10.0.0.2", chain, PROXIES) == "198.51.100.1"


def test_several_proxies_are_skipped_from_the_right() -> None:
    chain = "198.51.100.1, 10.1.1.1, 10.2.2.2"
    assert resolve_client_ip("10.0.0.2", chain, PROXIES) == "198.51.100.1"


def test_a_chain_made_only_of_proxies_falls_back_to_the_peer() -> None:
    assert resolve_client_ip("10.0.0.2", "10.1.1.1, 10.2.2.2", PROXIES) == "10.0.0.2"


@pytest.mark.parametrize("chain", ["garbage", "198.51.100.1, , 10.1.1.1", "unknown, 10.1.1.1", ""])
def test_a_malformed_chain_is_not_guessed_about(chain: str) -> None:
    assert resolve_client_ip("10.0.0.2", chain, PROXIES) == "10.0.0.2"


def test_ipv6_proxies_and_clients_work() -> None:
    assert resolve_client_ip("fd00::2", "2001:db8::7", PROXIES) == "2001:db8::7"


def test_a_missing_peer_is_reported_as_unknown() -> None:
    assert resolve_client_ip(None, "198.51.100.1", PROXIES) == "unknown"
