import { NextResponse } from "next/server"
import { withAuth } from "@/lib/api/with-auth"
import {
  championReading,
  championRole,
  moveEntry,
  personTimeline,
  postEntry,
  sourceLabel,
  uuidOrNull,
} from "@/lib/gov/people/person-history.mjs"
import { getGovProfileById } from "@/lib/gov/shared/gov-profiles"
import { createServiceClient } from "@/lib/supabase/service"

const CHUNK = 150
const PAGE = 1000
const MOVE_MONTHS = 24
const POSITION_COLUMNS =
  "id,contact_id,organization_id,role_type,raw_title,valid_from,valid_to,date_basis,first_observed_at,last_observed_at,source,source_url"
const MOVE_COLUMNS =
  "id,gov_contact_id,name,event_type,title,previous_title,effective_date,date_basis,status,source_provider,source_url,evidence,organization_id,previous_organization_id"

async function paged(buildQuery) {
  const rows = []
  for (let offset = 0; ; offset += PAGE) {
    const { data, error } = await buildQuery().range(offset, offset + PAGE - 1)
    if (error) throw error
    rows.push(...(data || []))
    if ((data || []).length < PAGE) return rows
  }
}

async function inChunks(ids, buildQuery) {
  const chunks = []
  for (let start = 0; start < ids.length; start += CHUNK) chunks.push(ids.slice(start, start + CHUNK))
  return (await Promise.all(chunks.map((chunk) => paged(() => buildQuery(chunk))))).flat()
}

async function loadOffice({ params, supabase, account }) {
  const { id } = await params
  const organizationId = uuidOrNull(id)
  if (!organizationId) return { response: NextResponse.json({ error: "Unknown office" }, { status: 404 }) }
  const govProfile = await getGovProfileById(supabase, account.gov_profile_id)
  if (!govProfile) return { response: NextResponse.json({ error: "No gov profile linked" }, { status: 403 }) }
  const service = createServiceClient()
  if (!service) return { response: NextResponse.json({ error: "Service unavailable" }, { status: 500 }) }
  const { data: office, error } = await service.from("gov_organizations")
    .select("id,name,org_type,source,source_ref,parent_organization_id").eq("id", organizationId).maybeSingle()
  if (error) throw error
  if (!office) return { response: NextResponse.json({ error: "Unknown office" }, { status: 404 }) }
  return { govProfile, service, office }
}

