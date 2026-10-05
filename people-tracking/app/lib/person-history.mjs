// A person's dated posts and moves, as the people sources record them. Every date says whether a source
// stated it or Chromie only observed the person in the post; the timeline never presents one as the other.
// A post is "confirmed current" only while an official source recently stated it; otherwise it reads "last
// observed here" with its date. A move is confirmed only when an official source states it; a move seen in
// two records is inferred, and a licensed profile's move is reported.

export const STANCES = ["target", "engaged", "champion", "detractor", "unknown"]
export const INTERACTION_KINDS = ["meeting", "call", "email", "event", "note", "other"]

export const SOURCE_LABELS = {
  fpds_staff: "FPDS contract actions",
  sam_notice_contacts: "SAM.gov notice contact",
  bio_pages: "Official biography",
  war_gov_releases: "war.gov release",
  congress_nominations: "Senate nomination",
  dvids_leadership: "DVIDS story",
  directory_affiliation: "Official staff directory",
  orange_slices: "Licensed intelligence source",
  coresignal: "Coresignal (licensed people data)",
  orange_slice: "Orange Slice (licensed people data)",
}

// gov_contact_positions.role_type values.
export const ROLE_TITLES = {
  acquisition_leader: "Acquisition leader",
  program_manager: "Program Manager",
  deputy_program_manager: "Deputy Program Manager",
  program_staff: "Program staff",
  technical_lead: "Technical lead",
  contracting_leader: "Contracting leader",
  contracting_officer: "Contracting Officer",
  contract_specialist: "Contract Specialist",
  cor: "Contracting Officer's Representative",
  contract_administrator: "Contract administrator",
  other: "Government staff",
}

// Sources whose statement can confirm a post or a move: the government's own publications.
const OFFICIAL_SOURCES = new Set(["bio_pages", "war_gov_releases", "congress_nominations", "dvids_leadership", "directory_affiliation"])
// A statement confirms a post for six months from the day a source last showed the person in it.
const CONFIRMED_DAYS = 183
const EVENT_NOUNS = {
  appointment: "appointment", transfer: "move", promotion: "promotion", acting: "acting appointment",
  announcement: "announced assignment", departure: "departure",
}

function day(value) {
  return value ? String(value).slice(0, 10) : null
}

export function sourceLabel(source) {
  return SOURCE_LABELS[source] || String(source || "Source")
}

/**
 * One post: title, office, start and end with the basis of each, the source, and its standing. An open post is
 * confirmed current only while an official source showed the person in it within six months; any other open post
 * reads "last observed here" with its date, and is listed with an office for two years. Observed posts never
 * close on their own.
 */
export function postEntry(position, organizationsById = new Map(), asOf = Date.now()) {
  const organization = organizationsById.get(position.organization_id) || null
  const stated = position.date_basis === "stated"
  const started = position.valid_from && stated
  const open = !position.valid_to
  const lastShown = day(position.last_observed_at) || (started ? day(position.valid_from) : null)
  const confirmed = open && stated && OFFICIAL_SOURCES.has(position.source) && within(lastShown, CONFIRMED_DAYS, asOf)
  const standing = !open ? "ended" : confirmed ? "confirmed_current" : "last_observed"
  return {
    id: position.id,
    title: position.raw_title || ROLE_TITLES[position.role_type] || "Government staff",
    role_type: position.role_type,
    organization: organization ? { id: organization.id, name: organization.name, org_type: organization.org_type } : null,
    // An observed post has no start date; its first observation is shown as such.
    started: started ? { date: day(position.valid_from), basis: "stated" } : null,
    first_seen: day(position.first_observed_at),
    last_seen: day(position.last_observed_at),
    ended: position.valid_to ? { date: day(position.valid_to), basis: position.date_basis || "observed" } : null,
    standing,
    standing_label: standingLabel({ standing, stated, lastShown, end: day(position.valid_to), source: position.source }),
    current: standing === "confirmed_current",
    // Listed with the office: confirmed, or observed there in the last two years (shown with that date).
    listed: open && (confirmed || within(lastShown, LISTED_DAYS, asOf)),
    source: position.source,
    source_label: sourceLabel(position.source),
    source_url: position.source_url || null,
  }
}

