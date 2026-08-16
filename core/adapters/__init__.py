"""Server flavor adapter registry and factory."""

from __future__ import annotations

from core.adapters.base import ServerFlavorAdapter
from core.adapters.fabric import FabricAdapter
from core.adapters.paper import FoliaAdapter, PaperAdapter, PurpurAdapter
from core.adapters.pocketmine import PocketMineAdapter
from core.adapters.quilt import QuiltAdapter
from core.adapters.vanilla import VanillaAdapter

_ADAPTERS: dict[str, type[ServerFlavorAdapter]] = {
    "paper": PaperAdapter,
    "folia": FoliaAdapter,
    "purpur": PurpurAdapter,
    "vanilla": VanillaAdapter,
    "fabric": FabricAdapter,
    "quilt": QuiltAdapter,
    "pocketmine": PocketMineAdapter,
}


def get_flavor_adapter(flavor: str) -> ServerFlavorAdapter:
    """Retrieve an instantiated adapter for the specified server flavor."""
    adapter_cls = _ADAPTERS.get(flavor.lower())
    if not adapter_cls:
        raise ValueError(f"Unsupported server flavor: '{flavor}'")
    return adapter_cls()


def list_supported_flavors() -> list[str]:
    """Return a list of all registered flavor identifiers."""
    return list(_ADAPTERS.keys())


__all__ = [
    "ServerFlavorAdapter",
    "PaperAdapter",
    "FoliaAdapter",
    "PurpurAdapter",
    "VanillaAdapter",
    "FabricAdapter",
    "QuiltAdapter",
    "PocketMineAdapter",
    "get_flavor_adapter",
    "list_supported_flavors",
]
