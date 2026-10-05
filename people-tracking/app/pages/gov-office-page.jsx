"use client"

import Link from "next/link"
import { useParams, useRouter } from "next/navigation"
import { useCallback, useEffect, useState } from "react"
import { ArrowRightLeft, ExternalLink, Sparkles } from "lucide-react"
import { useSession } from "@/components/SessionProviderClient"
import {
  BODY_CLASS,
  BTN_OUTLINE,
  BTN_PRIMARY,
  CARD_CLASS,
  CHIP,
  CHIP_TONES,
} from "@/components/ui/codex-surface/surface-theme"
import { GovForbiddenState } from "@/components/ui/gov/shared/gov-gate-cards"
import GovLoadingState from "@/components/ui/gov/shared/gov-loading-state"
import GovPageHeader from "@/components/ui/gov/shared/gov-page-header"
import GovPageShell from "@/components/ui/gov/shared/gov-page-shell"

const GROUP_LABEL = "text-[12.5px] text-cdx-ink-3"
const ROW = "rounded-[10px] border border-cdx-line bg-cdx-sunken p-3"
// Position role types grouped the way a capture team reads an office.
const ROLE_GROUPS = [
  ["leadership", "Leadership", ["acquisition_leader", "contracting_leader"]],
  ["program", "Program staff", ["program_manager", "deputy_program_manager", "program_staff", "technical_lead"]],
  ["contracting_officer", "Contracting officers", ["contracting_officer"]],
  ["contract_specialist", "Contract specialists", ["contract_specialist", "contract_administrator", "cor"]],
  ["other", "Other staff", ["other"]],
]
const STANCE_TONES = { champion: "green", engaged: "blue", target: "violet", detractor: "red", unknown: "neutral" }
const SIGNAL_LABELS = {
  new_in_post: "New in the post", solicitation_out: "Solicitation out", talks_to_industry: "Talks to industry",
}

// A person with more than one role here is listed once, under the first group they fit.
function roleGroup(person) {
  return ROLE_GROUPS.find(([, , roles]) => roles.some((role) => person.roles.includes(role)))?.[0] || "other"
}

function personHref(contactId) {
  return `/contractors/directory?${new URLSearchParams({ person: `contact:${contactId}` })}`
}


function PersonRow({ person }) {
  return (
    <div className={ROW}>
      <div className="flex flex-wrap items-center gap-2">
        <Link href={personHref(person.contact_id)} className="text-[13.5px] font-medium text-cdx-ink hover:text-cdx-blue">
          {person.name}
        </Link>
        {person.confirmed ? <span className={`${CHIP} ${CHIP_TONES.blue}`}>Confirmed current</span> : null}
        {person.stance ? (
          <span className={`${CHIP} ${CHIP_TONES[STANCE_TONES[person.stance]]}`}>{person.stance}</span>
        ) : null}
      </div>
      {person.title ? <p className="mt-0.5 text-[12.5px] text-cdx-ink-2">{person.title}</p> : null}
      <p className="mt-1 text-[12px] text-cdx-ink-3">{[person.standing_label, person.sources.join(", ")].filter(Boolean).join(" · ")}</p>
    </div>
  )
}

// Inbound moves that are not yet (or not only) an arrival say what the record states, as the alert e-mails do.
const INBOUND_VERBS = { announcement: " named to ", promotion: " promoted to ", acting: " acting as " }