/** The people an office holds now by role, its moves in and out over two years, and the company's tracking. */
export const GET = withAuth(async ({ params, supabase, account }) => {
  try {
    const context = await loadOffice({ params, supabase, account })
    if (context.response) return context.response
    const { govProfile, service, office } = context
    const since = new Date(Date.now() - MOVE_MONTHS * 30.44 * 86_400_000).toISOString().slice(0, 10)
    const [held, movesIn, movesOut, parent, tracked] = await Promise.all([
      paged(() => service.from("gov_contact_positions").select(POSITION_COLUMNS)
        .eq("organization_id", office.id).is("valid_to", null).order("id")),
      paged(() => service.from("gov_contact_role_history").select(MOVE_COLUMNS)
        .eq("organization_id", office.id).gte("effective_date", since).order("id")),
      paged(() => service.from("gov_contact_role_history").select(MOVE_COLUMNS)
        .eq("previous_organization_id", office.id).gte("effective_date", since).order("id")),
      office.parent_organization_id
        ? service.from("gov_organizations").select("id,name").eq("id", office.parent_organization_id).maybeSingle()
        : Promise.resolve({ data: null, error: null }),
      supabase.from("gov_tracked_offices").select("id,created_at")
        .eq("gov_profile_id", govProfile.id).eq("organization_id", office.id).maybeSingle(),
    ])
    if (parent.error) throw parent.error
    if (tracked.error) throw tracked.error

    const contactIds = [...new Set(held.map((row) => row.contact_id))]
    const [contacts, relationships] = await Promise.all([
      inChunks(contactIds, (chunk) => service.from("gov_contacts").select("id,name,title,agency,role").in("id", chunk).order("id")),
      inChunks(contactIds, (chunk) => supabase.from("gov_person_relationships").select("contact_id,stance,notes")
        .eq("gov_profile_id", govProfile.id).in("contact_id", chunk).order("id")),
    ])
    const officeById = new Map([[office.id, office]])
    const relationshipByContact = new Map(relationships.map((row) => [row.contact_id, row]))
    const postsByContact = new Map()
    for (const row of held) {
      postsByContact.set(row.contact_id, [...(postsByContact.get(row.contact_id) || []), postEntry(row, officeById)])
    }
    const listed = contacts.map((contact) => {
      const posts = postsByContact.get(contact.id) || []
      // The label of the best post here: a confirmed one, else the latest observed.
      const best = posts.find((post) => post.current)
        || [...posts].sort((a, b) => String(b.last_seen || "").localeCompare(String(a.last_seen || "")))[0]
      return {
        id: `contact:${contact.id}`,
        contact_id: contact.id,
        name: contact.name,
        title: contact.title,
        agency: contact.agency,
        roles: [...new Set(posts.map((post) => post.role_type))],
        first_seen: posts.map((post) => post.first_seen).filter(Boolean).sort()[0] || null,
        last_seen: posts.map((post) => post.last_seen).filter(Boolean).sort().at(-1) || null,
        since: posts.map((post) => post.started?.date).filter(Boolean).sort()[0] || null,
        sources: [...new Set(posts.map((post) => post.source_label))],
        stance: relationshipByContact.get(contact.id)?.stance || null,
        titles: [contact.title, ...posts.map((post) => post.title)],
        confirmed: posts.some((post) => post.current),
        listed: posts.some((post) => post.listed),
        standing_label: best?.standing_label || null,
      }
    }).sort((a, b) => String(a.name).localeCompare(String(b.name)))

    // People confirmed here or observed here in two years, each with its label; an open post nobody has observed in
    // two years is listed apart. Only an official statement makes someone confirmed current.
    const people = listed.filter((person) => person.listed)
    const notRecent = listed.filter((person) => !person.listed)

    // Champion readings only for people whose title names the role one needs; their full history is read.
    const candidates = people.filter((person) => championRole(person.titles)).map((person) => person.contact_id)
    const [candidatePositions, candidateMoves, interactions] = await Promise.all([
      inChunks(candidates, (chunk) => service.from("gov_contact_positions").select(POSITION_COLUMNS).in("contact_id", chunk).order("id")),
      inChunks(candidates, (chunk) => service.from("gov_contact_role_history").select(MOVE_COLUMNS).in("gov_contact_id", chunk).order("id")),
      inChunks(candidates, (chunk) => supabase.from("gov_person_interactions").select("contact_id,kind,happened_on")
        .eq("gov_profile_id", govProfile.id).in("contact_id", chunk).order("id")),
    ])
    const candidateOrgIds = [...new Set(candidatePositions.map((row) => row.organization_id).filter(Boolean))]
    const organizations = await inChunks(candidateOrgIds, (chunk) =>
      service.from("gov_organizations").select("id,name,org_type").in("id", chunk).order("id"))
    const champions = people.filter((person) => candidates.includes(person.contact_id)).map((person) => ({
      contact_id: person.contact_id,
      name: person.name,
      reading: championReading({
        timeline: personTimeline({
          positions: candidatePositions.filter((row) => row.contact_id === person.contact_id),
          moves: candidateMoves.filter((row) => row.gov_contact_id === person.contact_id),
          organizations,
        }),
        title: person.title,
        interactions: interactions.filter((row) => row.contact_id === person.contact_id),
        relationship: relationshipByContact.get(person.contact_id) || null,
      }),
    })).filter((item) => item.reading)

    const moveOrgIds = [...new Set([...movesIn, ...movesOut].flatMap((row) => [row.organization_id, row.previous_organization_id]).filter(Boolean))]
    const organizationsById = new Map((await inChunks(moveOrgIds, (chunk) =>
      service.from("gov_organizations").select("id,name,org_type").in("id", chunk).order("id"))).map((org) => [org.id, org]))
    const moves = [...new Map([...movesIn, ...movesOut].map((row) => [row.id, row])).values()]
      .filter((row) => row.status !== "retracted" && row.status !== "superseded")
      .map((row) => ({
        ...moveEntry(row, organizationsById),
        contact_id: row.gov_contact_id,
        name: row.name,
        // A departure names the office it left.
        direction: row.organization_id === office.id && row.event_type !== "departure" ? "in" : "out",
      }))
      .sort((a, b) => String(b.date).localeCompare(String(a.date)))

    return NextResponse.json({
      office: {
        id: office.id,
        name: office.name,
        org_type: office.org_type,
        code: office.source === "fpds_office" ? office.source_ref : null,
        source_label: sourceLabel(office.source),
        parent: parent.data || null,
      },
      tracked: Boolean(tracked.data),
      people: people.map(({ titles: _titles, ...person }) => person),
      not_recent: notRecent.map(({ titles: _titles, ...person }) => person),
      moves,
      champions,
    })
  } catch (err) {
    console.error("[gov-organizations/[id] GET]", err)
    return NextResponse.json({ error: "Failed to load office" }, { status: 500 })
  }
})

/** Track the office: moves into or out of it alert the company. */
export const PUT = withAuth(async ({ params, supabase, user, account }) => {
  try {
    const context = await loadOffice({ params, supabase, account })
    if (context.response) return context.response
    const { error } = await supabase.from("gov_tracked_offices").upsert(
      { gov_profile_id: context.govProfile.id, organization_id: context.office.id, created_by: user.id },
      { onConflict: "gov_profile_id,organization_id", ignoreDuplicates: true },
    )
    if (error) throw error
    return NextResponse.json({ tracked: true })
  } catch (err) {
    console.error("[gov-organizations/[id] PUT]", err)
    return NextResponse.json({ error: "Failed to track office" }, { status: 500 })
  }
})

export const DELETE = withAuth(async ({ params, supabase, account }) => {
  try {
    const context = await loadOffice({ params, supabase, account })
    if (context.response) return context.response
    const { error } = await supabase.from("gov_tracked_offices").delete()
      .eq("gov_profile_id", context.govProfile.id).eq("organization_id", context.office.id)
    if (error) throw error
    return NextResponse.json({ tracked: false })
  } catch (err) {
    console.error("[gov-organizations/[id] DELETE]", err)
    return NextResponse.json({ error: "Failed to stop tracking office" }, { status: 500 })
  }
})
