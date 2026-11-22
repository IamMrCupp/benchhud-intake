"""The contract with the public site's relay, held to the real endpoint.

`tests/fixtures/request-contract.json` is what the site repository copies and
builds against. It is only worth copying if it can't drift from the code, so
this module reads the fixture and checks two things: every example in it gets
exactly the status and body it promises, and every rule it states (fields,
caps, allowed values, codes) is the rule the endpoint enforces.

If one of these fails, the endpoint and the fixture disagree. Fix whichever is
wrong, and if the fixture changes, the site repo has to hear about it.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from intake import auth, web_requests
from intake.app import SUBMIT_PATH, app
from intake.db import connect
from intake.schema import initialise

FIXTURE = Path(__file__).parent / "fixtures" / "request-contract.json"
CONTRACT = json.loads(FIXTURE.read_text("utf-8"))
TOKEN = CONTRACT["test_token"]
EXAMPLES = CONTRACT["examples"]
VALID = next(e for e in EXAMPLES if e["name"] == "accepted-full")["request"]["json"]


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """No dependency overrides: the app finds its database the way a deployment does."""
    path = tmp_path / "intake.db"
    monkeypatch.setenv("INTAKE_DB_PATH", str(path))
    monkeypatch.setenv(auth.SUBMIT_TOKEN_ENV_VAR, TOKEN)
    monkeypatch.delenv(auth.API_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(web_requests.MAX_PENDING_ENV_VAR, raising=False)
    return path


@pytest.fixture
def client(db_path):
    return TestClient(app)


def _count(db_path) -> int:
    with connect(db_path) as conn:
        initialise(conn)  # a 401 never opens the database, so it may not exist yet
        return conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]


def _send(client, request):
    kwargs = {"headers": request["headers"]}
    if "raw" in request:
        kwargs["content"] = request["raw"]
    else:
        kwargs["content"] = json.dumps(request["json"])
    return client.post(CONTRACT["endpoint"]["path"], **kwargs)


def _post(client, body, **headers):
    return client.post(
        SUBMIT_PATH, json=body, headers={"Authorization": f"Bearer {TOKEN}", **headers}
    )


# --- the fixture's examples, replayed ---------------------------------------


@pytest.mark.parametrize("example", EXAMPLES, ids=[e["name"] for e in EXAMPLES])
def test_example_gets_the_documented_response(example, client, db_path, monkeypatch) -> None:
    given = example.get("given", {})
    if "max_pending" in given:
        monkeypatch.setenv(web_requests.MAX_PENDING_ENV_VAR, str(given["max_pending"]))
    if given.get("submit_token_configured") is False:
        monkeypatch.delenv(auth.SUBMIT_TOKEN_ENV_VAR)

    response = _send(client, example["request"])
    expected = example["response"]

    assert response.status_code == expected["status"]
    if expected["status"] == 202:
        body = response.json()
        # Exactly the documented keys: an id and nothing else.
        assert set(body) == set(expected["body"]) == {"id"}
        assert re.fullmatch(CONTRACT["responses"]["202"]["id_pattern"], body["id"])
        if given.get("send_twice"):
            again = _send(client, example["request"])
            assert (again.status_code, again.json()) == (202, body)
        assert _count(db_path) == 1
    else:
        assert response.json() == expected["body"]
        assert _count(db_path) == 0


def test_every_documented_status_has_an_example() -> None:
    # 413 needs a body too big to be worth writing into a fixture; it has its
    # own test below.
    assert {str(e["response"]["status"]) for e in EXAMPLES} | {"413"} == set(CONTRACT["responses"])


@pytest.mark.parametrize("example", EXAMPLES, ids=[e["name"] for e in EXAMPLES])
def test_example_body_matches_its_status_shape(example) -> None:
    """The per-example bodies and the `responses` table are the same shapes."""
    documented = CONTRACT["responses"][str(example["response"]["status"])]["body"]
    body = example["response"]["body"]

    assert set(body) == set(documented)
    if "errors" in body:
        for error in body["errors"]:
            assert set(error) == {"field", "code"}
            assert error["field"] in CONTRACT["error_fields"]
            assert error["code"] in CONTRACT["error_codes"]
    else:
        assert body == documented


# --- the fixture's rules, against the code ----------------------------------


def test_endpoint_is_where_the_fixture_says() -> None:
    assert CONTRACT["endpoint"] == {"method": "POST", "path": SUBMIT_PATH}


def test_fields_match_the_code() -> None:
    fields = CONTRACT["fields"]

    assert tuple(fields) == web_requests.FIELDS
    assert tuple(n for n, f in fields.items() if f["required"]) == web_requests.REQUIRED
    assert {
        n: f["max_length"] for n, f in fields.items() if "max_length" in f
    } == web_requests.MAX_LENGTHS
    assert tuple(fields["handoff"]["values"]) == web_requests.HANDOFFS
    assert CONTRACT["at_least_one_of"] == [["email", "phone"]]
    assert CONTRACT["max_body_bytes"] == web_requests.MAX_BODY_BYTES
    assert (
        CONTRACT["headers"]["Idempotency-Key"]["max_length"]
        == web_requests.MAX_IDEMPOTENCY_KEY_LENGTH
    )
    assert f"default {web_requests.DEFAULT_MAX_PENDING}," in CONTRACT["responses"]["429"]["note"]
    assert web_requests.MAX_PENDING_ENV_VAR in CONTRACT["responses"]["429"]["note"]
    assert auth.SUBMIT_TOKEN_ENV_VAR in CONTRACT["headers"]["Authorization"]["token"]


def test_error_codes_are_exactly_the_ones_the_code_can_emit() -> None:
    assert tuple(CONTRACT["error_codes"]) == web_requests.ERROR_CODES
    with pytest.raises(ValueError):
        web_requests.FieldError("device", "some_new_code")


def test_error_fields_are_the_fields_plus_body_and_the_key_header() -> None:
    assert CONTRACT["error_fields"] == [
        *web_requests.FIELDS,
        "body",
        web_requests.IDEMPOTENCY_HEADER,
    ]


@pytest.mark.parametrize("name", [n for n, f in CONTRACT["fields"].items() if "max_length" in f])
def test_length_cap_is_exactly_the_documented_one(name, client) -> None:
    cap = CONTRACT["fields"][name]["max_length"]

    at_cap = _post(client, {**VALID, name: "x" * cap})
    over = _post(client, {**VALID, name: "x" * (cap + 1)})

    assert at_cap.status_code == 202
    assert over.status_code == 400
    assert over.json()["errors"] == [{"field": name, "code": "too_long"}]


@pytest.mark.parametrize("name", [n for n, f in CONTRACT["fields"].items() if "max_length" in f])
def test_length_is_counted_after_stripping(name, client) -> None:
    cap = CONTRACT["fields"][name]["max_length"]

    assert _post(client, {**VALID, name: "  " + "x" * cap + " \n"}).status_code == 202


@pytest.mark.parametrize("name", [n for n, f in CONTRACT["fields"].items() if f["required"]])
def test_required_field_is_required(name, client) -> None:
    response = _post(client, {k: v for k, v in VALID.items() if k != name})

    assert response.status_code == 400
    assert response.json()["errors"] == [{"field": name, "code": "required"}]


@pytest.mark.parametrize("name", [n for n, f in CONTRACT["fields"].items() if not f["required"]])
def test_optional_field_is_optional(name, client) -> None:
    assert _post(client, {k: v for k, v in VALID.items() if k != name}).status_code == 202


@pytest.mark.parametrize("handoff", CONTRACT["fields"]["handoff"]["values"])
def test_each_handoff_value_is_accepted(handoff, client) -> None:
    assert _post(client, {**VALID, "handoff": handoff}).status_code == 202


@pytest.mark.parametrize("handoff", ["Drop_Off", "MAIL_IN", "drop-off"])
def test_handoff_values_are_exact(handoff, client) -> None:
    response = _post(client, {**VALID, "handoff": handoff})

    assert response.status_code == 400
    assert response.json()["errors"] == [{"field": "handoff", "code": "invalid"}]


def test_a_wrong_contact_field_is_not_also_reported_as_missing(client) -> None:
    response = _post(client, {**VALID, "email": 7, "phone": None})

    assert response.json()["errors"] == [{"field": "email", "code": "invalid"}]


def test_the_key_is_stripped_and_a_blank_one_is_no_key(client, db_path) -> None:
    first = _post(client, VALID, **{"Idempotency-Key": "k1"})
    padded = _post(client, VALID, **{"Idempotency-Key": "  k1  "})
    assert padded.json() == first.json()

    blanks = {_post(client, VALID, **{"Idempotency-Key": "   "}).json()["id"] for _ in range(2)}
    assert len(blanks) == 2
    assert _count(db_path) == 3


def test_a_repeat_is_validated_before_it_is_replayed(client, db_path) -> None:
    """As the fixture's Idempotency-Key note says."""
    first = _post(client, VALID, **{"Idempotency-Key": "k1"})

    different = _post(client, {**VALID, "device": "Other"}, **{"Idempotency-Key": "k1"})
    invalid = _post(client, {**VALID, "device": ""}, **{"Idempotency-Key": "k1"})

    assert (different.status_code, different.json()) == (202, first.json())
    assert invalid.status_code == 400
    assert _count(db_path) == 1


