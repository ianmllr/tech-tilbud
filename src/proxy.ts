import { NextRequest, NextResponse } from 'next/server'
import { getClientIp } from '@/lib/clientIp'

const WINDOW_MS = 60_000
const MAX_REQUESTS = 60

const ipRequests = new Map<string, number[]>()
let lastCleanup = Date.now()

function cleanup(now: number) {
    if (now - lastCleanup < WINDOW_MS) return
    lastCleanup = now
    for (const [ip, timestamps] of ipRequests.entries()) {
        const recent = timestamps.filter(t => now - t < WINDOW_MS)
        if (recent.length === 0) {
            ipRequests.delete(ip)
        } else {
            ipRequests.set(ip, recent)
        }
    }
}

export function proxy(request: NextRequest) {
    const now = Date.now()
    cleanup(now)

    const ip = getClientIp(request.headers)

    const timestamps = (ipRequests.get(ip) ?? []).filter(t => now - t < WINDOW_MS)

    if (timestamps.length >= MAX_REQUESTS) {
        const retryAfter = Math.ceil((timestamps[0] + WINDOW_MS - now) / 1000)
        return new NextResponse(
            JSON.stringify({ error: 'Too Many Requests', retryAfter }),
            {
                status: 429,
                headers: {
                    'Content-Type': 'application/json',
                    'Retry-After': String(retryAfter),
                    'X-RateLimit-Limit': String(MAX_REQUESTS),
                    'X-RateLimit-Remaining': '0',
                    'X-RateLimit-Reset': String(Math.ceil((timestamps[0] + WINDOW_MS) / 1000)),
                },
            }
        )
    }

    timestamps.push(now)
    ipRequests.set(ip, timestamps)

    const remaining = MAX_REQUESTS - timestamps.length
    const response = NextResponse.next()
    response.headers.set('X-RateLimit-Limit', String(MAX_REQUESTS))
    response.headers.set('X-RateLimit-Remaining', String(remaining))
    response.headers.set('X-RateLimit-Reset', String(Math.ceil((timestamps[0] + WINDOW_MS) / 1000)))
    return response
}

export const config = {
    matcher: ['/((?!_next/static|_next/image|favicon.ico|images/).*)'],
}

