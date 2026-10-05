#!/usr/bin/env python3
"""Read the Army's own publications into the organization memory (the `memory` stage of the army profile).

    AGENCY=army python research/tools/org_memory_army.py build     # research/agencies/army/memory/organization_seed.json
    AGENCY=army python research/tools/org_memory_army.py --check   # exit 1 when a build would change the file
    AGENCY=army python research/tools/org_memory_army.py collect   # fetch the documents the build reads, not yet saved
    python research/tools/org_memory_army.py --selfcheck

Three sources, each node resting on a dated observation in the source's own words:

- The Office of the ASA(ALT) organization chart (PDF): the Assistant Secretary, the six Portfolio Acquisition
  Executives (PAE), the fourteen PMEs, PEO ACWA, CPE Enterprise Software and Services, the Army Contracting Command,
  and the leader each box names (`leads`, with the role the box prints). The chart places a PME under a PAE only by
  column, so that edge is `inferred` and says so. Staff boxes (DASAs, deputies without a portfolio, USAASC, PIT) are
  not requirement owners and are left out.
- The Army Materiel Command acquisition forecast: the PEO, JPEO and CPE commands its Command column names and the
  program offices its PM / Directorate column names under each, as the forecast prints them. These are the names
  the forecast and the notices still carry; no source here states which PAE or PME succeeded which PEO, so none is
  claimed. The other Army commands the column names (IMCOM, TACOM, INSCOM) are nodes too, under the parent the value
  prints ("AMC: TACOM") and under none when it prints none.
- SAM.gov federal organization records: the Army, AMC, ACC and the contracting offices the profile sweeps, with
  the DoDAAC each record states and the hierarchy it prints.

Every record is `review_status: draft` and carries `generator: org_memory_army`; a rebuild is byte for byte the
same while the saved documents are.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import kept_page  # noqa: E402
from agency import KEY, MANIFEST, MEMORY, NOTE_TAG, P, ROOT  # noqa: E402
from lrae_package import ARMY_RELEASES, read_sheet  # noqa: E402
from org_memory_darpa import node, observation, relationship, unique_aliases  # noqa: E402
from org_memory_lrae import squash  # noqa: E402

SEED = MEMORY / "organization_seed.json"
GENERATOR = "org_memory_army"
AGENCY_ID = P["agency"]["node"]
ASAALT_ID = "office:asaalt"
AMC_ID, ACC_ID = "command:amc", "command:acc"
CHART_URL = "https://api.army.mil/e2/c/downloads/2026/09/10/55469c8c/20260910-asaalt-org-chart-pae-pme.pdf"
FORECAST = ARMY_RELEASES[0]
FORECAST_URL = "https://api.army.mil/e2/c/downloads/2026/05/27/be8e6aeb/enclosure-1-fy26-amc-acquisition-forecast-jun-dec-2026.xlsx"
OSBP_URL = "https://www.army.mil/osbp"  # the Office of Small Business Programs page that links the forecast workbooks
SAM_ORG = "https://sam.gov/api/prod/federalorganizations/v1/organizations/{}"
# SAM.gov organization id -> node: the department, its materiel and contracting commands, the swept offices.
SAM_NODES = {"300000201": AGENCY_ID, "100000117": AMC_ID, "100007305": ACC_ID, **P["sam_org_nodes"]}
PERSON_RE = re.compile(r"^(?:Mr|Ms|Mrs|Dr)\.\s|^(?:GEN|LTG|MG|BG|COL|LTC|SGM|CSM)\s")
# A box's first line -> the kind of office it is. The chart prints the kind first, the portfolio after.
BOX_KINDS = {"PAE": "acquisition_portfolio", "PME": "program_executive_office", "PEO": "program_executive_office",
             "CPE": "program_executive_office"}
DEPUTY_RE = re.compile(r"^(?:Acting )?Deputy PAE$")
COMMAND_RE = re.compile(r"^(J?PEO|CPE)\s")  # the forecast's acquisition commands; the rest are the commands it buys for
# The parents a Command value prints ("AMC: TACOM", "HQ, DA"), by the node the memory has or makes for them.
COMMAND_PARENTS = {"AMC": AMC_ID, "DA": AGENCY_ID}
# Customers outside the Department of the Army the forecast also buys for. The Army memory carries no node for them, so
# their rows keep the office as printed and name no Army office.
OUTSIDE_ARMY = {"AIR FORCE", "NAVY", "USMC", "OSD", "DOD", "DECA", "DODEA", "DPAA", "CDAO", "USEUCOM", "USSOUTHCOM", "USAFRICOM",
                "USPACOM"}
LEADER_REACH = 40  # points: a leader's name sits this close above the box it heads
CONTRACT = ("08_org_memory_format.md: observations are what a source states; relationships are dated claims resting on "
            "observations; interpretations are our readings; corrections are retractions")
SCOPE = ("Department of the Army acquisition: ASA(ALT), its portfolio executives and their PMEs, the PEOs and program offices "
         "the AMC forecast names, the materiel and contracting commands and the contracting offices swept")
NOTES = "drafted by org_memory_army from the ASA(ALT) chart, the AMC forecast and SAM.gov organization records; names as printed in the source"


def rows() -> list[dict]:
    return [json.loads(l) for l in MANIFEST.read_text(encoding="utf-8").splitlines() if l.strip()]


def by_url(manifest: list[dict], url: str) -> dict | None:
    hits = [r for r in manifest if kept_page(r) and (ROOT / r["path"]).exists()
            and url in (r.get("url"), r.get("final_url"))]
    return hits[-1] if hits else None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---------------------------------------------------------------- the chart

def chart_blocks(bbox_html: str) -> list[dict]:
    """Every text block of the chart as poppler lays it out: its box and its lines, words as printed."""
    out = []
    for b in re.finditer(r'<block xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</block>', bbox_html, re.S):
        lines = [" ".join(html.unescape(w) for w in re.findall(r"<word[^>]*>([^<]*)</word>", l))
                 for l in re.findall(r"<line[^>]*>(.*?)</line>", b.group(5), re.S)]
        out.append({"x0": float(b[1]), "y0": float(b[2]), "x1": float(b[3]), "y1": float(b[4]), "lines": lines})
    return out


def leader_of(box: dict, blocks: list[dict]) -> dict | None:
    """The person block just above a box and overlapping it: the name the chart prints over the box."""
    above = [b for b in blocks if len(b["lines"]) == 1 and PERSON_RE.match(b["lines"][0]) and b["y0"] < box["y0"]
             and box["y0"] - b["y0"] <= LEADER_REACH and min(b["x1"], box["x1"]) > max(b["x0"], box["x0"])]
    return max(above, key=lambda b: b["y0"]) if above else None


def person_id(name: str) -> str:
    return "person:" + slug(PERSON_RE.sub("", name))


def center(b: dict) -> float:
    return (b["x0"] + b["x1"]) / 2


def chart_boxes(blocks: list[dict]) -> list[dict]:
    """The offices the chart shows, with the leader each names: {kind, name, type, leader, role, box}."""
    out = []
    for b in blocks:
        head, rest = b["lines"][0], " ".join(b["lines"][1:])
        if head in BOX_KINDS and rest:
            kind, name, role = head, f"{head} {rest}", head
        elif DEPUTY_RE.match(head):
            kind, name, role = "deputy", f"PAE {' '.join(b['lines'][1:])}", head
        elif " ".join(b["lines"]) == "Army Contracting Command":
            kind, name, role = "command", "Army Contracting Command", "Army Contracting Command"
        elif head == "ASA(ALT) & AAE":
            kind, name, role = "asaalt", "ASA(ALT)", head
        else:
            continue
        lead = leader_of(b, blocks)
        out.append({"kind": kind, "name": name, "leader": lead["lines"][0] if lead else None, "role": role, "box": b,
                    "passage": " ".join(([lead["lines"][0]] if lead else []) + b["lines"])})
    return out


def column_parent(box: dict, paes: list[dict]) -> dict | None:
    """The PAE whose column a PME box stands in: the nearest PAE box by horizontal centre, within a column's width."""
    near = min(paes, key=lambda p: abs(center(p["box"]) - center(box)), default=None)
    return near if near and abs(center(near["box"]) - center(box)) < 60 else None


