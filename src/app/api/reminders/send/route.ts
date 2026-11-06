import { sql } from '@/lib/db'
import { Resend } from 'resend'
import { NextResponse } from 'next/server'
import { timingSafeEqual } from 'node:crypto'

function isAuthorized(authHeader: string | null, secret: string): boolean {
    if (!authHeader) return false
    const expected = Buffer.from(`Bearer ${secret}`)
    const actual = Buffer.from(authHeader)
    if (actual.length !== expected.length) return false
    return timingSafeEqual(actual, expected)
}

export async function POST(req: Request) {
    const secret = process.env.QSTASH_CURRENT_SIGNING_KEY
    if (!secret) {
        console.error('reminders/send: QSTASH_CURRENT_SIGNING_KEY is not set')
        return NextResponse.json({error: 'Server misconfigured'}, {status: 500})
    }

    if (!isAuthorized(req.headers.get('authorization'), secret)) {
        return NextResponse.json({error: 'Unauthorized'}, {status: 401})
    }

    const resend = new Resend(process.env.RESEND_API_KEY!)

    const due = await sql`
        SELECT id, email
        FROM reminders
        WHERE sent = false AND send_at <= NOW()
    `

    let sentCount = 0
    let failedCount = 0

    for (const row of due) {
        try {
            const { error } = await resend.emails.send({
                from: 'reminder@tech-tilbud.com',
                to: row.email,
                subject: `Påmindelse: din 6 måneders binding på dit abonnement er ved at udløbe`,
                html: `
                <h2>Bindingen på dit abonnement er ved at udløbe.</h2>
                <p>For knap 6 måneder siden bad du om at få en påmindelse når bindingen på dit abonnement udløber.</p>
                <p>Det er nu.</p>
                <p>Det betyder at du endnu en gang kan spare penge på nye teknologi-produkter.</p>
                <p>Tjek om der er et tilbud der passer til dig på tech-tilbud.dk!</p>
            `,
            })

            if (error) {
                // log only the error name: resend messages can echo the recipient address
                console.error(`reminders/send: failed to send reminder ${row.id}: ${error.name}`)
                failedCount++
                continue
            }

            await sql`DELETE FROM reminders WHERE id = ${row.id}`
            sentCount++
        } catch (err) {
            const name = err instanceof Error ? err.name : typeof err
            console.error(`reminders/send: error processing reminder ${row.id}: ${name}`)
            failedCount++
        }
    }

    return NextResponse.json({success: failedCount === 0, sent: sentCount, failed: failedCount})
}