function standingLabel({ standing, stated, lastShown, end, source }) {
  if (standing === "confirmed_current") return `Confirmed current as of ${lastShown} (${sourceLabel(source)})`
  if (standing === "ended") return stated ? `Left ${end} (stated)` : `Last observed here ${end}; post closed`
  if (stated && OFFICIAL_SOURCES.has(source)) return `Last confirmed ${lastShown} (${sourceLabel(source)}); not confirmed since`
  return lastShown ? `Last observed here ${lastShown}` : "Not dated"
}

/**
 * How sure a move is: confirmed when an official source states it, reported when a licensed profile states it,
 * inferred when Chromie saw the person in two records (one office, then another) and no source states the move.
 */
export function moveCertainty(move) {
  const source = move.source_provider ?? move.source
  const stated = (move.date_basis ?? move.basis) === "stated"
  const certainty = stated && OFFICIAL_SOURCES.has(source) ? "confirmed" : stated ? "reported" : "inferred"
  const noun = EVENT_NOUNS[move.event_type] || "move"
  const label = certainty === "confirmed" ? `Confirmed ${noun}`
    : certainty === "reported" ? `Reported ${noun} (a licensed profile states it; no official source yet)`
      : `Inferred ${noun} (seen in two records; no source states it)`
  return { certainty, certainty_label: label }
}

/** One ledger move; retracted moves are left out. A vendor profile states a month, so its date is a month. */
export function moveEntry(move, organizationsById = new Map()) {
  const organization = organizationsById.get(move.organization_id) || null
  const previous = organizationsById.get(move.previous_organization_id) || null
  const evidence = Array.isArray(move.evidence) ? move.evidence : []
  const month = evidence.some((item) => item?.date_precision === "month")
  const after = evidence.find((item) => item?.now) || null
  return {
    id: move.id,
    event_type: move.event_type,
    title: move.title,
    previous_title: move.previous_title || null,
    organization: organization ? { id: organization.id, name: organization.name } : null,
    previous_organization: previous ? { id: previous.id, name: previous.name } : null,
    date: month ? day(move.effective_date)?.slice(0, 7) || null : day(move.effective_date),
    basis: move.date_basis || "stated",
    // Where a departed person went, as the profile states it.
    now: after?.now || null,
    employer: after?.employer || null,
    outside_government: after?.outside_government === true,
    status: move.status || "current",
    ...moveCertainty(move),
    source: move.source_provider,
    source_label: sourceLabel(move.source_provider),
    source_url: move.source_url || null,
    evidence,
  }
}

function postSortKey(post) {
  return post.ended?.date || post.last_seen || post.started?.date || ""
}

/** Current posts first (latest seen first), then past posts by end date; moves newest first. */
export function personTimeline({ positions = [], moves = [], organizations = [], asOf = Date.now() } = {}) {
  const organizationsById = new Map(organizations.map((organization) => [organization.id, organization]))
  const posts = positions.map((position) => postEntry(position, organizationsById, asOf))
  posts.sort((a, b) => Number(b.current) - Number(a.current) || postSortKey(b).localeCompare(postSortKey(a)))
  const ledger = moves
    .filter((move) => move.status !== "retracted" && move.status !== "superseded")
    .map((move) => moveEntry(move, organizationsById))
    .sort((a, b) => String(b.date).localeCompare(String(a.date)))
  return { posts, moves: ledger }
}

/** The post a person is confirmed to hold now, else nothing: an observed post is never presented as current. */
export function currentPost(positions = [], organizations = [], asOf = Date.now()) {
  return personTimeline({ positions, organizations, asOf }).posts.find((post) => post.current) || null
}

