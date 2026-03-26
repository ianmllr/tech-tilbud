type PaginationControlsProps = {
    page: number
    totalPages: number
    onPrev: () => void
    onNext: () => void
}

export default function PaginationControls({ page, totalPages, onPrev, onNext }: PaginationControlsProps) {
    return (
        <div className="flex gap-2 justify-center mt-5">
            <button
                onClick={onPrev}
                disabled={page === 1}
                className="px-3 py-1.5 rounded-md border border-[#334155] hover:border-[#4a90b8] hover:text-[#cdd6e0] transition-colors"
            >
                Forrige
            </button>
            <span className="flex items-center gap-3 text-[#cdd6e0] text-sm">Side {page} af {totalPages}</span>
            <button
                onClick={onNext}
                disabled={page === totalPages}
                className="px-3 py-1.5 rounded-md border border-[#334155] hover:border-[#4a90b8] hover:text-[#cdd6e0] transition-colors"
            >
                Næste
            </button>
        </div>
    )
}