def chart_text(row: dict) -> str:
    return subprocess.run(["pdftotext", "-bbox-layout", str(ROOT / row["path"]), "-"], check=True, capture_output=True, text=True).stdout


# ---------------------------------------------------------------- the forecast

def forecast_offices(rows_: list[dict]) -> tuple[Counter, Counter]:
    """(command -> rows, (command, PM / Directorate) -> rows) for the forecast's acquisition commands."""
    commands, pairs = Counter(), Counter()
    for r in rows_:
        if COMMAND_RE.match(r["command"]):
            commands[r["command"]] += 1
            if r["pm_directorate"]:
                pairs[(r["command"], r["pm_directorate"])] += 1
    return commands, pairs


def command_id(command: str) -> str:
    head, rest = command.split(" ", 1)
    return f"{head.lower()}:{slug(rest)}"


def named_commands(rows_: list[dict]) -> Counter:
    """command -> rows for the Army commands the forecast buys for: its Command values but the acquisition commands."""
    return Counter(r["command"] for r in rows_ if r["command"] and not COMMAND_RE.match(r["command"]) and r["command"] not in OUTSIDE_ARMY)


def command_place(command: str) -> tuple[str, str, str]:
    """(node id, name, parent id) of a Command value: 'AMC: TACOM' -> ('command:tacom', 'TACOM', AMC_ID); 'HQ, AMC' is
    AMC's own headquarters, so AMC's node; 'INSCOM' prints no parent, so none."""
    hq = re.fullmatch(r"HQ,\s*(.+)", command)
    if hq:
        return COMMAND_PARENTS.get(hq[1], f"command:{slug(hq[1])}"), hq[1], ""
    parent, sep, name = (x.strip() for x in command.partition(":"))
    if sep:
        return f"command:{slug(name)}", name, COMMAND_PARENTS.get(parent, f"command:{slug(parent)}")
    return f"command:{slug(command)}", command, ""


