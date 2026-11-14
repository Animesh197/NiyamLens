const plannedCapabilities = [
  'Track a small, verified set of RBI directions for NBFCs',
  'Compare historical and current versions with deterministic, cited diffs',
  'Extract candidate obligations quoted directly from the source text',
  'Draft proposed follow-up actions for a human reviewer to approve or reject',
]

function App() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex max-w-4xl items-center justify-between gap-4 px-4 py-4 sm:px-6">
          <span className="text-lg font-semibold tracking-tight">NiyamLens</span>
          <span className="rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
            Prototype · in development
          </span>
        </div>
      </header>

      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-12 sm:px-6 sm:py-16">
        <h1 className="text-3xl font-semibold tracking-tight text-balance sm:text-4xl">
          Regulatory change and compliance intelligence for Indian NBFCs
        </h1>
        <p className="mt-4 max-w-2xl text-base leading-relaxed text-slate-600 dark:text-slate-300">
          NiyamLens is a human-reviewed research assistant that helps compliance analysts follow
          RBI directions, see what changed between versions, and trace every finding back to its
          source document, section and page.
        </p>

        <section
          aria-labelledby="planned-heading"
          className="mt-10 rounded-lg border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900"
        >
          <h2 id="planned-heading" className="text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Planned capabilities
          </h2>
          <ul className="mt-4 space-y-3">
            {plannedCapabilities.map((item) => (
              <li key={item} className="flex gap-3 text-slate-700 dark:text-slate-200">
                <span aria-hidden="true" className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
          <p className="mt-6 border-t border-slate-200 pt-4 text-sm text-slate-500 dark:border-slate-800 dark:text-slate-400">
            Status: project foundation only. Document ingestion, version comparison and assessment
            features are not built yet, so no regulatory data is shown here.
          </p>
        </section>
      </main>

      <footer className="border-t border-slate-200 dark:border-slate-800">
        <p className="mx-auto max-w-4xl px-4 py-6 text-xs leading-relaxed text-slate-500 sm:px-6 dark:text-slate-400">
          NiyamLens is an information and decision-support prototype. It is not legal advice and
          does not certify regulatory compliance. All interpretations and proposed actions require
          review by a qualified person.
        </p>
      </footer>
    </div>
  )
}

export default App
