# functions.py
# Homework 2 — Ollama chat + tool execution helpers (self-contained in HW2/)
# Adapted from 07_rag/LabHW2/functions.py
# Tim Fraser (course pattern)

# Provides agent(), agent_run(), and df_as_text() for Ollama chat + tool execution.
# Tool implementations live in the caller script; this module dispatches by function name.

# 0. SETUP ###################################

## 0.1 Load Packages #################################

import inspect
import json  # for working with JSON
import os
import re
from pathlib import Path

import pandas as pd  # for data manipulation
import requests  # for HTTP requests

# pip install requests pandas tabulate  # tabulate: used by DataFrame.to_markdown

## 0.2 Configuration #################################

# Canonical default for this assignment; override: export OLLAMA_MODEL=llama3.2:latest
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")
_ollama_port = os.environ.get("OLLAMA_PORT", "11434")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", f"http://127.0.0.1:{_ollama_port}").rstrip("/")
CHAT_URL = f"{OLLAMA_HOST}/api/chat"

# Fail fast if Ollama is down; allow long generation for slow models
REQUEST_TIMEOUT = (10, 600)


def _post_chat(body):
    """POST to Ollama /api/chat with timeout; raise RuntimeError if unreachable."""
    try:
        return requests.post(CHAT_URL, json=body, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(
            f"Cannot connect to Ollama at {OLLAMA_HOST}. "
            "Start it (e.g. run `ollama serve` in a terminal) and ensure the model is pulled."
        ) from e
    except requests.exceptions.Timeout as e:
        raise RuntimeError(
            "Ollama request timed out. The model may be loading or overloaded; try again."
        ) from e


_FUNCTIONS_FILE = Path(__file__).resolve()


def _globals_for_tool_dispatch():
    """
    Tool callables live in the *script that called agent_run*, not in this module.
    Walk the stack until we leave functions.py so tool lookup succeeds.
    """
    frame = inspect.currentframe().f_back
    while frame is not None:
        gpath = frame.f_globals.get("__file__")
        if gpath:
            try:
                if Path(gpath).resolve() != _FUNCTIONS_FILE:
                    return frame.f_globals
            except OSError:
                pass
        frame = frame.f_back
    return globals()


def _parse_tool_arguments(raw_args):
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        s = raw_args.strip() or "{}"
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return {}
    return {}


def _resolve_tool_function(caller_globals, func_name):
    if not func_name:
        return None
    fn = caller_globals.get(func_name) or globals().get(func_name)
    if fn is not None:
        return fn
    for key, val in caller_globals.items():
        if callable(val) and key.lower() == func_name.strip().lower():
            return val
    return None


def _normalize_embedded_tool_params(raw):
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return {}
    return {}


def _recover_tool_output_from_text_content(content, tools, caller_globals):
    """
    Some models return JSON-shaped tool text in message.content instead of tool_calls.
    If it matches a registered tool name, run it.
    """
    if not (content and tools):
        return None

    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```\s*$", "", text)

    registered = []
    for spec in tools:
        fn = (spec.get("function") or {}).get("name")
        if fn:
            registered.append(fn)
    if not registered:
        return None

    target_name = None
    params = {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            target_name = parsed.get("name") or parsed.get("tool")
            inner = parsed.get("function")
            if isinstance(inner, dict):
                target_name = inner.get("name") or target_name
            params = _normalize_embedded_tool_params(
                parsed.get("parameters") or parsed.get("arguments")
            )

    if not target_name or target_name not in registered:
        for fn in registered:
            if re.search(rf'"name"\s*:\s*"{re.escape(fn)}"', content):
                target_name = fn
                params = {}
                break

    if not target_name or target_name not in registered:
        return None

    func = _resolve_tool_function(caller_globals, target_name)
    if func is None:
        return None

    try:
        out = func(**params)
    except TypeError:
        try:
            out = func()
        except TypeError:
            return None

    return (target_name, out)


def _raise_ollama_ok(response):
    """Turn HTTP errors into actionable messages (404 usually = wrong service on port)."""
    if response.ok:
        return
    snippet = (response.text or "")[:500]
    if response.status_code == 404:
        raise RuntimeError(
            f"HTTP 404 from {response.url}\n"
            "That almost always means nothing at this address is serving Ollama's API "
            "(another app may be using the port, or OLLAMA_HOST is wrong).\n"
            f"Response body (truncated): {snippet!r}\n\n"
            "Checks:\n"
            f"  curl {OLLAMA_HOST}/api/tags\n"
            "  (should return JSON listing models when Ollama is running)\n"
            "  lsof -i :11434   # see which process owns the port (macOS/Linux)\n\n"
            "If Ollama uses a different URL, set e.g.:\n"
            "  export OLLAMA_HOST=http://127.0.0.1:11434"
        )
    response.raise_for_status()


# 1. AGENT FUNCTION ###################################


def agent(
    messages,
    model=DEFAULT_MODEL,
    output="text",
    tools=None,
    all=False,
    tool_choice="required",
):
    """
    Single Ollama chat turn, with or without tools.
    """
    if tools is None:
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": 2048},
        }

        response = _post_chat(body)
        _raise_ollama_ok(response)
        result = response.json()

        return result["message"]["content"]

    body = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "stream": False,
    }
    if tool_choice is not None:
        body["tool_choice"] = tool_choice

    response = _post_chat(body)
    _raise_ollama_ok(response)
    result = response.json()

    msg = result.get("message") or {}
    tool_calls = msg.get("tool_calls") or []

    if tool_calls:
        caller_globals = _globals_for_tool_dispatch()
        for tool_call in tool_calls:
            fn_block = tool_call.get("function") or {}
            func_name = fn_block.get("name") or tool_call.get("name")
            raw_args = fn_block.get("arguments", {})
            func_args = _parse_tool_arguments(raw_args)

            func = _resolve_tool_function(caller_globals, func_name)
            if func is None:
                raise RuntimeError(
                    f"Model requested unknown tool {func_name!r}. "
                    "Define a same-named function in the script that calls agent_run()."
                )
            tool_result = func(**func_args)
            tool_call["output"] = tool_result

    if all:
        return result

    if tool_calls:
        if output == "tools":
            return tool_calls
        last_out = tool_calls[-1].get("output")
        if last_out is not None:
            return last_out
        return msg.get("content") or ""

    if tools:
        caller_globals = _globals_for_tool_dispatch()
        recovered = _recover_tool_output_from_text_content(msg.get("content") or "", tools, caller_globals)
        if recovered is not None:
            rname, rval = recovered
            synthetic_call = {
                "function": {"name": rname, "arguments": "{}"},
                "output": rval,
            }
            # all=True: return full Ollama-shaped result so callers can trace tool execution
            # even when the model put JSON in .content instead of native tool_calls.
            if all:
                msg2 = result.setdefault("message", {})
                msg2["tool_calls"] = [synthetic_call]
                msg2["_tool_recovery_from_content"] = True
                return result
            if output == "tools":
                return [synthetic_call]
            return rval

    return msg.get("content") or ""


def agent_run(
    role,
    task,
    tools=None,
    output="text",
    model=DEFAULT_MODEL,
    tool_choice="required",
):
    """
    Run one agent turn: system role + user task.
    """
    messages = [
        {"role": "system", "content": role},
        {"role": "user", "content": task},
    ]

    if tools is None:
        resp = agent(
            messages=messages,
            model=model,
            output=output,
            tools=None,
        )
    else:
        resp = agent(
            messages=messages,
            model=model,
            output=output,
            tools=tools,
            tool_choice=tool_choice,
        )
    return resp


# 2. DATA CONVERSION FUNCTION ###################################


def df_as_text(df):
    """Convert a pandas DataFrame to a markdown table string."""
    tab = df.to_markdown(index=False)
    return tab
