/// The six top-level pages (spec → Frontend pages; nav skeleton per plan N0).
enum NavigationItem: String, CaseIterable, Identifiable {
    case library = "Library"
    case project = "Project"
    case script = "Script"
    case attempts = "Attempts"
    case brief = "Brief"
    case settings = "Settings"

    var id: Self { self }

    var systemImage: String {
        switch self {
        case .library: "books.vertical"
        case .project: "square.grid.2x2"
        case .script: "list.number"
        case .attempts: "film.stack"
        case .brief: "doc.text"
        case .settings: "gearshape"
        }
    }
}
