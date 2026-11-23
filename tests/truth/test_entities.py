import polars as pl

from herdbench._http import HttpClient
from herdbench.sources.hyperliquid import InfoApi
from herdbench.truth import entities
from tests.utils.server import Routes, serve

M = "0x" + "a1" * 20
SUB1 = "0x" + "b2" * 20
SUB2 = "0x" + "c3" * 20
SOLO = "0x00000000000000000000000000000000000000aa"


def handler(body):
    if body["type"] == "userRole":
        if body["user"] == SUB1:
            return 200, {"role": "subAccount", "data": {"master": M}}
        return 200, {"role": "user"}
    if body["type"] == "subAccounts":
        if body["user"] == M:
            return 200, [
                {"name": "sub01", "subAccountUser": SUB1, "master": M, "clearinghouseState": {}, "spotState": {}},
                {"name": "sub02", "subAccountUser": SUB2, "master": M, "clearinghouseState": {}, "spotState": {}},
            ]
        return 200, None
    raise AssertionError(body)


def api(base):
    return InfoApi(url=base + "/info", http=HttpClient(retries=2, backoff=0.0))


def test_user_role_returns_the_master_of_a_sub_account():
    with serve(Routes(post={"/info": handler})) as base:
        a = api(base)
        assert a.user_role(SUB1) == ("subAccount", M)
        assert a.user_role(SOLO) == ("user", None)
        assert a.sub_accounts(M) == [SUB1, SUB2]
        assert a.sub_accounts(SOLO) == []


def test_entity_map_folds_sub_accounts_and_their_siblings_into_the_master():
    roles = {SUB1: ("subAccount", M), SOLO: ("user", None)}
    subs = {M: [SUB1, SUB2]}
    emap = entities.build_map(roles, subs)
    assert emap == {SUB1: M, SUB2: M, M: M}


def test_attach_uses_the_address_itself_when_unmapped():
    pos = pl.DataFrame({"user": [SUB1, SUB2, SOLO], "market": ["BTC"] * 3, "size": [1.0, -1.0, 2.0]})
    out = entities.attach(pos, {SUB1: M, SUB2: M, M: M})
    assert out["entity"].to_list() == [M, M, SOLO]


def test_fetch_roles_resumes_without_querying_finished_addresses(tmp_path):
    store = tmp_path / "roles.jsonl"
    routes = Routes(post={"/info": handler})
    with serve(routes) as base:
        entities.fetch_roles([SUB1, SOLO], api(base), store)
        first = sum(1 for m, *_ in routes.log if m == "POST")
        roles, subs = entities.fetch_roles([SUB1, SOLO, SUB2], api(base), store)
        second = sum(1 for m, *_ in routes.log if m == "POST") - first
    assert roles[SUB1] == ("subAccount", M)
    assert subs[M] == [SUB1, SUB2]
    assert second <= 2  # only SUB2's role (and no repeat of SUB1, SOLO or the master's list)


def test_map_hash_is_stable_and_order_independent():
    a = entities.map_hash({SUB1: M, SUB2: M})
    b = entities.map_hash({SUB2: M, SUB1: M})
    assert a == b
    assert len(a) == 64
    assert entities.map_hash({SUB1: M}) != a
