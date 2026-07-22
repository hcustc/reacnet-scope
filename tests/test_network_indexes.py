from __future__ import annotations

from rng_tools.network import Reaction, ReactionNetwork


def test_reverse_lookup_uses_prebuilt_reaction_index() -> None:
    """Reverse pairing remains correct without scanning every reaction."""
    forward = Reaction(("[C]", "[O]"), ("[C][O]",), 12)
    reverse = Reaction(("[C][O]",), ("[O]", "[C]"), 5)
    unrelated = [Reaction((f"[X{i}]",), (f"[Y{i}]",), 1) for i in range(200)]
    network = ReactionNetwork([forward, reverse, *unrelated])

    found = network.find_reverse(network.reactions[0])
    assert found is not None
    assert found.key == reverse.key
    assert network.net_flux(network.reactions[0]) == (12, 5, 7, True)

