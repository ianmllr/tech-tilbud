import { sql } from '@/lib/db'
import { getClientIp } from '@/lib/clientIp'
import { NextResponse } from 'next/server'

const MAX_EMAIL_LENGTH = 254
const MAX_MESSAGE_LENGTH = 50
const MIN_DAYS_BEFORE = 0
const MAX_DAYS_BEFORE = 10
// something@something.tld, no whitespace, angle brackets, commas or extra @
const EMAIL_RE = /^[^\s@<>,]+@[^\s@<>,]+\.[^\s@<>,]+$/

function badRequest(error: string) {
    return NextResponse.json({ error }, { status: 400 })
}

export async function POST(req: Request) {
    const contentType = req.headers.get('content-type') ?? ''
    if (contentType.split(';')[0].trim().toLowerCase() !== 'application/json') {
        return NextResponse.json({ error: 'Content-Type skal være application/json.' }, { status: 415 })
    }

    let body: unknown
    try {
        body = await req.json()
    } catch {
        return badRequest('Ugyldig JSON.')
    }

    if (typeof body !== 'object' || body === null || Array.isArray(body)) {
        return badRequest('Ugyldig forespørgsel.')
    }

    const { email: rawEmail, message: rawMessage, daysBefore } = body as Record<string, unknown>

    if (typeof rawEmail !== 'string') {
        return badRequest('Ugyldig e-mailadresse.')
    }
    const email = rawEmail.trim().toLowerCase()
    if (email.length === 0 || email.length > MAX_EMAIL_LENGTH || !EMAIL_RE.test(email)) {
        return badRequest('Ugyldig e-mailadresse.')
    }

    if (
        typeof daysBefore !== 'number' ||
        !Number.isInteger(daysBefore) ||
        daysBefore < MIN_DAYS_BEFORE ||
        daysBefore > MAX_DAYS_BEFORE
    ) {
        return badRequest(`Antal dage skal være et helt tal mellem ${MIN_DAYS_BEFORE} og ${MAX_DAYS_BEFORE}.`)
    }

    let message: string | null = null
    if (rawMessage !== undefined && rawMessage !== null) {
        if (typeof rawMessage !== 'string' || rawMessage.length > MAX_MESSAGE_LENGTH) {
            return badRequest(`Noten må højst være ${MAX_MESSAGE_LENGTH} tegn.`)
        }
        message = rawMessage
    }

    const ip = getClientIp(req.headers)

    const existingForIp = await sql`
        SELECT id FROM reminders WHERE ip = ${ip} AND sent = false LIMIT 1
    `
    if (existingForIp.length > 0) {
        return NextResponse.json({ error: 'Der er allerede sat en påmindelse fra denne IP-adresse.' }, { status: 429 })
    }

    const existingForEmail = await sql`
        SELECT id FROM reminders WHERE email = ${email} AND sent = false LIMIT 1
    `
    if (existingForEmail.length > 0) {
        return NextResponse.json({ error: 'Der er allerede sat en påmindelse for denne e-mailadresse.' }, { status: 409 })
    }

    const sendAt = new Date()
    sendAt.setMonth(sendAt.getMonth() + 6)
    sendAt.setDate(sendAt.getDate() - daysBefore)

    await sql`
        INSERT INTO reminders (email, days_before, send_at, message, ip)
        VALUES (${email}, ${daysBefore}, ${sendAt.toISOString()}, ${message}, ${ip})
    `

    return NextResponse.json({ success: true, sendAt: sendAt.toISOString() })
}
