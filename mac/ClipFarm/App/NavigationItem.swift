/// The six top-level pages (spec → Frontend pages; nav skeleton per plan N0).
enum NavigationItem: String, CaseIterable, Identifiable {
    case library
    case project
    case script
    case attempts
    case brief
    case settings

    var id: Self { self }

    /// User-facing label — deliberately separate from `rawValue`, which stays a
    /// stable identifier (localization at Track 2 never touches case names).
    var label: String {
        switch self {
        case .library: "Library"
        case .project: "Project"
        case .script: "Script"
        case .attempts: "Attempts"
        case .brief: "Brief"
        case .settings: "Settings"
        }
    }

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