/** "since 2024-03 (stated)" or "seen 2024-03 to 2025-06" for one post. */
export function postDates(post) {
  const start = post.started ? `since ${post.started.date}` : post.first_seen ? `seen from ${post.first_seen}` : ""
  if (!post.ended) return [start, post.last_seen && !post.started ? `last seen ${post.last_seen}` : ""].filter(Boolean).join(", ")
  const end = post.ended.basis === "stated" ? `left ${post.ended.date}` : `last seen ${post.ended.date}`
  return [start, end].filter(Boolean).join(", ")
}

export function cleanRelationship(body = {}) {
  const stance = String(body.stance || "").trim()
  if (!STANCES.includes(stance)) return { error: `stance must be one of ${STANCES.join(", ")}` }
  return { value: { stance, notes: String(body.notes || "").trim().slice(0, 4000) } }
}

export function cleanInteraction(body = {}, today = new Date().toISOString().slice(0, 10)) {
  const kind = String(body.kind || "").trim()
  const summary = String(body.summary || "").trim().slice(0, 4000)
  const happenedOn = String(body.happened_on || today).slice(0, 10)
  if (!INTERACTION_KINDS.includes(kind)) return { error: `kind must be one of ${INTERACTION_KINDS.join(", ")}` }
  if (!summary) return { error: "summary is required" }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(happenedOn) || Number.isNaN(Date.parse(happenedOn)) || happenedOn > today) {
    return { error: "happened_on must be a past date (YYYY-MM-DD)" }
  }
  return { value: { kind, summary, happened_on: happenedOn } }
}

export function uuidOrNull(value) {
  const id = String(value || "").trim()
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id) ? id.toLowerCase() : null
}

/** The contact id behind a directory person id ("contact:<uuid>") or a bare uuid. */
export function contactIdFrom(value) {
  return uuidOrNull(String(value || "").trim().replace(/^contact(?::|%3A)/i, ""))
}

// Potential champions, the research stakeholder rule: someone whose title names a leadership or requirements post,
// confirmed in it or observed there within six months, with two or more kinds of signal that they are acting now
// (a new post plus a meeting is enough). It is a reading, never a fact; each signal carries its date and source.
// A title alone establishes neither budget authority nor willingness to champion, so the reading also shows the
// problem they own, why the company matters to them, any evidence of advocacy, and what is still to confirm.
const DAY_MS = 86_400_000
const NEW_IN_POST_DAYS = 548
const RECENT_NOTICE_DAYS = 180
const RECENT_TALK_DAYS = 365
const LISTED_DAYS = 730
const DECISION_MAKER_RE =
  /\bdirector\b|program executive|acquisition executive|commander|commanding officer|\bchief\b|secretary|assistant (?:commissioner|commandant|administrator)/i
const PROBLEM_OWNER_RE = /requirements (?:contact|development|branch|division|manager|officer|lead)/i
const MOVE_INTO_POST = new Set(["appointment", "transfer", "promotion", "acting", "announcement"])
const TALK_KINDS = new Set(["meeting", "call", "event"])

function ageDays(value, asOf) {
  const time = Date.parse(String(value || "").slice(0, 10))
  return Number.isFinite(time) ? (asOf - time) / DAY_MS : Number.NaN
}

function within(value, days, asOf) {
  const age = ageDays(value, asOf)
  return age >= 0 && age <= days
}

/** Whether any title names a decision maker or problem owner, the role a champion candidate needs. */
export function championRole(titles = []) {
  const named = titles.filter(Boolean)
  if (named.some((value) => DECISION_MAKER_RE.test(value))) return "decision_maker"
  return named.some((value) => PROBLEM_OWNER_RE.test(value)) ? "problem_owner" : null
}

/**
 * The potential-champion reading for one person, or null. `timeline` is personTimeline output; `interactions` and
 * `relationship` are the viewing company's own, so a tenant's meetings and notes count only for that tenant.
 */
