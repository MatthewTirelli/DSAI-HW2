# functions.py
# Homework 2 — OpenAI chat + tool execution helpers (self-contained in HW2/)

from __future__ import annotations

import inspect
import json
import os
import re
from pathlib import Path

import pandas as pd
from dotenv_loader import load_hw2_dotenv
from openai import OpenAI

# pip install tabulate  # used by DataFrame.to_markdown

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HW2_DIR = Path(__file__).resolve().parent

load_hw2_dotenv()


def _dotenv_help() -> str:
    return (
        "Set OPENAI_API_KEY in the repository root `.env` file "
        f"(recommended: `{_REPO_ROOT / '.env'}`) "
        f"or in `{_HW2_DIR / '.env'}`. "
        "Alternatively export OPENAI_API_KEY in your shell."
    )


def _default_model_value() -> str:
    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


# clinical_pipeline imports DEFAULT_MODEL — resolved after dotenv load
DEFAULT_MODEL = _default_model_value()


def _get_client() -> OpenAI:
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        raise RuntimeError("OPENAI_API_KEY is not set. " + _dotenv_help())
    return OpenAI()


_FUNCTIONS_FILE = Path(__file__).resolve()


def _globals_for_tool_dispatch():
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


def _build_assistant_message_from_openai(completion):
    msg = completion.choices[0].message
    out = {"role": "assistant", "content": msg.content or ""}
    tcs = msg.tool_calls or []
    if not tcs:
        return out
    serial = []
    for tc in tcs:
        fn = tc.function
        serial.append(
            {
                "id": getattr(tc, "id", ""),
                "type": "function",
                "function": {"name": fn.name, "arguments": fn.arguments or "{}"},
            }
        )
    out["tool_calls"] = serial
    return out


def agent(
    messages,
    model=None,
    output="text",
    tools=None,
    all=False,
    tool_choice="required",
    seed=None,
):
    """
    Single OpenAI chat completion; executes tool handlers when tool_calls present.
    """
    if model is None:
        model = DEFAULT_MODEL

    client = _get_client()

    if tools is None:
        plain_kw: dict = dict(model=model, messages=messages, temperature=0.2)
        if seed is not None:
            plain_kw["seed"] = int(seed)
        completion = client.chat.completions.create(**plain_kw)
        txt = completion.choices[0].message.content or ""
        if all:
            return {"message": {"role": "assistant", "content": txt, "tool_calls": []}}
        return txt

    kwargs = dict(model=model, messages=messages, tools=tools, temperature=0.2)
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    if seed is not None:
        kwargs["seed"] = int(seed)

    completion = client.chat.completions.create(**kwargs)

    assistant_msg = _build_assistant_message_from_openai(completion)
    tool_calls_serial = assistant_msg.get("tool_calls") or []

    if tool_calls_serial:
        caller_globals = _globals_for_tool_dispatch()
        for tc in tool_calls_serial:
            fn_block = tc.get("function") or {}
            func_name = fn_block.get("name")
            raw_args = fn_block.get("arguments", {})
            func_args = _parse_tool_arguments(raw_args)
            func = _resolve_tool_function(caller_globals, func_name)
            if func is None:
                raise RuntimeError(
                    f"Model requested unknown tool {func_name!r}. "
                    "Define a same-named function in the script that calls agent()."
                )
            tool_result = func(**func_args)
            tc["output"] = tool_result

    msg_wrap = assistant_msg if tool_calls_serial else {"role": "assistant", "content": assistant_msg.get("content") or "", "tool_calls": []}

    if all:
        return {"message": msg_wrap}

    if tool_calls_serial:
        if output == "tools":
            return tool_calls_serial
        last_out = tool_calls_serial[-1].get("output")
        if last_out is not None:
            return last_out
        return msg_wrap.get("content") or ""

    # No native tool_calls — try recover from prose
    content = assistant_msg.get("content") or ""
    if tools:
        caller_globals = _globals_for_tool_dispatch()
        recovered = _recover_tool_output_from_text_content(content, tools, caller_globals)
        if recovered is not None:
            rname, rval = recovered
            synthetic_call = {"function": {"name": rname, "arguments": "{}"}, "output": rval}
            if all:
                return {
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": [synthetic_call],
                        "_tool_recovery_from_content": True,
                    }
                }
            if output == "tools":
                return [synthetic_call]
            return rval

    return content


def agent_run(
    role,
    task,
    tools=None,
    output="text",
    model=None,
    tool_choice="required",
    seed=None,
):
    if model is None:
        model = DEFAULT_MODEL
    messages = [
        {"role": "system", "content": role},
        {"role": "user", "content": task},
    ]

    if tools is None:
        return agent(
            messages=messages,
            model=model,
            output=output,
            tools=None,
            seed=seed,
        )

    return agent(
        messages=messages,
        model=model,
        output=output,
        tools=tools,
        tool_choice=tool_choice,
        seed=seed,
    )


def df_as_text(df):
    """Convert a pandas DataFrame to a markdown table string."""
    return df.to_markdown(index=False)


def openai_api_configured() -> bool:
    return bool((os.environ.get("OPENAI_API_KEY") or "").strip())
