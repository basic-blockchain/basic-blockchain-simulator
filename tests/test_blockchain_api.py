import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent.parent / "basic-blockchain.py"


def load_app_module():
    spec = importlib.util.spec_from_file_location("basic_blockchain", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_home_endpoint_exposes_expected_routes():
    module = load_app_module()
    client = module.app.test_client()

    response = client.get("/")
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["message"] == "Blockchain simulator is running"
    assert payload["routes"] == {
        "mine_block": "/mine_block",
        "get_chain": "/get_chain",
        "valid": "/valid",
    }


def test_mine_block_increases_chain_length():
    module = load_app_module()
    client = module.app.test_client()

    initial_chain = client.get("/get_chain").get_json()
    initial_length = initial_chain["length"]

    mine_response = client.get("/mine_block")
    assert mine_response.status_code == 200

    updated_chain = client.get("/get_chain").get_json()
    assert updated_chain["length"] == initial_length + 1
