"""Project-specific Model Evolution adapters."""

from importlib.metadata import entry_points

from .base import ProjectAdapter


def load_adapter(name: str) -> ProjectAdapter:
    matches = list(entry_points().select(group="model_evolution.adapters", name=name))
    if not matches:
        raise ValueError(f"unknown Model Evolution adapter: {name}")
    if len(matches) != 1:
        raise ValueError(f"multiple Model Evolution adapters registered as {name}")
    adapter = matches[0].load()
    instance = adapter() if isinstance(adapter, type) else adapter
    if instance.name != name:
        raise ValueError(
            f"Model Evolution adapter registered as {name} declares {instance.name}"
        )
    return instance


__all__ = ["ProjectAdapter", "load_adapter"]
