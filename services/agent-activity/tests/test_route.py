import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("activity_route", Path(__file__).parents[1]/"ops/publish-route.py")
route = importlib.util.module_from_spec(spec)
spec.loader.exec_module(route)


def configuration(extra=""):
    return ("api.seiche.info {\n"
        "    @outside_research not path /api/v2/research-data /api/v2/research-data/*"+extra+"\n"
        "    header @outside_research Content-Security-Policy \"default-src 'none'\"\n"
        "    handle_path /agents/learning/* { reverse_proxy 127.0.0.1:18797 }\n"
        "    handle_path /agents/commerce/* { reverse_proxy 127.0.0.1:18798 }\n}\n")


def test_publish_exempts_only_activity_and_preserves_concurrent_exclusions():
    original = configuration(" /another-reviewed-dashboard /another-reviewed-dashboard/*")
    result = route.updated_config(original)
    assert "@outside_research not path /api/v2/research-data /api/v2/research-data/* /another-reviewed-dashboard /another-reviewed-dashboard/* /agents/activity /agents/activity/*\n" in result
    assert result.count("import /etc/caddy/agent-activity.caddy") == 1
    assert "handle_path /agents/learning/* { reverse_proxy 127.0.0.1:18797 }" in result
    assert "handle_path /agents/commerce/* { reverse_proxy 127.0.0.1:18798 }" in result
    assert "header @outside_research Content-Security-Policy \"default-src 'none'\"" in result
    assert route.updated_config(result) == result
    assert route.updated_config(result, remove=True) == original


def test_remove_preserves_exclusions_added_after_activity_was_published():
    published = route.updated_config(configuration())
    changed = published.replace("/agents/activity/*", "/agents/activity/* /new-owner-route/*")
    assert route.updated_config(changed, remove=True) == configuration(" /new-owner-route/*")


@pytest.mark.parametrize("text", ["unrecognized server configuration", configuration().replace("@outside_research", "@new_matcher"), configuration()+configuration()])
def test_unrecognized_shared_configuration_is_rejected(text):
    with pytest.raises(ValueError):
        route.updated_config(text)
