import { NextResponse } from "next/server"
import { withAuth } from "@/lib/api/with-auth"
import {
  championReading,
  cleanInteraction,
  cleanRelationship,
  contactIdFrom,
  personTimeline,
  revolvingDoorReading,
} from "@/lib/gov/people/person-history.mjs"
import { getGovProfileById } from "@/lib/gov/shared/gov-profiles"
import { createServiceClient } from "@/lib/supabase/service"

// The shared people graph is read with the service client; the company's own relationship and interactions
// go through the session client, so row-level security keeps them inside the company.
const MOVE_COLUMNS =
  "id,event_type,title,previous_title,effective_date,date_basis,status,source_provider,source_url,evidence,organization_id,previous_organization_id"

async function loadContext({ params, supabase, account }) {
  const { id } = await params
  const contactId = contactIdFrom(id)
  if (!contactId) return { response: NextResponse.json({ error: "Unknown person" }, { status: 404 }) }
  const govProfile = await getGovProfileById(supabase, account.gov_profile_id)
  if (!govProfile) return { response: NextResponse.json({ error: "No gov profile linked" }, { status: 403 }) }
  const service = createServiceClient()
  if (!service) return { response: NextResponse.json({ error: "Service unavailable" }, { status: 500 }) }
  const { data: person, error } = await service
    .from("gov_contacts")
    .select("id,name,title,email,phone,agency,role,source,source_url,last_seen")
    .eq("id", contactId)
    .maybeSingle()
  if (error) throw error
  if (!person) return { response: NextResponse.json({ error: "Unknown person" }, { status: 404 }) }
  return { contactId, govProfile, service, person }
}

async function readTenant(supabase, govProfileId, contactId) {
  const [relationship, interactions] = await Promise.all([
    supabase.from("gov_person_relationships").select("id,stance,notes,owner_user_id,created_at,updated_at")
      .eq("gov_profile_id", govProfileId).eq("contact_id", contactId).maybeSingle(),
    supabase.from("gov_person_interactions").select("id,happened_on,kind,summary,gov_run_id,created_at")
      .eq("gov_profile_id", govProfileId).eq("contact_id", contactId)
      .order("happened_on", { ascending: false }).limit(50),
  ])
  if (relationship.error) throw relationship.error
  if (interactions.error) throw interactions.error
  return { relationship: relationship.data || null, interactions: interactions.data || [] }
}

export const GET = withAuth(async ({ params, supabase, account }) => {
  try {
    const context = await loadContext({ params, supabase, account })
    if (context.response) return context.response
    const { contactId, govProfile, service, person } = context
    const [positions, moves, tenant] = await Promise.all([
      service.from("gov_contact_positions")
        .select("id,organization_id,role_type,raw_title,valid_from,valid_to,date_basis,first_observed_at,last_observed_at,source,source_url")
        .eq("contact_id", contactId).limit(500),
      service.from("gov_contact_role_history").select(MOVE_COLUMNS).eq("gov_contact_id", contactId)
        .order("effective_date", { ascending: false }).limit(200),
      readTenant(supabase, govProfile.id, contactId),
    ])
    if (positions.error) throw positions.error
    if (moves.error) throw moves.error
    const organizationIds = [...new Set([
      ...(positions.data || []).map((row) => row.organization_id),
      ...(moves.data || []).flatMap((row) => [row.organization_id, row.previous_organization_id]),
    ].filter(Boolean))]
    const organizations = organizationIds.length
      ? await service.from("gov_organizations").select("id,name,org_type").in("id", organizationIds)
      : { data: [], error: null }
    if (organizations.error) throw organizations.error
    const timeline = personTimeline({ positions: positions.data, moves: moves.data, organizations: organizations.data })
    return NextResponse.json({
      person: { ...person, id: `contact:${person.id}`, contact_id: person.id },
      timeline,
      champion: championReading({ timeline, title: person.title, interactions: tenant.interactions, relationship: tenant.relationship }),
      revolving_door: revolvingDoorReading({ timeline }),
      ...tenant,
    })
  } catch (err) {
    console.error("[gov-people/[id] GET]", err)
    return NextResponse.json({ error: "Failed to load person" }, { status: 500 })
  }
})

/** Set the company's stance and notes on a person; the first save starts tracking them. */
export const PUT = withAuth(async ({ request, params, supabase, user, account }) => {
  try {
    const context = await loadContext({ params, supabase, account })
    if (context.response) return context.response
    const { contactId, govProfile } = context
    const { value, error: invalid } = cleanRelationship(await request.json().catch(() => ({})))
    if (invalid) return NextResponse.json({ error: invalid }, { status: 400 })
    const now = new Date().toISOString()
    const updated = await supabase.from("gov_person_relationships").update({ ...value, updated_at: now })
      .eq("gov_profile_id", govProfile.id).eq("contact_id", contactId).select("id,stance,notes,owner_user_id,created_at,updated_at")
      .maybeSingle()
    if (updated.error) throw updated.error
    if (updated.data) return NextResponse.json({ relationship: updated.data })
    const inserted = await supabase.from("gov_person_relationships")
      .insert({ ...value, gov_profile_id: govProfile.id, contact_id: contactId, owner_user_id: user.id, created_by: user.id })
      .select("id,stance,notes,owner_user_id,created_at,updated_at").single()
    if (inserted.error) throw inserted.error
    return NextResponse.json({ relationship: inserted.data }, { status: 201 })
  } catch (err) {
    console.error("[gov-people/[id] PUT]", err)
    return NextResponse.json({ error: "Failed to save relationship" }, { status: 500 })
  }
})

/** Stop tracking a person; logged interactions stay. */
export const DELETE = withAuth(async ({ params, supabase, account }) => {
  try {
    const context = await loadContext({ params, supabase, account })
    if (context.response) return context.response
    const { error } = await supabase.from("gov_person_relationships").delete()
      .eq("gov_profile_id", context.govProfile.id).eq("contact_id", context.contactId)
    if (error) throw error
    return NextResponse.json({ ok: true })
  } catch (err) {
    console.error("[gov-people/[id] DELETE]", err)
    return NextResponse.json({ error: "Failed to remove relationship" }, { status: 500 })
  }
})

/** Log one interaction with the person. */
export const POST = withAuth(async ({ request, params, supabase, user, account }) => {
  try {
    const context = await loadContext({ params, supabase, account })
    if (context.response) return context.response
    const { value, error: invalid } = cleanInteraction(await request.json().catch(() => ({})))
    if (invalid) return NextResponse.json({ error: invalid }, { status: 400 })
    const { data, error } = await supabase.from("gov_person_interactions")
      .insert({ ...value, gov_profile_id: context.govProfile.id, contact_id: context.contactId, created_by: user.id })
      .select("id,happened_on,kind,summary,gov_run_id,created_at").single()
    if (error) throw error
    return NextResponse.json({ interaction: data }, { status: 201 })
  } catch (err) {
    console.error("[gov-people/[id] POST]", err)
    return NextResponse.json({ error: "Failed to log interaction" }, { status: 500 })
  }
})
