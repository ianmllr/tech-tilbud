// src/app/api/reminders/send/route.ts
import { sql } from '@/lib/db'
import { Resend } from 'resend'
import { NextResponse } from 'next/server'

export async function POST(req: Request) {
    const authHeader = req.headers.get('authorization')
    if (authHeader !== `Bearer ${process.env.QSTASH_CURRENT_SIGNING_KEY}`) {
        return NextResponse.json({error: 'Unauthorized'}, {status: 401})
    }

    const resend = new Resend(process.env.RESEND_API_KEY!)

    const due = await sql`
        SELECT id, email, offer_title, offer_url
        FROM reminders
        WHERE sent = false AND send_at <= NOW()
    `

    let sentCount = 0

    for (const row of due) {
        await resend.emails.send({
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

        await sql`UPDATE reminders
                  SET sent = true
                  WHERE id = ${row.id}`
        sentCount++
    }

    return NextResponse.json({success: true, sent: sentCount})
}
