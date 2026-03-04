import ReminderForm from "@/components/ReminderForm"
import Header from "@/components/Header"

export default function Reminder() {
    return (
        <>
            <Header />
            <main className="flex flex-col items-center px-4 text-center pt-16 pb-16">
                <h1 className="text-3xl font-bold mb-4 justify-center-safe">Sæt en påmindelse</h1>
                <p className="text-base max-w-prose mb-12 ">
                    Sæt en påmindelse om at det snart er tid til at skifte abonnement og få et nyt tech-produkt.
                    Vi sender dig en e-mail om ca. 6 måneder og minder dig om at bindingen på dit abonnement er ved at udløbe.
                </p>

                <ReminderForm />
            </main>
        </>
    )
}