import functools
import inspect
import types
from typing import Any, Callable

import flask
import sqlalchemy

import anki_note_tooling.config


def _has_parameter(func: Callable[..., Any], param_name: str, param_type: Any) -> bool:
    sig = inspect.signature(func)
    params = sig.parameters
    if (p := params.get(param_name)) is not None:
        return p.annotation == param_type
    else:
        return False


def add_routes(
    *,
    config: anki_note_tooling.config.Config,
    db: sqlalchemy.Engine,
    flask_app: flask.Flask,
    routes: list[tuple[str, list[str], Callable[..., Any]]],
):
    for path, methods, view_func in routes:
        assert isinstance(view_func, types.FunctionType)
        view_func_name = view_func.__name__

        if _has_parameter(view_func, "db", sqlalchemy.Engine):
            view_func = functools.partial(view_func, db=db)

        if _has_parameter(view_func, "config", anki_note_tooling.config.Config):
            view_func = functools.partial(view_func, config=config)

        flask_app.add_url_rule(path, view_func_name, view_func, methods=methods)
