import { sql } from '@/lib/db'
import { NextResponse } from 'next/server'

export async function POST(req: Request) {
    const { email, message, daysBefore } = await req.json()

    if (!email || daysBefore === undefined) {
        return NextResponse.json({ error: 'Missing fields' }, { status: 400 })
    }

    if (message && message.length > 50) {
        return NextResponse.json({ error: 'Note must be 50 characters or less' }, { status: 400 })
    }

    const sendAt = new Date()
    sendAt.setMonth(sendAt.getMonth() + 6)
    sendAt.setDate(sendAt.getDate() - daysBefore)

    await sql`
        INSERT INTO reminders (email, days_before, send_at, message)
        VALUES (${email}, ${daysBefore}, ${sendAt.toISOString()}, ${message ?? null})
    `

    return NextResponse.json({success: true, sendAt: sendAt.toISOString()})
}

