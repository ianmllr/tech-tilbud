'use client'

import React, { useState } from 'react'
import Tooltip from "@/components/Tooltip";

export default function ReminderForm() {
    const [email, setEmail] = useState('')
    const [message, setMessage] = useState('')
    const [sendEarlier, setSendEarlier] = useState(false)
    const [daysCount, setDaysCount] = useState(1)
    const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
    const [errorMsg, setErrorMsg] = useState('')
    const [gdprAccepted, setGdprAccepted] = useState(false)

    const dayOptions = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

    // Changed React.FormEvent to React.SyntheticEvent<HTMLFormElement> to fix deprecation warning
    async function handleSubmit(e: React.SyntheticEvent<HTMLFormElement>) {
        e.preventDefault()

        // prevent submission if GDPR is not accepted
        if (!gdprAccepted) {
            setErrorMsg('Du skal acceptere vilkårene for at fortsætte.')
            setStatus('error')
            return
        }

        setStatus('loading')
        setErrorMsg('')

        const daysBefore = sendEarlier ? daysCount : 0

        try {
            await fetch('/api/reminders', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, message, daysBefore }),
            })

            setStatus('success')
            setEmail('')
            setMessage('')
            setSendEarlier(false)
            setDaysCount(1)
            setGdprAccepted(false)
        } catch (err: unknown) {
            setStatus('error')
            setErrorMsg(err instanceof Error ? err.message : 'Ukendt fejl')
        }
    }

    if (status === 'success') {
        return (
            <div className="mt-2 p-3 rounded-lg bg-[#1e3a2a] border border-[#4caf82] text-[#4caf82] text-sm">
                Påmindelse sat. Vi sender dig en e-mail om ca. 6 måneder og minder dig om at bindingen på dit abonnement er ved at udløbe.
            </div>
        )
    }

    return (
        <form onSubmit={handleSubmit} className="mt-2 flex flex-col gap-2 p-3 rounded-lg border border-[#334155] bg-[#232b36]">
            <p className="text-xs text-[#7d8fa0] m-0">
                Få en e-mail om 6 måneder, så du kan tjekke om dit abonnement stadig er den bedste pris.
            </p>

            <input
                type="email"
                required
                placeholder="Din e-mail"
                value={email}
                onChange={e => setEmail(e.target.value)}
                className="px-3 py-2 rounded-md border border-[#334155] bg-[#2a3340] text-[#cdd6e0] text-sm placeholder-[#7d8fa0] focus:outline-none focus:border-[#4a90b8]"
            />

            <textarea
                placeholder="Personlig note (valgfrit)"
                value={message}
                onChange={e => setMessage(e.target.value)}
                maxLength={50}
                rows={2}
                className="..."
            />
            <p className="text-xs text-[#7d8fa0] text-right m-0">{message.length}/50</p>


            <div className="flex flex-col gap-2 my-1">
                <div className="flex items-center gap-2">
                    <input
                        type="checkbox"
                        id="sendEarlier"
                        checked={sendEarlier}
                        onChange={(e) => setSendEarlier(e.target.checked)}
                        className="rounded border-[#334155] bg-[#2a3340] cursor-pointer"
                    />
                    <label htmlFor="sendEarlier" className="text-xs text-[#cdd6e0] cursor-pointer select-none">
                        Send nogle dage før?
                    </label>
                </div>

                {sendEarlier && (
                    <select
                        value={daysCount}
                        onChange={(e) => setDaysCount(Number(e.target.value))}
                        className="px-3 py-2 rounded-md border border-[#334155] bg-[#2a3340] text-[#cdd6e0] text-sm focus:outline-none focus:border-[#4a90b8]"
                    >
                        {dayOptions.map((day) => (
                            <option key={day} value={day}>
                                {day} dag{day > 1 ? 'e' : ''} før
                            </option>
                        ))}
                    </select>
                )}
            </div>

            {status === 'error' && (
                <p className="text-xs text-[#e05555] m-0">{errorMsg}</p>
            )}

            <div className="flex items-center gap-2">
                <input
                    type="checkbox"
                    id="gdpr"
                    required
                    checked={gdprAccepted}
                    onChange={(e) => setGdprAccepted(e.target.checked)}
                    className="rounded border-[#334155] bg-[#2a3340] cursor-pointer"
                />
                <label htmlFor="gdpr" className="text-xs text-[#cdd6e0] cursor-pointer select-none">
                    Jeg accepterer at min emailadresse og min IP bliver gemt
                    midlertidigt i forbindelse med denne påmindelse.
                </label>
                <Tooltip text="GDPR: Vi gemmer din email i 6 måneder så vi ved hvem vi skal sende beskeden til. Vi gemmer din IP så man ikke kan bede om mails sendt til mere end én emailadresse.. Din data bliver naturligvis slettet bagefter, og bliver aldrig solgt eller delt med nogen." />
            </div>

            <div className="flex gap-2 justify-center">
                <button
                    type="submit"
                    disabled={status === 'loading' || !gdprAccepted}
                    className="px-4 py-2 rounded-lg bg-[#4a90b8] text-white text-[13px] font-bold hover:bg-[#3a7aa0] transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                >
                    {status === 'loading' ? 'Sender...' : 'Sæt påmindelse'}
                </button>
            </div>

        </form>
    )
}