# ---------------------------------------------------------------- SAM.gov organization records

# The fields of a SAM.gov federal organization record the memory quotes: its place in the hierarchy by key and by name.
SAM_FIELDS = ("orgKey", "name", "type", "level", "fullParentPath", "fullParentPathName", "aacCode", "fpdsCode", "cgac", "codeHierarchy")


def sam_record(row: dict) -> dict:
    """The organization a saved federalorganizations answer carries: `{"_embedded": [{"org": {...}}]}`."""
    data = json.loads((ROOT / row["path"]).read_text(encoding="utf-8"))
    return (data.get("_embedded") or [{}])[0].get("org") or {}


def sam_parent(rec: dict, known: dict[str, str]) -> str | None:
    """The nearest organization above this one on the record's own path that the memory has a node for."""
    path = str(rec.get("fullParentPath") or "").split(".")[:-1]
    return next((known[k] for k in reversed(path) if k in known), None)


# ---------------------------------------------------------------- the seed

def build_seed(manifest: list[dict]) -> dict:
    obs, rels, nodes = [], [], []
    counter = {"obs": 0, "rel": 0}

    def add_obs(row, observed_at, kind, passage, subjects) -> str:
        counter["obs"] += 1
        oid = f"obs:army:{counter['obs']:03d}"
        obs.append(observation(oid, row, observed_at, kind, passage, subjects))
        return oid

    def add_rel(kind, src, dst, obs_ids, as_of, **kw) -> None:
        counter["rel"] += 1
        rel = relationship(f"rel:army:{counter['rel']:03d}", kind, src, dst, obs_ids, as_of, **kw)
        rel["generator"] = GENERATOR
        rels.append(rel)

    def add_node(*args, **kw) -> None:
        n = node(*args, **kw)
        n["generator"] = GENERATOR
        n["notes"] = kw.get("notes", NOTES)
        nodes.append(n)

    chart = by_url(manifest, CHART_URL)
    forecast = by_url(manifest, FORECAST_URL)
    sam = {oid: by_url(manifest, SAM_ORG.format(oid)) for oid in SAM_NODES}
    missing = [k for k, v in (("chart", chart), ("forecast", forecast), *((f"sam {k}", v) for k, v in sam.items())) if v is None]
    if missing:
        raise SystemExit(f"documents not saved yet: {', '.join(missing)}; run `collect` first")

    # SAM.gov: the department, AMC, ACC and the swept contracting offices, each on its own record.
    for org_id, nid in SAM_NODES.items():
        row, rec = sam[org_id], sam_record(sam[org_id])
        name = squash(str(rec.get("name") or ""))
        passage = json.dumps({k: rec[k] for k in SAM_FIELDS if rec.get(k) is not None}, separators=(",", ":"), ensure_ascii=False)
        oid = add_obs(row, row["retrieved_at"][:10], "naming", passage, [nid])
        codes = {"sam_organization_id": org_id}
        aac = str(rec.get("aacCode") or "").strip()
        if aac:
            codes["uic"] = aac
        if nid == AGENCY_ID:
            codes["fpds_agency_id"] = str(rec.get("fpdsCode") or P["agency"]["subtier_code"])
        kind = "agency" if nid == AGENCY_ID else "command" if nid in (AMC_ID, ACC_ID) else "contracting_office"
        aliases = [{"text": aac, "observation_ids": [oid]}] if aac else []
        if nid == AGENCY_ID:
            aliases.append({"text": P["agency"]["subtier_name"], "observation_ids": [oid]})
        add_node(nid, kind, name or P["fpds_offices"].get(aac, nid), [oid], unique_aliases(aliases), codes)
        parent = sam_parent(rec, SAM_NODES)
        if parent:
            add_rel("child_of", nid, parent, [oid], row["retrieved_at"][:10], dates_status="unknown",
                    dates_note="the record states the organization's place in the hierarchy, not since when",
                    status_note=f"stated by the SAM.gov organization record of the latest cited retrieval ({rec.get('fullParentPathName')}); "
                                "not re-verified since")
        if kind == "contracting_office":
            add_rel("contracts_for", nid, AGENCY_ID, [oid], row["retrieved_at"][:10], role="contracting office of the Army Contracting Command",
                    dates_status="unknown", status_note="stated by the SAM.gov organization record; not re-verified since")

    # The chart: ASA(ALT), its portfolio executives, their PMEs and the offices outside a portfolio.
    bbox = chart_text(chart)
    as_of = re.search(r"As of (\d{2})/(\d{2})/(\d{2})", " ".join(" ".join(b["lines"]) for b in chart_blocks(bbox)))
    chart_day = f"20{as_of[3]}-{as_of[1]}-{as_of[2]}" if as_of else chart["retrieved_at"][:10]
    boxes = chart_boxes(chart_blocks(bbox))
    paes = [b for b in boxes if b["kind"] == "PAE"]
    ids = {}
    for b in boxes:
        if b["kind"] in ("deputy", "command"):
            continue
        nid = ASAALT_ID if b["kind"] == "asaalt" else f"{b['kind'].lower()}:{slug(b['name'].split(' ', 1)[1])}"
        ids[b["name"]] = nid
        oid = add_obs(chart, chart_day, "existence", b["passage"], [nid] + ([person_id(b["leader"])] if b["leader"] else []))
        b["obs"] = oid
        if nid == ASAALT_ID:
            # A secretariat office, not a contracting activity (a command loads as one); named by the code the chart prints.
            add_node(nid, "department", "Office of the Assistant Secretary of the Army (Acquisition, Logistics and Technology)", [oid],
                     [{"text": "ASA(ALT)", "observation_ids": [oid]}], codes={"office_code": "ASA(ALT)"})
            add_rel("child_of", nid, AGENCY_ID, [oid], chart_day, dates_status="unknown", status_note="the chart of the latest cited retrieval; not re-verified since")
            continue
        add_node(nid, BOX_KINDS[b["kind"]], b["name"], [oid])
        pae = column_parent(b["box"], paes) if b["kind"] == "PME" else None
        if pae:
            add_rel("child_of", nid, ids[pae["name"]], [oid, pae["obs"]], chart_day, dates_status="unknown",
                    status_note=f"the generator's inference: the chart prints this PME in the column of {pae['name']}; no line or text on the chart states it")
            rels[-1]["evidence_class"] = "inferred"
        else:
            add_rel("child_of", nid, ASAALT_ID, [oid], chart_day, dates_status="unknown",
                    status_note="shown on the Office of the ASA(ALT) chart of the latest cited retrieval; not re-verified since")
    for b in boxes:
        if not b["leader"]:
            continue
        target = ACC_ID if b["kind"] == "command" else ids.get(b["name"])
        if not target:
            continue
        pid = person_id(b["leader"])
        oid = b.get("obs") if b["kind"] not in ("deputy", "command") else add_obs(chart, chart_day, "leadership", b["passage"], [pid, target])
        if not any(n["id"] == pid for n in nodes):
            add_node(pid, "person", b["leader"], [oid], notes="public official role only; recorded with source and observation date; " + NOTES)
        add_rel("leads", pid, target, [oid], chart_day, role=b["role"], dates_status="unknown",
                dates_note="the chart states who holds the role on its date, not since when",
                status_note="named in this box on the chart of the latest cited retrieval; not re-verified since")

    # The forecast: the acquisition commands and the program offices under them, as its columns print them.
    _, forecast_rows = read_sheet(ROOT / forecast["path"], FORECAST["key"], FORECAST["sheet"], FORECAST["header_row"])
    commands, pairs = forecast_offices(forecast_rows)
    day = FORECAST["release_date"]
    for command, n in sorted(commands.items()):
        cid = command_id(command)
        oid = add_obs(forecast, day, "existence", f"column 'Command': '{command}' ({n} rows)", [cid])
        hq = sorted(pm for c, pm in pairs if c == command and pm.startswith("HQ,"))
        codes = {"office_code": command} | {f"office_code_hq{i or ''}": f"{command} - {pm}" for i, pm in enumerate(hq)}
        add_node(cid, "program_executive_office", command, [oid], [], codes,
                 notes=f"named in the Command column of the AMC forecast; the chart of {chart_day} prints PAEs and PMEs, and no source here "
                       "states which of them stands where this office stood; " + NOTES)
    for (command, pm), n in sorted(pairs.items()):
        if pm.startswith("HQ,"):
            continue
        cid, nid = command_id(command), f"pm:{slug(command)}--{slug(pm)}"
        oid = add_obs(forecast, day, "existence", f"column 'Command': '{command}'; column 'PM / Directorate': '{pm}' ({n} rows)", [nid, cid])
        aliases = [{"text": pm, "observation_ids": [oid]}] if re.match(r"(?:J?PM|PL)\s", pm) else []  # a name that stands alone
        add_node(nid, "program_office", f"{command} {pm}", [oid], aliases, {"office_code": f"{command} - {pm}"})
        add_rel("child_of", nid, cid, [oid], day, dates_status="unknown",
                dates_note="the forecast's rows state the office under the command, not since when",
                status_note="stated by the forecast rows of the latest cited release; not re-verified since")

    # The Army commands the forecast buys for. A value that names a node the memory has ("HQ, AMC", "HQ, DA") is an alias
    # of it; a parent the memory lacks ("AFC" of "AFC: CCDC") is made from the value that prints it.
    have = {n["id"]: n for n in nodes}
    for command, n in sorted(named_commands(forecast_rows).items()):
        nid, name, parent = command_place(command)
        oid = add_obs(forecast, day, "existence", f"column 'Command': '{command}' ({n} rows)", [nid] + [parent] * bool(parent))
        if parent and parent not in have:
            head = command.partition(":")[0].strip()
            add_node(parent, "command", head, [oid], [], {"office_code": head},
                     notes=f"printed as the parent of '{command}' in the Command column of the AMC forecast; " + NOTES)
            have[parent] = nodes[-1]
        if nid in have:
            have[nid]["aliases"] = unique_aliases(have[nid]["aliases"] + [{"text": command, "observation_ids": [oid]}])
            have[nid]["observation_ids"].append(oid)
            continue
        add_node(nid, "command", name, [oid], [], {"office_code": command},
                 notes="named in the Command column of the AMC forecast" + ("" if parent else ", which prints no parent for it") + "; " + NOTES)
        have[nid] = nodes[-1]
        if parent:
            add_rel("child_of", nid, parent, [oid], day, dates_status="unknown",
                    dates_note="the forecast's Command column prints the command under its parent, not since when",
                    status_note="stated by the forecast rows of the latest cited release; not re-verified since")

    return {"generated": max(chart_day, day, *(r["retrieved_at"][:10] for r in sam.values())), "contract": CONTRACT, "scope": SCOPE,
            "nodes": nodes, "observations": obs, "relationships": rels, "interpretations": []}


