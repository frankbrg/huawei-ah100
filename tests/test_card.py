from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.huawei_ah100 import card


async def test_card_is_served_once_and_loaded_with_the_integration_version() -> None:
    hass = SimpleNamespace(data={}, http=SimpleNamespace(async_register_static_paths=AsyncMock()))
    with (
        patch.object(
            card, "async_get_integration", AsyncMock(return_value=SimpleNamespace(version="0.3.0"))
        ),
        patch.object(card, "add_extra_js_url") as add_url,
    ):
        await card.async_register_card(hass)
        await card.async_register_card(hass)

    hass.http.async_register_static_paths.assert_awaited_once()
    (paths,) = hass.http.async_register_static_paths.await_args.args
    assert [(path.url_path, path.path, path.cache_headers) for path in paths] == [
        ("/huawei_ah100/huawei-ah100-card.js", str(card.CARD_PATH), True)
    ]
    add_url.assert_called_once_with(hass, "/huawei_ah100/huawei-ah100-card.js?v=0.3.0")
    assert card.CARD_PATH.is_file()
