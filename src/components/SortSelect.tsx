import type { SortOrder } from '@/types/offer'

interface SortSelectProps {
    value: SortOrder
    onChange: (value: SortOrder) => void
}

const SORT_OPTIONS: { value: SortOrder; label: string }[] = [
    { value: 'asc', label: 'Abonnementspris: lav til høj' },
    { value: 'desc', label: 'Abonnementspris: høj til lav' },
    { value: 'saved_desc', label: 'Reelt sparet: høj til lav' },
    { value: 'saved_asc', label: 'Reelt sparet: lav til høj' },
    { value: 'name_asc', label: 'Navn: A → Å' },
    { value: 'name_desc', label: 'Navn: Å → A' },
]

export default function SortSelect({ value, onChange }: SortSelectProps) {
    return (
        <select
            value={value}
            onChange={e => onChange(e.target.value as SortOrder)}
            className="px-4 py-2 rounded-md border border-[#334155] bg-[#2a3340] text-[#cdd6e0] text-sm"
        >
            {SORT_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
            ))}
        </select>
    )
}
