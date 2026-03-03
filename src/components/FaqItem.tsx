'use client'

import { useState } from 'react'

interface FaqItemProps {
    question: string
    answer: string
}

export default function FaqItem({ question, answer }: FaqItemProps) {
    const [open, setOpen] = useState(false)

    return (
        <div className="w-full max-w-prose border-b border-[#334155]">
            <button
                onClick={() => setOpen(!open)}
                className="w-full text-left py-4 flex justify-between items-center font-semibold text-[#cdd6e0]"
            >
                {question}
                <span className="text-[#7d8fa0]">{open ? '▲' : '▼'}</span>
            </button>
            {open && (
                <p className="pb-4 text-[#7d8fa0]">
                    {answer}
                </p>
            )}
        </div>
    )
}
