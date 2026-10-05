import assert from "node:assert/strict"
import test from "node:test"
import {
  cleanInteraction,
  championReading,
  cleanRelationship,
  contactIdFrom,
  currentPost,
  personTimeline,
  postDates,
  revolvingDoorReading,
} from "./person-history.mjs"

const ORGS = [
  { id: "org-a", name: "OFFICE A", org_type: "contracting_office" },
  { id: "org-b", name: "OFFICE B", org_type: "contracting_office" },
]

const POSITIONS = [
  {
    id: "p-old", organization_id: "org-a", role_type: "contracting_officer", raw_title: null, valid_from: null,
    valid_to: "2024-04-10", date_basis: "observed", first_observed_at: "2024-02-10T00:00:00Z",
    last_observed_at: "2024-04-10T00:00:00Z", source: "fpds_staff", source_url: "https://www.fpds.gov/a",
  },
  {
    id: "p-new", organization_id: "org-b", role_type: "program_manager", raw_title: "Program Manager", valid_from: "2025-05-01",
    valid_to: null, date_basis: "stated", first_observed_at: "2025-05-10T00:00:00Z",
    last_observed_at: "2026-09-01T00:00:00Z", source: "bio_pages", source_url: "https://www.navy.mil/bio",
  },
]

const MOVES = [
  { id: "m1", event_type: "transfer", title: "Contracting Officer, OFFICE B", effective_date: "2025-05-10",
    date_basis: "observed", status: "current", source_provider: "fpds_staff", organization_id: "org-b",
    previous_organization_id: "org-a", source_url: "https://www.fpds.gov/b", evidence: [{ office_code: "B" }] },
  { id: "m0", event_type: "transfer", title: "Retracted", effective_date: "2025-06-01", date_basis: "observed",
    status: "retracted", source_provider: "fpds_staff", source_url: "https://www.fpds.gov/c" },
]

test("timeline keeps stated and observed dates apart and drops retracted moves", () => {
  const { posts, moves } = personTimeline({ positions: POSITIONS, moves: MOVES, organizations: ORGS })
  assert.deepEqual(posts.map((post) => post.id), ["p-new", "p-old"])
  assert.deepEqual(posts[0].started, { date: "2025-05-01", basis: "stated" })
  assert.equal(posts[1].started, null)
  assert.deepEqual(posts[1].ended, { date: "2024-04-10", basis: "observed" })
  assert.equal(postDates(posts[1]), "seen from 2024-02-10, last seen 2024-04-10")
  assert.equal(postDates(posts[0]), "since 2025-05-01")
  assert.deepEqual(moves.map((move) => [move.id, move.basis, move.organization.name, move.previous_organization.name]),
    [["m1", "observed", "OFFICE B", "OFFICE A"]])
  assert.equal(moves[0].source_label, "FPDS contract actions")
})

test("current post is the one an official source recently confirmed", () => {
  const asOf = Date.parse("2026-10-02")
  assert.equal(currentPost(POSITIONS, ORGS, asOf).title, "Program Manager")
  assert.equal(currentPost([POSITIONS[0]], ORGS, asOf), null)
  assert.equal(currentPost(POSITIONS, ORGS, Date.parse("2027-06-01")), null, "not confirmed for six months")
})

test("relationship and interaction input is validated", () => {
  assert.deepEqual(cleanRelationship({ stance: "champion", notes: " met at industry day " }).value,
    { stance: "champion", notes: "met at industry day" })
  assert.ok(cleanRelationship({ stance: "friend" }).error)
  assert.deepEqual(cleanInteraction({ kind: "call", summary: "Intro call", happened_on: "2026-09-30" }, "2026-10-02").value,
    { kind: "call", summary: "Intro call", happened_on: "2026-09-30" })
  assert.ok(cleanInteraction({ kind: "call", summary: "Later", happened_on: "2026-10-09" }, "2026-10-02").error)
  assert.ok(cleanInteraction({ kind: "call", summary: " " }, "2026-10-02").error)
})

test("directory person ids map to contact ids", () => {
  const id = "20000000-0000-4000-8000-0000000000C1"
  assert.equal(contactIdFrom(`contact:${id}`), id.toLowerCase())
  assert.equal(contactIdFrom("person:name:jane"), null)
})

