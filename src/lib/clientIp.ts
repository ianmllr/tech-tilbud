// on vercel x-real-ip is set by the platform and can't be spoofed by the client,
// so prefer it over the first x-forwarded-for entry
export function getClientIp(headers: Headers): string {
    const realIp = headers.get('x-real-ip')?.trim()
    if (realIp) return realIp

    const forwarded = headers.get('x-forwarded-for')?.split(',')[0]?.trim()
    if (forwarded) return forwarded

    return 'unknown'
}
