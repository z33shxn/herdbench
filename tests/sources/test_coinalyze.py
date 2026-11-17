import pytest

from herdbench._http import HttpClient
from herdbench.sources import coinalyze
from tests.utils.server import Routes, serve

SECRET = "test-key-0000"


def test_missing_key_is_an_explicit_error(monkeypatch):
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)
    with pytest.raises(coinalyze.MissingKey):
        coinalyze.Coinalyze()


def test_history_sends_the_key_as_a_query_parameter_and_parses_rows(monkeypatch):
    monkeypatch.setenv("COINALYZE_API_KEY", SECRET)
    payload = [{"symbol": "BTC.H", "history": [{"t": 1765756800, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5}]}]
    routes = Routes(json_pages={"/v1/open-interest-history": (payload, None)})
    with serve(routes) as base:
        client = coinalyze.Coinalyze(base=base + "/v1/", http=HttpClient(retries=2, backoff=0.0))
        df = client.history("open-interest-history", "BTC.H", "daily", 1765756800, 1765843200)
    assert df.loc[0, "c"] == 1.5
    assert df.loc[0, "symbol"] == "BTC.H"
    sent = [p for method, p, _h in routes.log if method == "GET"][0]
    assert f"api_key={SECRET}" in sent


def test_errors_never_reveal_the_key(monkeypatch):
    monkeypatch.setenv("COINALYZE_API_KEY", SECRET)
    routes = Routes()  # every path returns 404
    with serve(routes) as base:
        client = coinalyze.Coinalyze(base=base + "/v1/", http=HttpClient(retries=1, backoff=0.0))
        with pytest.raises(coinalyze.CoinalyzeError) as err:
            client.history("open-interest-history", "BTC.H", "daily", 1, 2)
    assert SECRET not in str(err.value)
    assert SECRET not in repr(client)