test("a leadership title new in the post and named on a notice reads as a potential champion", () => {
  const asOf = Date.parse("2026-10-02")
  const timeline = personTimeline({
    positions: [
      { id: "p1", organization_id: "org-b", role_type: "program_manager", raw_title: "Commanding Officer, Example Unit",
        valid_from: "2026-01-15", valid_to: null, date_basis: "stated", first_observed_at: "2026-01-15",
        last_observed_at: "2026-09-01", source: "dvids_leadership", source_url: "https://www.dvidshub.net/news/1" },
      { id: "p2", organization_id: "org-b", role_type: "program_manager", raw_title: "Program contact", valid_from: null,
        valid_to: null, date_basis: "observed", first_observed_at: "2026-08-01", last_observed_at: "2026-08-20",
        source: "sam_notice_contacts", source_url: "https://sam.gov/opp/1" },
    ],
    organizations: ORGS,
  })
  const reading = championReading({ timeline, asOf })
  assert.equal(reading.reading, true)
  assert.equal(reading.role, "decision_maker")
  assert.deepEqual(reading.signals.map((signal) => signal.kind), ["new_in_post", "solicitation_out"])
  // One signal alone is not enough, and a contracting officer is not a decision maker by title.
  assert.equal(championReading({ timeline: { posts: [timeline.posts[0]], moves: [] }, asOf }), null)
  const officer = personTimeline({ positions: [{ ...POSITIONS[1], raw_title: "Contracting Officer" }], organizations: ORGS })
  assert.equal(championReading({ timeline: officer, asOf }), null)
})

test("the viewing company's own meetings count as talking to industry", () => {
  const asOf = Date.parse("2026-10-02")
  const timeline = personTimeline({
    positions: [{ id: "p1", organization_id: "org-b", role_type: "program_manager", raw_title: "Director, Example Office",
      valid_from: "2026-03-01", valid_to: null, date_basis: "stated", first_observed_at: "2026-03-01",
      last_observed_at: "2026-09-01", source: "bio_pages", source_url: "https://www.navy.mil/bio" }],
    organizations: ORGS,
  })
  assert.equal(championReading({ timeline, asOf }), null)
  const reading = championReading({ timeline, asOf, interactions: [{ kind: "meeting", happened_on: "2026-09-20", summary: "x" }] })
  assert.deepEqual(reading.signals.map((signal) => signal.kind), ["new_in_post", "talks_to_industry"])
})

test("a vendor departure shows its month, where the person went, and a revolving-door reading", () => {
  const asOf = Date.parse("2026-10-02")
  const timeline = personTimeline({
    moves: [{ id: "m-vendor", event_type: "departure", title: "Contract Specialist, OFFICE A", effective_date: "2025-02-01",
      date_basis: "stated", status: "current", source_provider: "coresignal", organization_id: "org-a",
      source_url: "https://www.linkedin.com/in/example-101",
      evidence: [{ source: "coresignal", date_precision: "month", now: "Vice President, Example Shipworks LLC",
        employer: "Example Shipworks LLC", outside_government: true }] }],
    organizations: ORGS,
  })
  const [move] = timeline.moves
  assert.deepEqual([move.date, move.now, move.source_label], ["2025-02", "Vice President, Example Shipworks LLC", "Coresignal (licensed people data)"])
  const reading = revolvingDoorReading({ timeline, asOf })
  assert.equal(reading.reading, true)
  assert.equal(reading.employer, "Example Shipworks LLC")
  assert.match(reading.text, /18 U\.S\.C\. 207/)
  // Too long ago, a retirement with no employer, or a move under review reads nothing.
  assert.equal(revolvingDoorReading({ timeline, asOf: Date.parse("2027-06-01") }), null)
  const retired = { moves: [{ ...move, employer: null, outside_government: true, now: "Retired" }] }
  assert.equal(revolvingDoorReading({ timeline: retired, asOf }), null)
  const review = { moves: [{ ...move, status: "review" }] }
  assert.equal(revolvingDoorReading({ timeline: review, asOf }), null)
  const incorporated = { moves: [{ ...move, employer: "Example Shipworks, Inc." }] }
  assert.match(revolvingDoorReading({ timeline: incorporated, asOf }).text, /works for Example Shipworks, Inc\. Federal/)
})