def test_a_repeat_is_replayed_even_when_the_queue_has_filled(client, monkeypatch) -> None:
    monkeypatch.setenv(web_requests.MAX_PENDING_ENV_VAR, "1")
    first = _post(client, VALID, **{"Idempotency-Key": "k1"})

    assert _post(client, VALID).status_code == 429
    again = _post(client, VALID, **{"Idempotency-Key": "k1"})
    assert (again.status_code, again.json()) == (202, first.json())


def test_idempotency_key_cap_is_the_documented_one(client) -> None:
    cap = CONTRACT["headers"]["Idempotency-Key"]["max_length"]

    assert _post(client, VALID, **{"Idempotency-Key": "k" * cap}).status_code == 202
    over = _post(client, VALID, **{"Idempotency-Key": "k" * (cap + 1)})
    assert over.status_code == 400
    assert over.json()["errors"] == [{"field": "Idempotency-Key", "code": "too_long"}]


def test_body_cap_is_exactly_the_documented_one(client, db_path) -> None:
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    body = json.dumps(VALID).encode()
    at_cap = body + b" " * (CONTRACT["max_body_bytes"] - len(body))

    assert client.post(SUBMIT_PATH, content=at_cap, headers=headers).status_code == 202
    over = client.post(SUBMIT_PATH, content=at_cap + b" ", headers=headers)

    assert over.status_code == 413
    assert over.json() == CONTRACT["responses"]["413"]["body"]
    assert _count(db_path) == 1


@pytest.mark.parametrize("content_type", [None, "text/json", "application/jsonx", "text/plain"])
def test_only_application_json_is_taken(client, db_path, content_type) -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if content_type:
        headers["Content-Type"] = content_type

    response = client.post(SUBMIT_PATH, content=json.dumps(VALID), headers=headers)

    assert response.status_code == 415
    assert response.json() == CONTRACT["responses"]["415"]["body"]
    assert _count(db_path) == 0


def test_a_charset_on_the_content_type_is_fine(client) -> None:
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }
    assert client.post(SUBMIT_PATH, content=json.dumps(VALID), headers=headers).status_code == 202


def test_fixture_holds_no_live_looking_secret() -> None:
    """The token in the fixture is a placeholder and says so."""
    assert "not-a-real-one" in TOKEN