def dumps(seed: dict) -> str:
    return json.dumps(seed, indent=1, ensure_ascii=False) + "\n"


def build(check: bool = False) -> int:
    if KEY != "army":
        print(f"this reader builds the army memory; the profile is {KEY} (set AGENCY=army)", file=sys.stderr)
        return 2
    seed = build_seed(rows())
    text = dumps(seed)
    kinds = Counter(n["type"] for n in seed["nodes"])
    print(f"{len(seed['nodes'])} node(s) {dict(kinds)}, {len(seed['observations'])} observation(s), {len(seed['relationships'])} relationship(s)")
    if check:
        if not SEED.exists() or SEED.read_text(encoding="utf-8") != text:
            print(f"{SEED.relative_to(ROOT)} differs from a fresh build; run `build` to regenerate", file=sys.stderr)
            return 1
        return 0
    SEED.parent.mkdir(parents=True, exist_ok=True)
    SEED.write_text(text, encoding="utf-8")
    print(f"written to {SEED.relative_to(ROOT)}")
    return 0


def collect() -> int:
    """The chart and the forecast through the hosted browser (api.army.mil refuses this address), the SAM.gov
    organization records directly."""
    from fetch import collect_missing  # noqa: E402
    manifest = rows()
    # The forecast is answered only as a link of the small business page, so that page is fetched and its forecast
    # workbooks follow from it.
    files = [u for u in (CHART_URL,) if by_url(manifest, u) is None] + ([OSBP_URL] if by_url(manifest, FORECAST_URL) is None else [])
    failed = subprocess.run([sys.executable, str(Path(__file__).parent / "browserbase_fetch.py"), *files], cwd=ROOT).returncode if files else 0
    wanted = [(SAM_ORG.format(oid), f"SAM.gov federal organization record{NOTE_TAG}: {oid} ({nid})") for oid, nid in SAM_NODES.items()]
    return 1 if collect_missing(wanted) or failed else 0


