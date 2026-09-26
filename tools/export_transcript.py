"""
Claude Code session transcript exporter.

Reads a Claude Code session .jsonl and writes a readable Markdown transcript for
submission under logs/. It preserves the substantive coding session (user
prompts, assistant visible text, tool calls, tool results, timestamps, and
session metadata) and deliberately EXCLUDES Claude's internal thinking/reasoning
blocks and any signatures.

It does not summarize or invent content; every included block is rendered from
the actual record. A defensive redaction pass masks real secret VALUES (provider
API keys, bearer tokens, and any literal values found in a local .env) while
preserving variable NAMES like TAVILY_API_KEY.

Usage:
    python tools/export_transcript.py <session.jsonl> <output.md> [--env <.env path>]
"""
import argparse
import datetime
import json
import os
import re


# --- Redaction -------------------------------------------------------------

SECRET_PATTERNS = [
    ("GOOGLE_API_KEY", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("TAVILY_API_KEY", re.compile(r"tvly-[A-Za-z0-9_\-]{10,}")),
    ("OPENAI_API_KEY", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("AWS_ACCESS_KEY", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("BEARER_TOKEN", re.compile(r"([Bb]earer\s+)[A-Za-z0-9_\-\.=]{20,}")),
]


def load_env_secret_values(env_path):
    """Return {var_name: value} for non-empty values in a .env, used as exact
    redaction targets. Values themselves are never printed by this script."""
    out = {}
    if not env_path or not os.path.exists(env_path):
        return out
    try:
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if v and len(v) >= 8:
                out[k] = v
    except Exception:
        pass
    return out


def make_redactor(env_secrets, sig_prefixes=()):
    # Known thinking-signature material (from the source) is scrubbed wherever it appears,
    # e.g. if a tool result happened to echo a signature prefix.
    sig_re = None
    if sig_prefixes:
        sig_re = re.compile("(?:" + "|".join(re.escape(p) for p in sig_prefixes) + r")[A-Za-z0-9+/=]*")

    def redact(text):
        if not text:
            return text
        s = text
        if sig_re is not None:
            s = sig_re.sub("<REDACTED:thinking-signature>", s)
        # 1. Exact .env value matches -> named placeholder, variable name kept.
        for name, value in env_secrets.items():
            if value and value in s:
                s = s.replace(value, "<REDACTED:%s>" % name)
        # 2. Provider key / token shaped strings.
        for name, pat in SECRET_PATTERNS:
            if name == "BEARER_TOKEN":
                s = pat.sub(lambda m: m.group(1) + "<REDACTED:BEARER_TOKEN>", s)
            else:
                s = pat.sub("<REDACTED:%s>" % name, s)
        return s
    return redact


# --- Content extraction ----------------------------------------------------

def get_text_blocks(content):
    """Yield ('text'|'tool_use'|'tool_result', block) for a message content that
    may be a plain string or a list of structured blocks. Thinking blocks are
    intentionally skipped so no internal reasoning is emitted."""
    if isinstance(content, str):
        if content.strip():
            yield ("str", content)
        return
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "thinking" or bt == "redacted_thinking":
                continue  # never emit internal reasoning or signatures
            if bt in ("text", "tool_use", "tool_result"):
                yield (bt, b)


def tool_result_text(block):
    """Extract the textual content of a tool_result block (str or list of parts)."""
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for p in c:
            if isinstance(p, dict):
                if p.get("type") == "text":
                    parts.append(p.get("text", ""))
                elif p.get("type") == "image":
                    # Never inline base64 image bytes: keep an honest placeholder.
                    src = p.get("source") or {}
                    data = src.get("data", "")
                    kb = (len(data) // 1024) if isinstance(data, str) else 0
                    parts.append("[image omitted from transcript: %s, ~%d KB base64]"
                                 % (src.get("media_type", "image"), kb))
                else:
                    parts.append(json.dumps(p, ensure_ascii=False))
            else:
                parts.append(str(p))
        return "\n".join(parts)
    if c is None:
        return ""
    return json.dumps(c, ensure_ascii=False)


def fence(text, lang=""):
    """Wrap text in a code fence, choosing a fence long enough to be safe."""
    text = text.rstrip("\n")
    longest = 0
    for m in re.finditer(r"`+", text):
        longest = max(longest, len(m.group(0)))
    bar = "`" * max(3, longest + 1)
    return "%s%s\n%s\n%s" % (bar, lang, text, bar)


# --- Main export -----------------------------------------------------------

def export(jsonl_path, out_path, env_path):
    recs = []
    for line in open(jsonl_path, encoding="utf-8"):
        line = line.strip()
        if line:
            recs.append(json.loads(line))

    sig_prefixes = set()
    for o in recs:
        m = o.get("message")
        if isinstance(m, dict) and isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"):
                    sg = b.get("signature")
                    if isinstance(sg, str) and len(sg) >= 40:
                        sig_prefixes.add(sg[:40])
    redact = make_redactor(load_env_secret_values(env_path), sig_prefixes)

    # Session metadata from the first record that carries it.
    meta = {"sessionId": None, "cwd": None, "gitBranch": None, "version": None}
    for o in recs:
        for k in meta:
            if meta[k] is None and o.get(k):
                meta[k] = o.get(k)

    timestamps = [o.get("timestamp") for o in recs if o.get("timestamp")]
    counts = {"user_prompts": 0, "assistant_texts": 0, "tool_calls": 0, "tool_results": 0, "thinking_skipped": 0}

    lines = []
    lines.append("# Claude Code Session Transcript")
    lines.append("")
    lines.append("Exported for PS3 submission. Internal thinking/reasoning blocks are excluded by design.")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append("| Session ID | `%s` |" % (meta["sessionId"] or "unknown"))
    lines.append("| Working directory | `%s` |" % (meta["cwd"] or "unknown"))
    lines.append("| Git branch | `%s` |" % (meta["gitBranch"] or "unknown"))
    lines.append("| Client version | `%s` |" % (meta["version"] or "unknown"))
    if timestamps:
        lines.append("| First timestamp | %s |" % timestamps[0])
        lines.append("| Last timestamp | %s |" % timestamps[-1])
    lines.append("| Source file | `%s` |" % os.path.basename(jsonl_path))
    lines.append("| Exported at | %s |" % datetime.datetime.now(datetime.timezone.utc).isoformat())
    lines.append("")
    lines.append("---")
    lines.append("")

    def hdr(role, ts):
        ts = (ts or "")[:19]
        return "## %s%s" % (role, ("  ·  `%s`" % ts if ts else ""))

    for o in recs:
        typ = o.get("type")
        if typ not in ("user", "assistant"):
            continue  # skip pure-metadata records (mode, system, attachment, ...)
        msg = o.get("message")
        if not isinstance(msg, dict):
            continue
        ts = o.get("timestamp")
        role = msg.get("role")

        # Count skipped thinking blocks for the validation report.
        if isinstance(msg.get("content"), list):
            counts["thinking_skipped"] += sum(
                1 for b in msg["content"]
                if isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))

        for kind, block in get_text_blocks(msg.get("content")):
            if role == "user" and kind == "str":
                counts["user_prompts"] += 1
                lines.append(hdr("USER", ts))
                lines.append("")
                lines.append(redact(block))
                lines.append("")
            elif role == "user" and kind == "text":
                counts["user_prompts"] += 1
                lines.append(hdr("USER", ts))
                lines.append("")
                lines.append(redact(block.get("text", "")))
                lines.append("")
            elif kind == "tool_result":
                counts["tool_results"] += 1
                err = block.get("is_error")
                label = "TOOL RESULT" + (" (error)" if err else "")
                lines.append(hdr(label, ts))
                lines.append("")
                lines.append(fence(redact(tool_result_text(block))))
                lines.append("")
            elif role == "assistant" and kind == "text":
                counts["assistant_texts"] += 1
                lines.append(hdr("ASSISTANT", ts))
                lines.append("")
                lines.append(redact(block.get("text", "")))
                lines.append("")
            elif kind == "tool_use":
                counts["tool_calls"] += 1
                name = block.get("name", "?")
                lines.append(hdr("TOOL CALL → %s" % name, ts))
                lines.append("")
                inp = block.get("input", {})
                pretty = json.dumps(inp, ensure_ascii=False, indent=2)
                lines.append(fence(redact(pretty), "json"))
                lines.append("")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("out")
    ap.add_argument("--env", default=None)
    args = ap.parse_args()
    c = export(args.jsonl, args.out, args.env)
    print("Export counts:", c)