export function championReading({ timeline, title = "", interactions = [], relationship = null, asOf = Date.now() }) {
  const { posts = [], moves = [] } = timeline || {}
  const current = posts.filter((post) => post.standing !== "ended"
    && (post.current || within(post.last_seen || post.started?.date, CONFIRMED_DAYS, asOf)))
  if (!current.length) return null
  const role = championRole([title, ...current.map((post) => post.title)])
  if (!role) return null

  const signals = []
  const started = current.find((post) => post.started && within(post.started.date, NEW_IN_POST_DAYS, asOf))
  const moved = moves.find((move) => MOVE_INTO_POST.has(move.event_type) && within(move.date, NEW_IN_POST_DAYS, asOf))
  if (started || moved) {
    signals.push(started
      ? { kind: "new_in_post", text: `In the post since ${started.started.date}`, date: started.started.date,
          basis: "stated", source_label: started.source_label, source_url: started.source_url }
      : { kind: "new_in_post", text: `${moved.title} (${moved.event_type})`, date: moved.date, basis: moved.basis,
          source_label: moved.source_label, source_url: moved.source_url })
  }
  const notice = posts.find((post) => post.source === "sam_notice_contacts" && within(post.last_seen, RECENT_NOTICE_DAYS, asOf))
  if (notice) {
    signals.push({ kind: "solicitation_out", text: `Named on a SAM.gov notice: ${notice.title}`, date: notice.last_seen,
      basis: "observed", source_label: notice.source_label, source_url: notice.source_url })
  }
  const talk = interactions
    .filter((item) => TALK_KINDS.has(item.kind) && within(item.happened_on, RECENT_TALK_DAYS, asOf))
    .sort((a, b) => String(b.happened_on).localeCompare(String(a.happened_on)))[0]
  if (talk) {
    signals.push({ kind: "talks_to_industry", text: `Met your team (${talk.kind})`, date: talk.happened_on,
      basis: "stated", source_label: "Your team's log", source_url: null })
  }
  if (signals.length < 2) return null

  const post = current.find((item) => championRole([item.title])) || current[0]
  const why = String(relationship?.notes || "").trim()
  const advocacy = relationship?.stance === "champion"
    ? { text: "Your team marked them as a champion", source_label: "Your team's record" } : null
  const toConfirm = [
    post.current ? null : `Whether they still hold the post (${post.standing_label.toLowerCase()})`,
    notice ? null : "What requirement they own",
    why ? null : "Why your company matters to them",
    "Whether they will advocate for you internally",
    role === "decision_maker" ? "Whether the post carries budget or decision authority for your work" : null,
  ].filter(Boolean)
  return {
    kind: "potential_champion",
    reading: true,
    role,
    documented_role: {
      title: post.title, office: post.organization?.name || null, standing_label: post.standing_label,
      source_label: post.source_label, source_url: post.source_url,
    },
    problem_owned: notice
      ? { text: `The requirement of the SAM.gov notice that names them (${notice.title})`, date: notice.last_seen,
          source_label: notice.source_label, source_url: notice.source_url }
      : null,
    why_it_matters: why ? { text: why, source_label: "Your team's notes" } : null,
    advocacy,
    to_confirm: toConfirm,
    signals,
  }
}

// Post-employment rules: 18 U.S.C. 207 bars former officials from representing anyone back to their agency on
// matters they handled (one or two years, permanently for a particular matter); 41 U.S.C. 2104 bars pay from a
// contractor for a year after certain roles on its contracts. Read as a signal, never as a finding about a person.
const REVOLVING_DOOR_DAYS = 730

/** A reading for someone who left government for an outside employer within two years, or null. */
export function revolvingDoorReading({ timeline, asOf = Date.now() }) {
  const move = (timeline?.moves || []).find((item) => item.event_type === "departure" && item.status === "current"
    && item.outside_government && item.employer && within(item.date, REVOLVING_DOOR_DAYS, asOf))
  if (!move) return null
  return {
    kind: "revolving_door",
    reading: true,
    employer: move.employer,
    left: move.date,
    text: `Left ${move.title} (${move.date}) and now works for ${move.employer.replace(/\.$/, "")}. Federal post-employment rules `
      + "(18 U.S.C. 207, 41 U.S.C. 2104) can limit their work on matters they handled there, so that office's "
      + "recompetes are where this change matters.",
    source_label: move.source_label,
    source_url: move.source_url,
  }
}
