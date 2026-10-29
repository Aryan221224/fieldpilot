"""A local GPU server must never be mistaken for Nebius."""
import dataclasses

from fieldpilot.config import Config


def test_nebius_is_labelled_nebius():
    c = dataclasses.replace(Config(), nebius_api_key="k",
                            nebius_base_url="https://api.tokenfactory.nebius.com/v1/")
    assert c.on_nebius and c.mode()["llm"] == "nebius-token-factory"
    assert c.mode()["vision"] == "nebius-token-factory"


def test_local_gpu_server_is_labelled_local_everywhere():
    c = dataclasses.replace(Config(), nebius_api_key="ollama",
                            nebius_base_url="http://localhost:11434/v1/",
                            vision_base_url="http://127.0.0.1:8000/v1")
    m = c.mode()
    assert not c.on_nebius
    assert m["llm"].startswith("LOCAL-GPU") and m["vision"].startswith("LOCAL-GPU")
    assert m["llm"] != "nebius-token-factory"


def test_remote_vision_endpoint_keeps_its_label():
    c = dataclasses.replace(Config(), nebius_api_key="k",
                            vision_base_url="https://my-endpoint.example.org/v1")
    assert c.mode()["vision"] == "nebius-serverless-endpoint"