def selfcheck() -> int:
    # The W6QK ACC-RSA record as SAM.gov answered it on 2026-09-25, trimmed: its nearest known ancestor is ACC.
    rec = {"orgKey": 500045573, "name": "W6QK ACC-RSA", "type": "OFFICE", "aacCode": "W58RGZ",
           "fullParentPath": "100000000.300000201.100000117.100007305.500038569.500038572.500045573"}
    assert sam_parent(rec, SAM_NODES) == ACC_ID and sam_parent({"fullParentPath": "100000000.300000201"}, SAM_NODES) is None
    assert sam_parent({"fullParentPath": "100000000.300000201.100000117"}, SAM_NODES) == AGENCY_ID
    bbox = ('<block xMin="340" yMin="232" xMax="454" yMax="244"><line><word>MG</word><word>Christopher</word><word>Schneider</word></line></block>'
            '<block xMin="379" yMin="251" xMax="454" yMax="290"><line><word>PAE</word></line><line><word>Agile</word><word>Sustainment</word></line>'
            '<line><word>&amp;</word><word>Ammo</word></line></block>'
            '<block xMin="373" yMin="539" xMax="453" yMax="550"><line><word>COL</word><word>Steve</word><word>Adcock</word></line></block>'
            '<block xMin="396" yMin="557" xMax="437" yMax="600"><line><word>PME</word></line><line><word>Ammunition</word><word>&amp;</word></line>'
            '<line><word>Energetics</word></line></block>'
            '<block xMin="675" yMin="258" xMax="694" yMax="280"><line><word>PAE</word></line><line><word>Fires</word></line></block>'
            '<block xMin="42" yMin="295" xMax="120" yMax="306"><line><word>Ms.</word><word>Kirsten</word><word>Taylor</word></line></block>'
            '<block xMin="63" yMin="309" xMax="112" yMax="330"><line><word>DASA</word></line><line><word>Plans</word></line></block>')
    boxes = chart_boxes(chart_blocks(bbox))
    assert [(b["kind"], b["name"], b["leader"]) for b in boxes] == [
        ("PAE", "PAE Agile Sustainment & Ammo", "MG Christopher Schneider"), ("PME", "PME Ammunition & Energetics", "COL Steve Adcock"),
        ("PAE", "PAE Fires", None)], boxes
    paes = [b for b in boxes if b["kind"] == "PAE"]
    assert column_parent(boxes[1]["box"], paes)["name"] == "PAE Agile Sustainment & Ammo", "a PME stands in its PAE's column"
    assert column_parent({"x0": 1500, "x1": 1540}, paes) is None, "a box in no PAE's column has none"
    assert boxes[0]["passage"] == "MG Christopher Schneider PAE Agile Sustainment & Ammo"
    rows_ = [{"command": "PEO AVIATION", "pm_directorate": "UAS"}, {"command": "PEO AVIATION", "pm_directorate": "UAS"},
             {"command": "PEO AVIATION", "pm_directorate": "HQ, PEO AVN"}, {"command": "AMC: TACOM", "pm_directorate": "TACOM: ILSC"},
             {"command": "CPE C2IN", "pm_directorate": ""}]
    commands, pairs = forecast_offices(rows_)
    assert commands == {"PEO AVIATION": 3, "CPE C2IN": 1} and pairs[("PEO AVIATION", "UAS")] == 2 and ("AMC: TACOM", "TACOM: ILSC") not in pairs
    assert person_id("BG Robert Mikesh Jr.") == "person:robert-mikesh-jr" and person_id("Dr. Steven Smith") == "person:steven-smith"
    assert command_id("PEO CS AND CSS") == "peo:cs-and-css" and command_id("JPEO AA") == "jpeo:aa" and command_id("CPE C2IN") == "cpe:c2in"
    assert named_commands(rows_ + [{"command": "NAVY"}, {"command": "INSCOM"}]) == {"AMC: TACOM": 1, "INSCOM": 1}
    assert [command_place(c) for c in ("AMC: TACOM", "AFC: CCDC", "HQ, AMC", "HQ, DA", "INSCOM")] == [
        ("command:tacom", "TACOM", AMC_ID), ("command:ccdc", "CCDC", "command:afc"), (AMC_ID, "AMC", ""), (AGENCY_ID, "DA", ""),
        ("command:inscom", "INSCOM", "")]
    print("org_memory_army selfcheck ok")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--selfcheck" in argv:
        sys.exit(selfcheck())
    if argv and argv[0] == "collect":
        sys.exit(collect())
    if argv and argv[0] == "--check":
        sys.exit(build(check=True))
    if argv and argv[0] == "build":
        sys.exit(build())
    print(__doc__)
    sys.exit(2)
