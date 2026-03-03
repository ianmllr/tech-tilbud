'use client'
import { useState } from 'react'

interface TooltipProps {
    text: string
}

export default function Tooltip({ text }: TooltipProps) {
    const [visible, setVisible] = useState(false)

    return (
        <span className="relative inline-flex">
        <button
            onClick={() => setVisible(v => !v)}
    className="inline-flex items-center justify-center w-4 h-4 rounded-full border border-[#7d8fa0] text-[#7d8fa0] text-[9px] cursor-pointer select-none leading-none"
    aria-label="Info"
        >
        i
        </button>
    {visible && (
        <span className="absolute bottom-[120%] left-1/2 -translate-x-1/2 bg-[#2a3340] border border-[#334155] text-[#cdd6e0] text-[11px] px-2 py-1 rounded-md whitespace-nowrap z-10 pointer-events-none">
            {text}
            <span className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-[#2a3340]" />
        </span>
    )}
    </span>
)
}
