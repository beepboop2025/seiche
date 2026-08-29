"""Seiche advertises the corpus without ingesting it in a request handler."""

from seiche.api import api_index


CORPUS_ROOT = "https://api.seiche.info/api/v2/corpus"


def test_api_index_projects_the_canonical_corpus_contract() -> None:
    corpus = api_index()["corpus"]

    assert corpus == {
        "catalog": f"{CORPUS_ROOT}/v1/catalog",
        "datasets": f"{CORPUS_ROOT}/v1/datasets",
        "bis_flows_for_seiche": f"{CORPUS_ROOT}/v1/bis/flows?product=seiche",
        "seiche_markets": f"{CORPUS_ROOT}/v1/seiche/markets",
        "seiche_exports": f"{CORPUS_ROOT}/v1/seiche/exports",
        "mcp": f"{CORPUS_ROOT}/mcp",
        "boundary": (
            "Discovery and public research evidence only. Catalog classes are "
            "not model, training, scoring, execution, or redistribution permission; "
            "restricted exports retain download=null."
        ),
    }


def test_corpus_discovery_keeps_permission_and_restriction_boundaries() -> None:
    corpus = api_index()["corpus"]
    boundary = corpus["boundary"].lower()

    assert "not model" in boundary
    assert "training" in boundary
    assert "execution" in boundary
    assert "download=null" in boundary
    assert all(str(value).startswith("https://") for key, value in corpus.items() if key != "boundary")