function MoveRow({ move }) {
  const other = move.direction === "in" ? move.previous_organization
    : move.event_type === "departure" ? null : move.organization
  return (
    <div className={ROW}>
      <div className="flex items-start gap-2">
        <ArrowRightLeft className="mt-0.5 h-3.5 w-3.5 shrink-0 text-cdx-ink-4" />
        <div className="min-w-0">
          <p className="text-[13.5px] text-cdx-ink">
            <Link href={personHref(move.contact_id)} className="font-medium hover:text-cdx-blue">{move.name}</Link>
            {move.direction === "in" ? INBOUND_VERBS[move.event_type] || " joined as "
              : move.event_type === "departure" ? " left " : " left for "}
            {move.title}
          </p>
          {move.now ? <p className="mt-0.5 text-[12px] text-cdx-ink-2">Now: {move.now}</p> : null}
          {other ? (
            <p className="mt-0.5 text-[12px] text-cdx-ink-3">
              {move.direction === "in" ? "From " : "To "}
              <Link href={`/contractors/offices/${other.id}`} className="hover:text-cdx-blue">{other.name}</Link>
            </p>
          ) : null}
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className="text-[12px] tabular-nums text-cdx-ink-3">{move.date}</span>
            <span title={move.certainty_label} className={`${CHIP} ${
              { confirmed: CHIP_TONES.green, reported: CHIP_TONES.amber }[move.certainty] || CHIP_TONES.neutral}`}>
              {{ confirmed: "Confirmed", reported: "Reported" }[move.certainty] || "Inferred"}
            </span>
            {move.status === "review" ? <span className={`${CHIP} ${CHIP_TONES.amber}`}>Needs review</span> : null}
            {move.source_url ? (
              <a href={move.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[12px] text-cdx-ink-3 hover:text-cdx-blue">
                {move.source_label}
                <ExternalLink className="h-3 w-3" />
              </a>
            ) : <span className="text-[12px] text-cdx-ink-4">{move.source_label}</span>}
          </div>
        </div>
      </div>
    </div>
  )
}

function ChampionRow({ champion }) {
  return (
    <div className={`${ROW} border-cdx-violet-line`}>
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles className="h-3.5 w-3.5 text-cdx-violet" />
        <Link href={personHref(champion.contact_id)} className="text-[13.5px] font-medium text-cdx-ink hover:text-cdx-blue">
          {champion.name}
        </Link>
        <span className={`${CHIP} ${CHIP_TONES.violet}`}>Reading</span>
      </div>
      <p className="mt-1 text-[12px] text-cdx-ink-2">
        {champion.reading.documented_role.title} · {champion.reading.documented_role.standing_label}
      </p>
      <ul className="mt-1.5 space-y-0.5">
        {champion.reading.signals.map((signal) => (
          <li key={signal.kind} className="text-[12px] text-cdx-ink-3">
            <span className="text-cdx-ink-2">{SIGNAL_LABELS[signal.kind]}:</span> {signal.text}
            {signal.date ? ` (${signal.date}, ${signal.source_label})` : ""}
          </li>
        ))}
      </ul>
      <p className="mt-1 text-[12px] text-cdx-ink-4">Still to confirm: {champion.reading.to_confirm.join("; ")}.</p>
    </div>
  )
}

/** One government office: who is confirmed or was last observed here by role, two years of moves, potential champions. */
export default function GovOfficePage() {
  const { id } = useParams()
  const router = useRouter()
  const { user, isLoading: sessionLoading } = useSession()
  const [state, setState] = useState({ status: "loading", data: null, error: "" })
  const [showAuth, setShowAuth] = useState(false)
  const [saving, setSaving] = useState(false)
  const authRedirect = `/contractors/offices/${id}`

  const load = useCallback(async () => {
    const response = await fetch(`/api/gov-organizations/${id}`).catch(() => null)
    if (response?.status === 401) return setShowAuth(true)
    if (response?.status === 403) return setState({ status: "forbidden", data: null, error: "" })
    const json = await response?.json().catch(() => ({}))
    if (!response?.ok) return setState({ status: "failed", data: null, error: json?.error || "Could not load this office." })
    setState({ status: "ready", data: json, error: "" })
  }, [id])

  useEffect(() => {
    if (sessionLoading) return
    if (!user?.id) setShowAuth(true)
    else load()
  }, [load, sessionLoading, user?.id])

  const toggleTracking = async () => {
    setSaving(true)
    const response = await fetch(`/api/gov-organizations/${id}`, { method: state.data.tracked ? "DELETE" : "PUT" }).catch(() => null)
    setSaving(false)
    if (response?.ok) load()
  }

  if (sessionLoading || (state.status === "loading" && !showAuth)) {
    return <GovLoadingState message="Loading office…" theme="codex" />
  }

  if (state.status === "forbidden") {
    return (
      <GovPageShell maxWidth="lg" authOpen={showAuth} onAuthClose={() => setShowAuth(false)} authRedirect={authRedirect}>
        <GovForbiddenState
          title="Office unavailable"
          description="Your account is not linked to a government contractor profile yet."
          actionLabel="Set up company profile"
          onAction={() => router.push("/contractors/admin#onboarding")}
        />
      </GovPageShell>
    )
  }

  const data = state.data
  const office = data?.office

  return (
    <GovPageShell maxWidth="7xl" authOpen={showAuth} onAuthClose={() => setShowAuth(false)} authRedirect={authRedirect}>
      <GovPageHeader
        label={office?.parent?.name || "Government office"}
        title={office?.name || "Office"}
        description="Who is confirmed in this office or was last observed here, how people moved in and out over two years, and where a potential champion may be. Only an official statement makes someone confirmed current; every other date says when they were last observed."
        actions={office ? (
          <div className="flex items-center gap-2">
            {office.code ? <span className={`${CHIP} h-fit`}>{office.code}</span> : null}
            <button type="button" disabled={saving} onClick={toggleTracking} className={`${data.tracked ? BTN_OUTLINE : BTN_PRIMARY} h-9 px-3.5`}>
              {data.tracked ? "Stop tracking" : "Track office"}
            </button>
          </div>
        ) : null}
      />

      {state.status === "failed" ? (
        <div className={`${CARD_CLASS} mt-8 px-5 py-5`}>
          <p className={BODY_CLASS}>{state.error}</p>
          <button type="button" className={`${BTN_OUTLINE} mt-4 h-9 px-3.5`} onClick={load}>Try again</button>
        </div>
      ) : null}

      {data ? (
        <div className="mt-7 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <section className={`${CARD_CLASS} self-start p-6`} aria-label="People by role">
            <p className={GROUP_LABEL}>People here ({data.people.length})</p>
            {data.people.length ? ROLE_GROUPS.map(([role, label]) => {
              const people = data.people.filter((person) => roleGroup(person) === role)
              return people.length ? (
                <div key={role} className="mt-5">
                  <p className="text-[13px] font-medium text-cdx-ink">{label} · {people.length}</p>
                  <div className="mt-2 space-y-2">
                    {people.map((person) => <PersonRow key={person.contact_id} person={person} />)}
                  </div>
                </div>
              ) : null
            }) : <p className="mt-3 text-[13.5px] text-cdx-ink-3">No one has been confirmed or observed here in the last two years.</p>}
            {data.not_recent?.length ? (
              <details className="mt-6">
                <summary className="cursor-pointer text-[13px] text-cdx-ink-3">Not observed in the last two years ({data.not_recent.length})</summary>
                <div className="mt-2 space-y-2">
                  {data.not_recent.map((person) => <PersonRow key={person.contact_id} person={person} />)}
                </div>
              </details>
            ) : null}
          </section>

          <div className="space-y-5 self-start">
            <section className={`${CARD_CLASS} p-6`} aria-label="Potential champions">
              <p className={GROUP_LABEL}>Potential champions</p>
              <p className="mt-1 text-[12px] text-cdx-ink-4">
                Readings, not facts: a leadership or requirements title with two kinds of dated signal, such as a new
                post and a meeting. A title alone establishes neither budget authority nor willingness to champion you.
              </p>
              <div className="mt-3 space-y-2">
                {data.champions.length
                  ? data.champions.map((champion) => <ChampionRow key={champion.contact_id} champion={champion} />)
                  : <p className="text-[13.5px] text-cdx-ink-3">No candidate meets the rule yet.</p>}
              </div>
            </section>

            <section className={`${CARD_CLASS} p-6`} aria-label="Moves">
              <p className={GROUP_LABEL}>Moves in the last 24 months ({data.moves.length})</p>
              <div className="mt-3 space-y-2">
                {data.moves.length
                  ? data.moves.map((move) => <MoveRow key={move.id} move={move} />)
                  : <p className="text-[13.5px] text-cdx-ink-3">No moves recorded.</p>}
              </div>
            </section>
          </div>
        </div>
      ) : null}
    </GovPageShell>
  )
}