test("only a recent official statement makes a post current; anything else reads last observed here", () => {
  const asOf = Date.parse("2026-10-02")
  const open = { id: "p-old", organization_id: "org-a", role_type: "contracting_officer", source: "fpds_staff",
    date_basis: "observed", first_observed_at: "2021-08-19", last_observed_at: "2021-09-29", valid_to: null }
  const post = (patch) => personTimeline({ positions: [{ ...open, ...patch }], organizations: ORGS, asOf }).posts[0]
  const stale = post({})
  assert.deepEqual([stale.current, stale.listed, stale.standing_label], [false, false, "Last observed here 2021-09-29"])
  // Observed in an FPDS action three months ago: listed with the office, never presented as current.
  const recent = post({ last_observed_at: "2026-06-22" })
  assert.deepEqual([recent.current, recent.listed, recent.standing], [false, true, "last_observed"])
  assert.equal(recent.standing_label, "Last observed here 2026-06-22")
  // An official biography re-read last month confirms it, with the date and the source.
  const bio = post({ source: "bio_pages", date_basis: "stated", valid_from: "2019-01-07", last_observed_at: "2026-09-20" })
  assert.deepEqual([bio.current, bio.standing_label], [true, "Confirmed current as of 2026-09-20 (Official biography)"])
  // A 2019 release states the start but has not confirmed the post since: not current.
  const old = post({ source: "war_gov_releases", date_basis: "stated", valid_from: "2019-01-07", last_observed_at: "2019-01-07" })
  assert.deepEqual([old.current, old.listed], [false, false])
  assert.equal(old.standing_label, "Last confirmed 2019-01-07 (war.gov release); not confirmed since")
  // A licensed profile's stated start never confirms a government post.
  assert.equal(post({ source: "coresignal", date_basis: "stated", valid_from: "2026-05-01", last_observed_at: "2026-09-01" }).current, false)
  assert.equal(post({ valid_to: "2024-04-10", last_observed_at: "2024-04-10" }).standing_label, "Last observed here 2024-04-10; post closed")
})

test("moves say whether an official source confirms them, a licensed profile reports them, or two records imply them", () => {
  const base = { id: "m", title: "Program Manager, OFFICE B", effective_date: "2026-05-01", status: "current", organization_id: "org-b" }
  const [inferred] = personTimeline({ moves: [{ ...base, event_type: "transfer", date_basis: "observed", source_provider: "fpds_staff" }], organizations: ORGS }).moves
  assert.equal(inferred.certainty, "inferred")
  assert.equal(inferred.certainty_label, "Inferred move (seen in two records; no source states it)")
  const [confirmed] = personTimeline({ moves: [{ ...base, event_type: "appointment", date_basis: "stated", source_provider: "dvids_leadership" }], organizations: ORGS }).moves
  assert.deepEqual([confirmed.certainty, confirmed.certainty_label], ["confirmed", "Confirmed appointment"])
  const [reported] = personTimeline({ moves: [{ ...base, event_type: "departure", date_basis: "stated", source_provider: "coresignal" }], organizations: ORGS }).moves
  assert.equal(reported.certainty, "reported")
  assert.match(reported.certainty_label, /^Reported departure \(a licensed profile states it/)
})

test("a potential champion shows the documented role, the problem owned, why the company matters and what to confirm", () => {
  const asOf = Date.parse("2026-10-02")
  const timeline = personTimeline({
    positions: [{ id: "p1", organization_id: "org-b", role_type: "program_manager", raw_title: "Director, Example Office",
      valid_from: "2026-03-01", valid_to: null, date_basis: "stated", first_observed_at: "2026-03-01",
      last_observed_at: "2026-09-01", source: "bio_pages", source_url: "https://www.navy.mil/bio" }],
    organizations: ORGS,
    asOf,
  })
  const meeting = [{ kind: "meeting", happened_on: "2026-09-20", summary: "Intro" }]
  const reading = championReading({ timeline, asOf, interactions: meeting })
  assert.equal(reading.kind, "potential_champion")
  assert.deepEqual(reading.documented_role, { title: "Director, Example Office", office: "OFFICE B",
    standing_label: "Confirmed current as of 2026-09-01 (Official biography)", source_label: "Official biography",
    source_url: "https://www.navy.mil/bio" })
  assert.equal(reading.problem_owned, null)
  assert.equal(reading.why_it_matters, null)
  assert.equal(reading.advocacy, null)
  assert.deepEqual(reading.to_confirm, ["What requirement they own", "Why your company matters to them",
    "Whether they will advocate for you internally", "Whether the post carries budget or decision authority for your work"])
  const noted = championReading({ timeline, asOf, interactions: meeting,
    relationship: { stance: "champion", notes: "Owns the onboard autonomy gap our product closes" } })
  assert.deepEqual(noted.why_it_matters, { text: "Owns the onboard autonomy gap our product closes", source_label: "Your team's notes" })
  assert.equal(noted.advocacy.text, "Your team marked them as a champion")
  assert.ok(!noted.to_confirm.includes("Why your company matters to them"))
})
