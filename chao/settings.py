"""Typed configuration with one declared precedence chain.

An entry point declares its fields once: key, parser, default and a doc
line. Sources are flat dicts of raw values (GUI parameters, a config file,
command-line flags), listed highest precedence first. load() is the only
place values are merged: unknown keys and missing required keys are errors,
every value is parsed once, and the origin of each value is recorded.
"""
from typing import Any, Callable, NamedTuple

REQUIRED = object()


class Field(NamedTuple):
    key: str
    parse: Callable[[Any], Any]
    default: Any
    doc: str


class Loaded(NamedTuple):
    values: dict    # key -> parsed value; the entry point builds its record
    origins: dict   # key -> source name, or 'default'


class ConfigError(ValueError):
    pass


def text(value):
    return str(value)


def integer(value):
    return int(value)


def number(value):
    return float(value)


def id_list(value):
    """'1,2,7', [1, 2, 7] or a GUI number 3 -> tuple of ints."""
    if isinstance(value, (int, float)):
        return (int(value),)
    items = value.split(',') if isinstance(value, str) else value
    return tuple(int(x) for x in items if str(x).strip())


def path_map(value):
    """{'2': '/a'} or '2=/a,6=/b' -> {2: '/a', 6: '/b'}."""
    if isinstance(value, str):
        value = dict(item.split('=', 1) for item in value.split(',') if item.strip())
    return {int(k): str(v) for k, v in value.items()}


def load(fields, sources):
    """Merge sources [(name, {key: raw})], highest precedence first."""
    known = {f.key for f in fields}
    for name, values in sources:
        unknown = sorted(set(values) - known)
        if unknown:
            raise ConfigError('{}: unknown keys {}'.format(name, unknown))
    parsed, origins = {}, {}
    for field in fields:
        origin = next((name for name, values in sources if field.key in values), None)
        if origin is None:
            if field.default is REQUIRED:
                raise ConfigError('missing required setting {!r}: {}'.format(field.key, field.doc))
            parsed[field.key], origins[field.key] = field.default, 'default'
            continue
        raw = dict(sources)[origin][field.key]
        try:
            parsed[field.key] = field.parse(raw)
        except (TypeError, ValueError) as exc:
            raise ConfigError('{}: bad value {!r} for {!r}: {}'.format(origin, raw, field.key, exc))
        origins[field.key] = origin
    return Loaded(parsed, origins)


def describe(loaded):
    """One 'key = value  (origin)' line per setting, like git config --show-origin."""
    return ['{} = {!r}  ({})'.format(k, v, loaded.origins[k])
            for k, v in loaded.values.items()]
