import pytest

from orc.collections import build_trie, prefix_groups

IDS = [
    "BEDROOM_NIGHTLIGHT",
    "BEDROOM_LAMP",
    "BEDROOM_DISPLAY",
    "ENTRANCE_DESK",
    "ENTRANCE_BULB_1",
    "ENTRANCE_BULB_2",
    "KITCHEN_CABINET",
    "KITCHEN_CABINET_MINI",
    "KITCHEN_OVERHEAD",
    "LIVING_ROOM_DESK",
    "LIVING_ROOM_FLOOR",
    "LIVING_ROOM_MINI",
    "OFFICE_DESK",
    "OFFICE_DISPLAY",
    "OFFICE_FLOOR",
    "OFFICE_TABLE",
]


def groups(ids):
    return {tuple(path): words for path, words in prefix_groups(build_trie(ids))}


def test_shared_prefix_grouped_together():
    result = groups(IDS)
    assert result[("BEDROOM",)] == {"BEDROOM_NIGHTLIGHT", "BEDROOM_LAMP", "BEDROOM_DISPLAY"}
    assert result[("OFFICE",)] == {"OFFICE_DESK", "OFFICE_DISPLAY", "OFFICE_FLOOR", "OFFICE_TABLE"}


def test_deeper_branching_subsumed_into_shortest_prefix():
    result = groups(IDS)
    assert result[("ENTRANCE",)] == {"ENTRANCE_DESK", "ENTRANCE_BULB_1", "ENTRANCE_BULB_2"}


def test_single_child_chain_compressed_into_prefix():
    result = groups(IDS)
    assert result[("LIVING", "ROOM")] == {"LIVING_ROOM_DESK", "LIVING_ROOM_FLOOR", "LIVING_ROOM_MINI"}


def test_word_that_is_also_a_prefix_included():
    result = groups(IDS)
    assert "KITCHEN_CABINET" in result[("KITCHEN",)]
    assert "KITCHEN_CABINET_MINI" in result[("KITCHEN",)]


def test_no_word_appears_in_multiple_groups():
    result = prefix_groups(build_trie(IDS))
    seen = []
    for _, words in result:
        for word in words:
            assert word not in seen
            seen.append(word)


def test_single_word_gets_its_own_group():
    result = groups(["ONLY_WORD"])
    assert result[("ONLY", "WORD")] == {"ONLY_WORD"}
