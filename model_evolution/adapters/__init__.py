"""Project-specific Model Evolution adapters."""

from importlib.metadata import entry_points

from .base import ProjectAdapter


def load_adapter(name: str) -> ProjectAdapter:
    if name == "latent-arborist":
        from .latent_arborist import LatentArboristAdapter

        return LatentArboristAdapter()
    matches = list(entry_points().select(group="model_evolution.adapters", name=name))
    if not matches:
        raise ValueError(f"unknown Model Evolution adapter: {name}")
    if len(matches) != 1:
        raise ValueError(f"multiple Model Evolution adapters registered as {name}")
    adapter = matches[0].load()
    return adapter() if isinstance(adapter, type) else adapter


__all__ = ["ProjectAdapter", "load_adapter"]
