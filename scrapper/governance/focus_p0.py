"""Compatibility facade for existing governed research behavior."""
from ..research.governance import focus_p0 as _owner

__all__ = [name for name in dir(_owner) if not name.startswith("_")]
globals().update({name: getattr(_owner, name) for name in __all__})

def __getattr__(name):
    return getattr(_owner, name)

def __dir__():
    return sorted(set(globals()) | set(dir(_owner)))
