"""Save one source text under OUT/sources and record it in OUT/sources/index.jsonl, so the checker can tie each
row's quote to the source its Source cell names. Each saved result gets its own id (S1, S2, ... in index order),
printed with the entry; a row cites it as [S12], so two searches that share a prefix never stand in for each other.

usage: <command> | python research/workflow/save_source.py SOURCES_DIR REL_PATH --source "<url or command>"
       python research/workflow/save_source.py SOURCES_DIR REL_PATH --file --source "<command that wrote it>"
       python research/workflow/save_source.py SOURCES_DIR --failed "HTTP 403" --source "<url or command>"

An empty text or a short block page (access denied, rate limited) is recorded as a failed fetch, never as an empty
result: the brief must list it in Still open as not collected."""
import argparse
import json
import os
import re
import sys

# ponytail: a block page is short; a real page that merely mentions "403" is long, so length guards the regex
BLOCKED = re.compile(r"request rejected|access denied|forbidden|too many requests|rate limit exceeded|"
                     r"(?:http|error|status)\W{0,3}(?:403|429)|captcha|enable javascript|are you a robot", re.I)
BLOCK_PAGE_MAX = 2000
SECRET = re.compile(r"([?&](?:api_key|apikey|key|token|access_token))=[^&\s]+", re.I)  # a key never reaches the index


def failure(text):
    """Why a fetched text is not a usable source, or None."""
    if not text.strip():
        return "empty response"
    if len(text) < BLOCK_PAGE_MAX and BLOCKED.search(text):
        return "block page: " + BLOCKED.search(text).group(0)
    return None


def save(sources, rel, source, text=None, reason=None):
    source = SECRET.sub(r"\1=REDACTED", source)
    index = os.path.join(sources, "index.jsonl")
    os.makedirs(sources, exist_ok=True)
    with open(index, "a+") as fh:
        fh.seek(0)
        n = sum(1 for line in fh if line.strip())
    entry = {"id": f"S{n + 1}", "source": source, "file": rel, "status": "ok", "reason": ""}
    if reason is None and text is not None:
        reason = failure(text)
        if rel and text.strip():
            path = os.path.join(sources, rel)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w") as fh:
                fh.write(text)
    if reason:
        entry.update(status="failed", reason=reason)
    with open(index, "a") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("sources")
    ap.add_argument("rel", nargs="?")
    ap.add_argument("--source", required=True)
    ap.add_argument("--failed", help="record a failed fetch with this reason; nothing is saved")
    ap.add_argument("--file", action="store_true", help="index REL_PATH, already written by a tool")
    a = ap.parse_args(argv)
    os.makedirs(a.sources, exist_ok=True)
    if a.failed:
        entry = save(a.sources, a.rel, a.source, reason=a.failed)
    elif a.file:
        with open(os.path.join(a.sources, a.rel), errors="replace") as fh:
            entry = save(a.sources, a.rel, a.source, reason=failure(fh.read()) or "")
    else:
        if not a.rel:
            ap.error("REL_PATH is required unless --failed")
        entry = save(a.sources, a.rel, a.source, text=sys.stdin.read())
    print(json.dumps(entry))
    return 0 if entry["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
