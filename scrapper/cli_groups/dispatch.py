"""Lazy delegation to existing handlers without acquiring runtime resources."""

from importlib import import_module


def load_attribute(module: str, name: str):
    return getattr(import_module(module, package="scrapper"), name)


def lazy_handler(module: str, name: str):
    """Keep patchable facade callables while loading only the invoked subsystem."""
    def invoke(*args, **kwargs):
        return load_attribute(module, name)(*args, **kwargs)
    invoke.__name__ = name
    invoke.__qualname__ = name
    invoke.__doc__ = f"Lazy compatibility delegate to {module}.{name}."
    return invoke
